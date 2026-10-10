use crate::{
    auth::{self, Auth},
    downloads,
    model::*,
    search::{self, SearchRequest},
    state::{App, public_error},
};
use axum::{
    Json, Router,
    body::Body,
    extract::{DefaultBodyLimit, Path, Query, State},
    http::{HeaderMap, StatusCode, header},
    response::{
        IntoResponse, Response,
        sse::{Event, KeepAlive, Sse},
    },
    routing::{get, post, put},
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{convert::Infallible, time::Duration};
use tower_http::services::{ServeDir, ServeFile};
pub struct ApiError(anyhow::Error);
impl<E: Into<anyhow::Error>> From<E> for ApiError {
    fn from(e: E) -> Self {
        Self(e.into())
    }
}
impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail":public_error(&self.0)})),
        )
            .into_response()
    }
}
type Result<T> = std::result::Result<T, ApiError>;
fn require(valid: bool, message: &str) -> Result<()> {
    if valid {
        Ok(())
    } else {
        Err(anyhow::anyhow!(message.to_owned()).into())
    }
}
pub fn router(app: App, auth: Auth, frontend: &std::path::Path) -> Router {
    let auth_routes = Router::new()
        .route("/api/auth/status", get(auth::status))
        .route("/api/auth/login", post(auth::login))
        .route("/api/auth/logout", post(auth::logout))
        .with_state(auth.clone());
    Router::new().route("/api/health",get(||async{Json(json!({"ok":true,"backend":"rust","version":env!("CARGO_PKG_VERSION")}))}))
        .route("/api/meta",get(||async{Json(json!({"sources":SOURCES.iter().map(|(id,label)|json!({"id":id,"label":label})).collect::<Vec<_>>()}))}))
        .route("/api/config",get(config).put(update_config))
        .route("/api/search/jobs",post(start_search))
        .route("/api/search/jobs/{id}",get(snapshot).delete(cancel_search))
        .route("/api/search/jobs/{id}/events",get(events))
        .route("/api/tracks/{id}/playback",post(playback))
        .route("/api/tracks/{id}/lyrics",get(lyrics))
        .route("/api/tracks/{id}/versions",post(versions))
        .route("/api/stream/{id}",get(stream))
        .route("/api/favorites",get(favorites))
        .route("/api/favorites/{id}",put(add_favorite).delete(remove_favorite))
        .route("/api/playlist",get(playlist).put(save_playlist))
        .route("/api/playlist/import",post(import_playlist))
        .route("/api/downloads",get(download_jobs).post(start_downloads))
        .route("/api/downloads/{id}/{action}",post(control_download))
        .route("/api/files",get(files))
        .route("/api/files/content",get(file_content))
        .with_state(app).merge(auth_routes)
        .fallback_service(ServeDir::new(frontend).not_found_service(ServeFile::new(frontend.join("index.html"))))
        .layer(DefaultBodyLimit::max(1024*1024)).layer(axum::middleware::from_fn_with_state(auth,auth::gate))
}
async fn config(State(app): State<App>) -> Json<Config> {
    Json(app.0.config.read().await.clone())
}
async fn versions(State(app): State<App>, Path(id): Path<String>) -> Result<Json<Track>> {
    let mut record = app.record(&id).await?;
    if !record.track.qualities.is_empty() {
        return Ok(Json(record.track));
    }
    let config = app.0.config.read().await.clone();
    let candidates = match record.track.source.as_str() {
        "qianqian" => vec![
            Quality::new("3000", "flac", None, true),
            Quality::new("320", "mp3", Some(320), false),
        ],
        "kuwo" => vec![
            Quality::new("2000kflac", "flac", None, true),
            Quality::new("320kmp3", "mp3", Some(320), false),
        ],
        "qq" => vec![Quality::new("M500", "mp3", Some(128), false)],
        "migu" => vec![Quality::new("PQ", "mp3", Some(320), false)],
        _ => vec![Quality::new("exhigh", "mp3", Some(320), false)],
    };
    record.track.qualities = candidates.clone();
    let mut verified = vec![];
    for mut candidate in candidates {
        if let Ok(asset) = app.asset(&record, Some(&candidate.id), &config).await {
            candidate.codec = asset.codec;
            candidate.bitrate = asset.bitrate;
            candidate.evidence = "probe".into();
            candidate.availability = "verified".into();
            candidate.label = Quality::new(
                &candidate.id,
                &candidate.codec,
                candidate.bitrate,
                candidate.lossless,
            )
            .label;
            verified.push(candidate)
        }
    }
    require(
        !verified.is_empty(),
        "该音源未提供有权限的完整资源，请检查 Cookie 或选择其他音源",
    )?;
    record.track.qualities = verified;
    app.remember(record.clone()).await?;
    Ok(Json(record.track))
}
async fn update_config(State(app): State<App>, Json(config): Json<Config>) -> Result<Json<Value>> {
    config.validate()?;
    app.0.store.put("config", "main", &config).await?;
    let restart = config.download_concurrency != app.0.config.read().await.download_concurrency;
    *app.0.config.write().await = config;
    app.0.assets.lock().await.clear();
    app.0.metadata.lock().await.clear();
    Ok(Json(json!({"saved":true,"restart_required":restart})))
}
async fn start_search(
    State(app): State<App>,
    Json(request): Json<SearchRequest>,
) -> Result<Json<Value>> {
    Ok(Json(json!({"id":search::start(app,request).await?})))
}
async fn job(app: &App, id: &str) -> Result<std::sync::Arc<search::SearchJob>> {
    app.0
        .jobs
        .lock()
        .await
        .get(id)
        .cloned()
        .ok_or_else(|| anyhow::anyhow!("搜索任务已过期，请重新搜索").into())
}
async fn snapshot(State(app): State<App>, Path(id): Path<String>) -> Result<Json<Value>> {
    Ok(Json(serde_json::to_value(
        &*job(&app, &id).await?.snapshot.lock().await,
    )?))
}
async fn cancel_search(State(app): State<App>, Path(id): Path<String>) -> Result<Json<Value>> {
    job(&app, &id).await?.cancel.cancel();
    Ok(Json(json!({"cancelled":true})))
}
#[derive(Deserialize)]
struct EventQuery {
    after: Option<u64>,
}
async fn events(
    State(app): State<App>,
    Path(id): Path<String>,
    Query(query): Query<EventQuery>,
    headers: HeaderMap,
) -> Result<Sse<impl futures_util::Stream<Item = std::result::Result<Event, Infallible>>>> {
    let job = job(&app, &id).await?;
    let mut rx = job.tx.subscribe();
    let mut sequence = query.after.unwrap_or(0).max(
        headers
            .get("last-event-id")
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.parse().ok())
            .unwrap_or(0),
    );
    let stream = async_stream::stream! {
        loop{
            let replay:Vec<_>=job.events.lock().await.iter().filter(|e|e.sequence>sequence).cloned().collect();
            for event in replay{sequence=event.sequence;let done=event.kind=="done";yield Ok(Event::default().id(sequence.to_string()).event(&event.kind).data(event.data.to_string()));if done{return}}
            match rx.recv().await{Ok(event) if event.sequence>sequence=>{sequence=event.sequence;let done=event.kind=="done";yield Ok(Event::default().id(sequence.to_string()).event(&event.kind).data(event.data.to_string()));if done{return}},Ok(_)=>(),Err(tokio::sync::broadcast::error::RecvError::Lagged(_))=>continue,Err(_)=>return}
        }
    };
    Ok(Sse::new(stream).keep_alive(KeepAlive::new().interval(Duration::from_secs(15))))
}
async fn playback(State(app): State<App>, Path(id): Path<String>) -> Result<Json<Track>> {
    let record = app.record(&id).await?;
    let config = app.0.config.read().await.clone();
    Ok(Json(app.playable(record, &config).await))
}
async fn lyrics(State(app): State<App>, Path(id): Path<String>) -> Result<Json<Value>> {
    if let Some(text) = app.0.store.get::<String>("lyrics", &id).await? {
        return Ok(Json(json!({"text":text})));
    }
    let record = app.record(&id).await?;
    let config = app.0.config.read().await.clone();
    let source = app.source(&record.track.source, &config)?;
    let text = tokio::time::timeout(Duration::from_secs(8), source.lyrics(&record))
        .await
        .map_err(|_| anyhow::anyhow!("歌词请求超时"))??;
    if !text.is_empty() {
        app.0.store.put("lyrics", &id, &text).await?;
    }
    Ok(Json(json!({"text":text})))
}
async fn stream(
    State(app): State<App>,
    Path(id): Path<String>,
    headers: HeaderMap,
) -> Result<Response> {
    let record = app.record(&id).await?;
    let config = app.0.config.read().await.clone();
    let asset = app.asset(&record, None, &config).await?;
    let source = app.source(&record.track.source, &config)?;
    let mut request = source
        .client
        .get(&asset.url)
        .header("Referer", &asset.referer)
        .header("Accept-Encoding", "identity")
        .timeout(Duration::from_secs(3600));
    if let Some(range) = headers.get(header::RANGE) {
        let text = range.to_str()?;
        require(valid_range(text), "播放范围格式无效")?;
        request = request.header(header::RANGE, range.clone())
    }
    let upstream = request.send().await?;
    let status = upstream.status();
    require(
        [200, 206, 416].contains(&status.as_u16()),
        "播放资源已失效，请重新试听",
    )?;
    let mut response = Response::builder().status(status.as_u16());
    for name in [
        header::CONTENT_TYPE,
        header::CONTENT_LENGTH,
        header::CONTENT_RANGE,
        header::ACCEPT_RANGES,
    ] {
        if let Some(value) = upstream.headers().get(&name) {
            response = response.header(name, value);
        }
    }
    Ok(response
        .header(header::CACHE_CONTROL, "private, no-store")
        .body(Body::from_stream(upstream.bytes_stream()))?)
}
fn valid_range(range: &str) -> bool {
    let Some(range) = range.strip_prefix("bytes=") else {
        return false;
    };
    let Some((start, end)) = range.split_once('-') else {
        return false;
    };
    (!start.is_empty() || !end.is_empty())
        && (start.is_empty() || start.parse::<u64>().is_ok())
        && (end.is_empty() || end.parse::<u64>().is_ok())
        && match (start.parse::<u64>(), end.parse::<u64>()) {
            (Ok(s), Ok(e)) => s <= e,
            _ => true,
        }
}
async fn favorites(State(app): State<App>) -> Result<Json<Vec<Track>>> {
    Ok(Json(app.0.store.list("favorite", 10000).await?))
}
async fn add_favorite(State(app): State<App>, Path(id): Path<String>) -> Result<Json<Value>> {
    let record = app.record(&id).await?;
    app.0.store.put("favorite", &id, &record.track).await?;
    Ok(Json(json!({"saved":true})))
}
async fn remove_favorite(State(app): State<App>, Path(id): Path<String>) -> Result<Json<Value>> {
    app.0.store.delete("favorite", &id).await?;
    Ok(Json(json!({"removed":true})))
}
async fn playlist(State(app): State<App>) -> Result<Json<Vec<Track>>> {
    let ids = app
        .0
        .store
        .get::<Vec<String>>("playlist", "main")
        .await?
        .unwrap_or_default();
    let mut tracks = vec![];
    for id in ids {
        if let Ok(record) = app.record(&id).await {
            tracks.push(record.track)
        }
    }
    Ok(Json(tracks))
}
async fn save_playlist(
    State(app): State<App>,
    Json(ids): Json<Vec<String>>,
) -> Result<Json<Value>> {
    require(ids.len() <= 1000, "播放列表最多 1000 首")?;
    for id in &ids {
        app.record(id).await?;
    }
    app.0.store.put("playlist", "main", &ids).await?;
    Ok(Json(json!({"saved":true})))
}
#[derive(Deserialize)]
struct Import {
    source: String,
    id: String,
}
async fn import_playlist(State(app): State<App>, Json(input): Json<Import>) -> Result<Json<Value>> {
    require(
        SOURCES.iter().any(|(id, _)| *id == input.source)
            && !input.id.is_empty()
            && input.id.len() <= 128
            && input
                .id
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '_'),
        "歌单音源或 ID 无效",
    )?;
    let config = app.0.config.read().await.clone();
    let source = app.source(&input.source, &config)?;
    let tracks = tokio::time::timeout(Duration::from_secs(45), source.playlist(&input.id, 1000))
        .await
        .map_err(|_| anyhow::anyhow!("歌单导入超时，请缩小歌单或稍后重试"))??;
    for record in &tracks {
        app.remember(record.clone()).await?;
    }
    Ok(Json(
        json!({"tracks":tracks.into_iter().map(|r|r.track).collect::<Vec<_>>(),"limit":1000,"detail":"当前最多导入音源返回的前 1000 首"}),
    ))
}
async fn download_jobs(State(app): State<App>) -> Result<Json<Vec<DownloadJob>>> {
    Ok(Json(app.0.store.list("download", 10000).await?))
}
async fn start_downloads(
    State(app): State<App>,
    Json(items): Json<Vec<downloads::DownloadItem>>,
) -> Result<Json<Vec<DownloadJob>>> {
    Ok(Json(downloads::enqueue(app, items).await?))
}
async fn control_download(
    State(app): State<App>,
    Path((id, action)): Path<(String, String)>,
) -> Result<Json<DownloadJob>> {
    Ok(Json(downloads::control(app, &id, &action).await?))
}
async fn files(State(app): State<App>) -> Result<Json<Vec<downloads::LibraryFile>>> {
    let root = app.0.music_root.clone();
    Ok(Json(
        tokio::task::spawn_blocking(move || downloads::files(&root)).await??,
    ))
}
#[derive(Deserialize)]
struct FileQuery {
    path: String,
}
async fn file_content(
    State(app): State<App>,
    Query(query): Query<FileQuery>,
    request: axum::extract::Request,
) -> Result<Response> {
    let path = downloads::library_path(&app.0.music_root, &query.path)?;
    use tower::ServiceExt;
    let mut response = ServeFile::new(path).oneshot(request).await?.map(Body::new);
    response.headers_mut().insert(
        header::CONTENT_DISPOSITION,
        axum::http::HeaderValue::from_static("attachment"),
    );
    Ok(response)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_single_ranges_are_accepted() {
        for range in ["bytes=0-100", "bytes=500-", "bytes=-500"] {
            assert!(valid_range(range))
        }
        for range in ["bytes=5-2", "bytes=0-1,3-4", "bytes=-", "bad"] {
            assert!(!valid_range(range))
        }
    }
}
