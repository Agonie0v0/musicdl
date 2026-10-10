use crate::{
    model::*,
    state::{App, public_error},
};
use anyhow::Result;
use futures_util::{StreamExt, stream::FuturesUnordered};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::sync::{Mutex, broadcast};
use tokio_util::sync::CancellationToken;

#[derive(Clone, Serialize)]
pub struct SearchEvent {
    pub sequence: u64,
    pub kind: String,
    pub data: Value,
}
#[derive(Default, Serialize)]
pub struct Snapshot {
    pub tracks: BTreeMap<String, Track>,
    pub sources: BTreeMap<String, Value>,
    pub done: bool,
    pub first_playable_ms: Option<u128>,
    pub metadata_ms: Option<u128>,
}
pub struct SearchJob {
    pub cancel: CancellationToken,
    pub events: Mutex<Vec<SearchEvent>>,
    pub snapshot: Mutex<Snapshot>,
    pub tx: broadcast::Sender<SearchEvent>,
    pub created: Instant,
}
impl SearchJob {
    fn new() -> Self {
        let (tx, _) = broadcast::channel(256);
        Self {
            cancel: CancellationToken::new(),
            events: Mutex::new(vec![]),
            snapshot: Mutex::new(Snapshot::default()),
            tx,
            created: Instant::now(),
        }
    }
    pub async fn emit(&self, kind: &str, data: Value) {
        let mut events = self.events.lock().await;
        let event = SearchEvent {
            sequence: events.len() as u64 + 1,
            kind: kind.into(),
            data,
        };
        events.push(event.clone());
        let _ = self.tx.send(event);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::Store;
    #[tokio::test]
    async fn cached_candidates_become_ready_and_events_are_replayable() {
        let dir = tempfile::tempdir().unwrap();
        let app = App::new(
            Store::open(&dir.path().join("state.db")).unwrap(),
            dir.path().join("music"),
        )
        .await
        .unwrap();
        let config = app.0.config.read().await.clone();
        let record = TrackRecord::new(
            "netease",
            "1".into(),
            "测试".into(),
            "歌手".into(),
            "专辑".into(),
            60.,
            None,
            vec![Quality::new("standard", "mp3", Some(128), false)],
            Value::Null,
        );
        app.0.metadata.lock().await.insert(
            format!("{}:netease:测试", config.fingerprint()),
            (Instant::now(), vec![record.clone()]),
        );
        app.0.assets.lock().await.insert(
            format!("{}:{}:playback", config.fingerprint(), record.track.id),
            (
                Instant::now(),
                Asset {
                    url: "https://example.com/a.mp3".into(),
                    codec: "mp3".into(),
                    bitrate: Some(128),
                    preview_only: false,
                    size: None,
                    duration: None,
                    referer: String::new(),
                },
            ),
        );
        let id = start(
            app.clone(),
            SearchRequest {
                keyword: "测试".into(),
                sources: Some(vec!["netease".into()]),
            },
        )
        .await
        .unwrap();
        let job = app.0.jobs.lock().await.get(&id).unwrap().clone();
        tokio::time::timeout(Duration::from_secs(2), job.cancel.cancelled())
            .await
            .unwrap();
        let events = job.events.lock().await;
        assert_eq!(
            events.iter().map(|e| e.sequence).collect::<Vec<_>>(),
            vec![1, 2, 3, 4]
        );
        assert_eq!(events[1].kind, "candidate");
        assert_eq!(events[2].kind, "track");
        assert_eq!(events[2].data["playback"]["state"], "ready");
        assert_eq!(events[3].kind, "done");
        assert!(job.snapshot.lock().await.first_playable_ms.is_some());
    }
    #[tokio::test]
    async fn cancellation_drops_network_and_releases_slots() {
        let dir = tempfile::tempdir().unwrap();
        let app = App::new(
            Store::open(&dir.path().join("state.db")).unwrap(),
            dir.path().join("music"),
        )
        .await
        .unwrap();
        let permit = app
            .0
            .search_slots
            .clone()
            .acquire_many_owned(10)
            .await
            .unwrap();
        let id = start(
            app.clone(),
            SearchRequest {
                keyword: "cancel".into(),
                sources: Some(vec!["netease".into()]),
            },
        )
        .await
        .unwrap();
        let job = app.0.jobs.lock().await.get(&id).unwrap().clone();
        job.cancel.cancel();
        tokio::time::timeout(Duration::from_secs(1), async {
            loop {
                if job.snapshot.lock().await.done {
                    break;
                }
                tokio::task::yield_now().await
            }
        })
        .await
        .unwrap();
        drop(permit);
        assert_eq!(app.0.search_slots.available_permits(), 10);
        assert_eq!(job.events.lock().await.last().unwrap().kind, "done");
    }
}
#[derive(Deserialize)]
pub struct SearchRequest {
    pub keyword: String,
    pub sources: Option<Vec<String>>,
}
pub async fn start(app: App, request: SearchRequest) -> Result<String> {
    let keyword = request.keyword.trim().to_owned();
    anyhow::ensure!(
        !keyword.is_empty() && keyword.chars().count() <= 200,
        "请输入 1–200 字的搜索词"
    );
    let config = app.0.config.read().await.clone();
    let sources = request.sources.unwrap_or_else(|| config.sources.clone());
    anyhow::ensure!(
        sources
            .iter()
            .collect::<std::collections::BTreeSet<_>>()
            .len()
            == sources.len(),
        "搜索音源不能重复"
    );
    anyhow::ensure!(
        !sources.is_empty()
            && sources.len() <= SOURCES.len()
            && sources
                .iter()
                .all(|s| SOURCES.iter().any(|(id, _)| s == id)),
        "搜索音源无效"
    );
    let id = uuid::Uuid::new_v4().to_string();
    let job = Arc::new(SearchJob::new());
    {
        let mut jobs = app.0.jobs.lock().await;
        jobs.retain(|_, job| job.created.elapsed() < Duration::from_secs(300));
        if jobs.len() >= 128
            && let Some(id) = jobs
                .iter()
                .filter(|(_, j)| j.cancel.is_cancelled())
                .max_by_key(|(_, j)| j.created.elapsed())
                .map(|(id, _)| id.clone())
        {
            jobs.remove(&id);
        }
        anyhow::ensure!(
            jobs.values().filter(|j| !j.cancel.is_cancelled()).count() < 20,
            "搜索任务过多，请稍后重试"
        );
        jobs.insert(id.clone(), job.clone());
    }
    tokio::spawn(async move {
        run(app, job, keyword, sources, config).await;
    });
    Ok(id)
}
async fn run(app: App, job: Arc<SearchJob>, keyword: String, sources: Vec<String>, config: Config) {
    let mut tasks = FuturesUnordered::new();
    for id in sources {
        let app = app.clone();
        let job = job.clone();
        let keyword = keyword.clone();
        let config = config.clone();
        tasks.push(async move{
        let result=tokio::select!{_ = job.cancel.cancelled()=>return, result=tokio::time::timeout(Duration::from_secs(config.search_timeout_seconds),async{let source=app.source(&id,&config)?;app.metadata(&source,&keyword,&config).await})=>result.unwrap_or_else(|_|Err(anyhow::anyhow!("搜索超时")))};
        match result{
            Ok(records)=>{
                let status=json!({"state":"complete","count":records.len()});job.snapshot.lock().await.sources.insert(id.clone(),status.clone());job.emit("source",json!({"id":id,"status":status})).await;
                let mut resolutions=FuturesUnordered::new();
                for record in records{
                    if job.cancel.is_cancelled(){return}
                    if app.remember(record.clone()).await.is_err(){job.emit("error",json!({"detail":"无法保存曲目，请检查数据目录"})).await;continue}
                    {let mut snapshot=job.snapshot.lock().await;snapshot.metadata_ms.get_or_insert(job.created.elapsed().as_millis());snapshot.tracks.insert(record.track.id.clone(),record.track.clone());}
                    job.emit("candidate",json!(record.track)).await;
                    let app=app.clone();let config=config.clone();let job=job.clone();resolutions.push(async move{tokio::select!{_=job.cancel.cancelled()=>(),track=app.playable(record,&config)=>{
                        {let mut snapshot=job.snapshot.lock().await;if track.playback.state=="ready"{snapshot.first_playable_ms.get_or_insert(job.created.elapsed().as_millis());}snapshot.tracks.insert(track.id.clone(),track.clone());}
                        job.emit("track",json!(track)).await;
                    }}});
                }
                while resolutions.next().await.is_some(){}
            },
            Err(error)=>{let status=json!({"state":"failed","detail":public_error(&error)});job.snapshot.lock().await.sources.insert(id.clone(),status.clone());job.emit("source",json!({"id":id,"status":status})).await;}
        }
    });
    }
    while tasks.next().await.is_some() {}
    job.snapshot.lock().await.done = true;
    job.emit("done", json!({"cancelled":job.cancel.is_cancelled()}))
        .await;
    job.cancel.cancel();
}
