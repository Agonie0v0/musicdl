use crate::{
    model::*,
    state::{App, public_error},
};
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use std::{
    path::{Path, PathBuf},
    time::{Duration, Instant},
};
use tokio::io::AsyncWriteExt;
use tokio_util::sync::CancellationToken;

#[derive(Deserialize)]
pub struct DownloadItem {
    pub track_id: String,
    pub quality_id: String,
}
pub async fn enqueue(app: App, items: Vec<DownloadItem>) -> Result<Vec<DownloadJob>> {
    ensure!(
        !items.is_empty() && items.len() <= 200,
        "每批请选择 1–200 首曲目"
    );
    let mut jobs = vec![];
    // Validate the entire batch before inserting any jobs.
    for item in items {
        let record = app.record(&item.track_id).await?;
        let q = record
            .track
            .qualities
            .iter()
            .find(|q| q.id == item.quality_id)
            .ok_or_else(|| anyhow::anyhow!("请选择曲目提供的音质版本"))?;
        jobs.push(DownloadJob {
            id: uuid::Uuid::new_v4().to_string(),
            quality_id: q.id.clone(),
            quality_label: q.label.clone(),
            track: record.track,
            status: "queued".into(),
            bytes: 0,
            total_bytes: None,
            detail: String::new(),
            file: None,
            created_at: now(),
            updated_at: now(),
        });
    }
    for job in &jobs {
        app.0.store.put("download", &job.id, job).await?;
        launch(app.clone(), job.clone()).await;
    }
    Ok(jobs)
}
pub async fn recover(app: &App) -> Result<()> {
    for mut job in app.0.store.list::<DownloadJob>("download", 10000).await? {
        if job.status != "complete" && uuid::Uuid::parse_str(&job.id).is_ok() {
            let _ =
                tokio::fs::remove_file(app.0.music_root.join(format!(".{}.part", job.id))).await;
            for suffix in [
                "tagged.mp3",
                "tagged.flac",
                "tagged.m4a",
                "tagged.ogg",
                "tagged.wav",
                "cover.jpg",
                "cover.png",
            ] {
                let _ =
                    tokio::fs::remove_file(app.0.music_root.join(format!(".{}.{suffix}", job.id)))
                        .await;
            }
        }
        if ["queued", "resolving", "downloading", "enriching"].contains(&job.status.as_str()) {
            job.status = "paused".into();
            job.detail = "服务已重启，点击继续重新下载".into();
            app.0.store.put("download", &job.id, &job).await?;
        }
    }
    Ok(())
}
pub async fn control(app: App, id: &str, action: &str) -> Result<DownloadJob> {
    let mut job = app
        .0
        .store
        .get::<DownloadJob>("download", id)
        .await?
        .ok_or_else(|| anyhow::anyhow!("下载任务不存在"))?;
    match action {
        "pause" | "cancel" => {
            ensure!(job.status != "complete", "已完成的任务不能暂停或取消");
            let runner = app.0.downloads.lock().await.get(id).cloned();
            if let Some(runner) = runner {
                runner.cancel.cancel();
                runner.finished.cancelled().await;
            }
            job = app
                .0
                .store
                .get("download", id)
                .await?
                .ok_or_else(|| anyhow::anyhow!("下载任务不存在"))?;
            ensure!(job.status != "complete", "曲目已完成下载");
            job.status = if action == "pause" {
                "paused"
            } else {
                "cancelled"
            }
            .into();
            job.detail = if action == "pause" {
                "已暂停；继续时重新验证资源"
            } else {
                "已取消"
            }
            .into();
        }
        "retry" => {
            ensure!(
                !app.0.downloads.lock().await.contains_key(id),
                "旧任务仍在清理，请稍后重试"
            );
            ensure!(
                ![
                    "queued",
                    "resolving",
                    "downloading",
                    "enriching",
                    "complete"
                ]
                .contains(&job.status.as_str()),
                "任务正在执行或已完成"
            );
            job.status = "queued".into();
            job.bytes = 0;
            job.detail.clear();
            app.0.store.put("download", id, &job).await?;
            launch(app.clone(), job.clone()).await;
            return Ok(job);
        }
        _ => anyhow::bail!("下载操作无效"),
    }
    job.updated_at = now();
    app.0.store.put("download", id, &job).await?;
    Ok(job)
}
async fn launch(app: App, job: DownloadJob) {
    let token = CancellationToken::new();
    let finished = CancellationToken::new();
    app.0.downloads.lock().await.insert(
        job.id.clone(),
        RunningDownload {
            cancel: token.clone(),
            finished: finished.clone(),
        },
    );
    tokio::spawn(async move {
        let mut job = job;
        let result = tokio::select! {biased;_=token.cancelled()=>None,result=run(&app,&mut job,&token)=>Some(result)};
        if let Some(Err(err)) = result {
            job.status = "failed".into();
            job.detail = public_error(&err);
            job.updated_at = now();
            let _ = app.0.store.put("download", &job.id, &job).await;
        }
        let _ = tokio::fs::remove_file(app.0.music_root.join(format!(".{}.part", job.id))).await;
        for codec in ["mp3", "flac", "m4a", "ogg", "wav"] {
            let _ = tokio::fs::remove_file(
                app.0.music_root.join(format!(".{}.tagged.{codec}", job.id)),
            )
            .await;
        }
        for ext in ["jpg", "png"] {
            let _ =
                tokio::fs::remove_file(app.0.music_root.join(format!(".{}.cover.{ext}", job.id)))
                    .await;
        }
        app.0.downloads.lock().await.remove(&job.id);
        finished.cancel();
    });
}
#[derive(Clone)]
pub struct RunningDownload {
    pub cancel: CancellationToken,
    pub finished: CancellationToken,
}
async fn save(app: &App, job: &mut DownloadJob, status: &str) -> Result<()> {
    job.status = status.into();
    job.updated_at = now();
    app.0.store.put("download", &job.id, job).await
}
async fn run(app: &App, job: &mut DownloadJob, token: &CancellationToken) -> Result<()> {
    let _permit = app.0.download_slots.acquire().await?;
    let config = app.0.config.read().await.clone();
    let record = app.record(&job.track.id).await?;
    save(app, job, "resolving").await?;
    let asset = app.asset(&record, Some(&job.quality_id), &config).await?;
    ensure!(!asset.preview_only, "试听片段不能作为完整曲目下载");
    let source = app.source(&record.track.source, &config)?;
    let response = source
        .client
        .get(&asset.url)
        .header("Referer", &asset.referer)
        .header("Accept-Encoding", "identity")
        .timeout(Duration::from_secs(3600))
        .send()
        .await?;
    ensure!(response.status().as_u16() == 200, "下载资源不可用，请重试");
    job.total_bytes = response.content_length().or(asset.size);
    job.bytes = 0;
    let tmp = app.0.music_root.join(format!(".{}.part", job.id));
    let mut file = tokio::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&tmp)
        .await?;
    save(app, job, "downloading").await?;
    let mut response = response;
    let mut saved = Instant::now();
    let mut prefix = vec![];
    while let Some(chunk) = response.chunk().await? {
        ensure!(!token.is_cancelled(), "已取消");
        if prefix.len() < 8192 {
            prefix.extend_from_slice(&chunk[..chunk.len().min(8192 - prefix.len())]);
        }
        file.write_all(&chunk).await?;
        job.bytes += chunk.len() as u64;
        if saved.elapsed() > Duration::from_millis(400) {
            save(app, job, "downloading").await?;
            saved = Instant::now();
        }
    }
    file.flush().await?;
    file.sync_all().await?;
    drop(file);
    ensure!(job.bytes > 0, "资源为空");
    if let Some(total) = job.total_bytes {
        ensure!(total == job.bytes, "下载不完整，请重试");
    }
    let codec = crate::http::detect_codec(&prefix, "")
        .ok_or_else(|| anyhow::anyhow!("下载的文件不是音频"))?;
    let quality = record
        .track
        .qualities
        .iter()
        .find(|q| q.id == job.quality_id)
        .unwrap();
    ensure!(
        !quality.lossless || codec == "flac",
        "下载资源不是所选无损格式，已禁止降级"
    );
    let segments = match config.download_layout.as_str() {
        "source" => vec![record.track.source.clone()],
        "artist" => vec![record.track.artist.clone()],
        "artist_album" => vec![record.track.artist.clone(), record.track.album.clone()],
        _ => vec![],
    };
    let folder = safe_directory(&app.0.music_root, &segments)?;
    let stem = format!(
        "{} - {} [{}-{}]",
        clean(&record.track.artist),
        clean(&record.track.title),
        record.track.source,
        &job.id[..8]
    );
    let target = folder.join(format!("{stem}.{codec}"));
    save(app, job, "enriching").await?;
    let lyrics = if config.lyrics_mode != "none" {
        tokio::time::timeout(Duration::from_secs(6), source.lyrics(&record))
            .await
            .ok()
            .and_then(Result::ok)
            .unwrap_or_default()
    } else {
        String::new()
    };
    // FFmpeg is optional; a failed enrichment leaves the validated original intact.
    if config.embed_metadata || ["embed", "both"].contains(&config.lyrics_mode.as_str()) {
        let cover = if config.embed_metadata && ["mp3", "flac", "m4a"].contains(&codec) {
            if let Some(url) = record.track.cover.as_ref() {
                let result = tokio::time::timeout(Duration::from_secs(4), async {
                    crate::http::validate_url(url)?;
                    let response = source
                        .client
                        .get(url)
                        .header("Referer", &asset.referer)
                        .send()
                        .await?;
                    ensure!(response.status().is_success(), "封面不可用");
                    let bytes = crate::http::limited_bytes(response, 2 * 1024 * 1024).await?;
                    let ext = if bytes.starts_with(&[0xff, 0xd8, 0xff]) {
                        "jpg"
                    } else if bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
                        "png"
                    } else {
                        anyhow::bail!("封面格式不支持")
                    };
                    let path = app.0.music_root.join(format!(".{}.cover.{ext}", job.id));
                    tokio::fs::write(&path, &bytes).await?;
                    Ok::<_, anyhow::Error>(path)
                })
                .await;
                result.ok().and_then(Result::ok)
            } else {
                None
            }
        } else {
            None
        };
        let tagged = app.0.music_root.join(format!(".{}.tagged.{codec}", job.id));
        let mut cmd = tokio::process::Command::new("ffmpeg");
        cmd.kill_on_drop(true)
            .arg("-nostdin")
            .arg("-v")
            .arg("error")
            .arg("-i")
            .arg(&tmp);
        if let Some(cover) = &cover {
            cmd.arg("-i").arg(cover);
        }
        cmd.args(["-map", "0:a:0", "-c:a", "copy"]);
        if cover.is_some() {
            cmd.args([
                "-map",
                "1:v:0",
                "-c:v",
                "copy",
                "-disposition:v:0",
                "attached_pic",
            ]);
        }
        if config.embed_metadata {
            for (key, value) in [
                ("title", &record.track.title),
                ("artist", &record.track.artist),
                ("album", &record.track.album),
            ] {
                cmd.arg("-metadata").arg(format!("{key}={value}"));
            }
        }
        if !lyrics.is_empty() && ["embed", "both"].contains(&config.lyrics_mode.as_str()) {
            cmd.arg("-metadata").arg(format!("lyrics={lyrics}"));
        }
        let output = tokio::time::timeout(Duration::from_secs(30), cmd.arg(&tagged).output()).await;
        if let Some(cover) = cover {
            let _ = tokio::fs::remove_file(cover).await;
        }
        if output.is_ok_and(|o| o.is_ok_and(|o| o.status.success())) {
            tokio::fs::remove_file(&tmp).await?;
            tokio::fs::rename(&tagged, &tmp).await?;
        } else {
            let _ = tokio::fs::remove_file(&tagged).await;
            job.detail = "音频已下载；标签写入失败或未安装 FFmpeg".into();
        }
    }
    ensure!(!token.is_cancelled(), "已取消");
    ensure!(!target.exists(), "目标文件已存在");
    tokio::fs::rename(&tmp, &target).await?;
    if !lyrics.is_empty()
        && ["lrc", "both"].contains(&config.lyrics_mode.as_str())
        && tokio::fs::write(target.with_extension("lrc"), &lyrics)
            .await
            .is_err()
    {
        job.detail = "音频已下载；歌词文件保存失败".into();
    }
    job.file = Some(
        target
            .strip_prefix(&app.0.music_root)?
            .to_string_lossy()
            .replace('\\', "/"),
    );
    save(app, job, "complete").await?;
    Ok(())
}
pub fn clean(name: &str) -> String {
    let name: String = name
        .chars()
        .map(|c| {
            if c.is_control() || "<>:\"/\\|?*".contains(c) {
                '_'
            } else {
                c
            }
        })
        .take(100)
        .collect();
    let name = name.trim().trim_matches('.');
    let head = name.split('.').next().unwrap_or("").to_uppercase();
    if name.is_empty()
        || [
            "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7",
            "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
        ]
        .contains(&head.as_str())
    {
        format!("_{name}")
    } else {
        name.to_owned()
    }
}
fn is_link(metadata: &std::fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    {
        metadata.file_type().is_symlink()
    }
}
pub fn safe_directory(root: &Path, segments: &[String]) -> Result<PathBuf> {
    let mut path = root.to_owned();
    for segment in segments {
        path.push(clean(segment));
        if path.exists() {
            let meta = std::fs::symlink_metadata(&path)?;
            ensure!(!is_link(&meta), "禁止使用链接目录");
        } else {
            std::fs::create_dir(&path)?;
        }
        ensure!(path.canonicalize()?.starts_with(root), "目录超出音乐库范围");
    }
    Ok(path)
}
pub fn library_path(root: &Path, relative: &str) -> Result<PathBuf> {
    let relative = Path::new(relative);
    ensure!(
        !relative.as_os_str().is_empty()
            && relative
                .components()
                .all(|c| matches!(c, std::path::Component::Normal(_))),
        "文件路径无效"
    );
    let mut current = root.to_owned();
    for segment in relative.components() {
        current.push(segment);
        ensure!(
            !is_link(&std::fs::symlink_metadata(&current)?),
            "禁止访问链接文件"
        );
    }
    let current = current.canonicalize()?;
    ensure!(
        current.starts_with(root) && current.is_file(),
        "文件不在音乐库中"
    );
    Ok(current)
}
#[derive(Serialize)]
pub struct LibraryFile {
    pub path: String,
    pub name: String,
    pub size: u64,
}
pub fn files(root: &Path) -> Result<Vec<LibraryFile>> {
    let mut output = vec![];
    let mut dirs = vec![root.to_owned()];
    while let Some(dir) = dirs.pop() {
        for item in std::fs::read_dir(dir)? {
            let item = item?;
            let meta = std::fs::symlink_metadata(item.path())?;
            if is_link(&meta) {
                continue;
            }
            if meta.is_dir() {
                dirs.push(item.path())
            } else if meta.is_file() && output.len() < 10000 {
                let path = item.path();
                if path
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .starts_with('.')
                {
                    continue;
                }
                output.push(LibraryFile {
                    path: path
                        .strip_prefix(root)?
                        .to_string_lossy()
                        .replace('\\', "/"),
                    name: item.file_name().to_string_lossy().into(),
                    size: item.metadata()?.len(),
                });
            }
        }
    }
    output.sort_by(|a, b| a.path.cmp(&b.path));
    Ok(output)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn paths_do_not_escape_and_names_are_portable() {
        assert!(!clean("../CON:a").contains('/'));
        assert_eq!(clean("CON"), "_CON");
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().canonicalize().unwrap();
        assert!(library_path(&root, "../secret").is_err());
        let folder = safe_directory(&root, &["../歌手".into()]).unwrap();
        assert!(folder.starts_with(&root));
    }
    #[tokio::test]
    async fn downloads_save_real_bytes_and_reject_lossless_downgrade() {
        use crate::{sources::Source, store::Store};

        let dir = tempfile::tempdir().unwrap();
        let app = App::new(
            Store::open(&dir.path().join("state.db")).unwrap(),
            dir.path().join("music"),
        )
        .await
        .unwrap();
        let config = Config {
            embed_metadata: false,
            lyrics_mode: "none".into(),
            ..Default::default()
        };
        *app.0.config.write().await = config.clone();
        let mut audio = vec![0; 32000];
        audio[..4].copy_from_slice(&[0xff, 0xfb, 0x90, 0]);
        let body = audio.clone();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            axum::serve(
                listener,
                axum::Router::new().route(
                    "/audio",
                    axum::routing::get(move || {
                        let body = body.clone();
                        async move { ([(axum::http::header::CONTENT_TYPE, "audio/mpeg")], body) }
                    }),
                ),
            )
            .await
            .unwrap()
        });
        app.0.clients.lock().unwrap().insert(
            format!("{}:netease", config.fingerprint()),
            Source {
                id: "netease".into(),
                client: reqwest::Client::new(),
                config: SourceConfig::default(),
            },
        );
        let record = TrackRecord::new(
            "netease",
            "test".into(),
            "测试曲目".into(),
            "歌手".into(),
            "专辑".into(),
            0.,
            None,
            vec![
                Quality::new("standard", "mp3", Some(128), false),
                Quality::new("lossless", "flac", None, true),
            ],
            serde_json::Value::Null,
        );
        app.remember(record.clone()).await.unwrap();
        for quality in ["standard", "lossless"] {
            app.0.assets.lock().await.insert(
                format!("{}:{}:{quality}", config.fingerprint(), record.track.id),
                (
                    Instant::now(),
                    Asset {
                        url: format!("http://{addr}/audio"),
                        codec: if quality == "lossless" { "flac" } else { "mp3" }.into(),
                        bitrate: Some(128),
                        preview_only: false,
                        size: Some(audio.len() as u64),
                        referer: String::new(),
                        duration: Some(2.),
                    },
                ),
            );
        }
        let jobs = enqueue(
            app.clone(),
            vec![
                DownloadItem {
                    track_id: record.track.id.clone(),
                    quality_id: "standard".into(),
                },
                DownloadItem {
                    track_id: record.track.id.clone(),
                    quality_id: "lossless".into(),
                },
            ],
        )
        .await
        .unwrap();
        tokio::time::timeout(Duration::from_secs(3), async {
            loop {
                if app.0.downloads.lock().await.is_empty() {
                    break;
                }
                tokio::task::yield_now().await
            }
        })
        .await
        .unwrap();
        let good = app
            .0
            .store
            .get::<DownloadJob>("download", &jobs[0].id)
            .await
            .unwrap()
            .unwrap();
        let bad = app
            .0
            .store
            .get::<DownloadJob>("download", &jobs[1].id)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(good.status, "complete");
        assert_eq!(good.bytes, 32000);
        assert_eq!(
            std::fs::read(library_path(&app.0.music_root, good.file.as_ref().unwrap()).unwrap())
                .unwrap(),
            audio
        );
        assert_eq!(bad.status, "failed");
        assert!(bad.file.is_none());
        assert!(bad.detail.contains("禁止降级"));
        assert_eq!(files(&app.0.music_root).unwrap().len(), 1);

        server.abort();
    }
}
