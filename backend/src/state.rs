use crate::{model::*, search::SearchJob, sources::Source, store::Store};
use anyhow::Result;
use std::{
    collections::HashMap,
    path::PathBuf,
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::sync::{Mutex, RwLock, Semaphore};

#[derive(Clone)]
pub struct App(pub Arc<Inner>);
pub struct Inner {
    pub store: Store,
    pub config: RwLock<Config>,
    pub jobs: Mutex<HashMap<String, Arc<SearchJob>>>,
    pub records: RwLock<HashMap<String, TrackRecord>>,
    pub assets: Mutex<HashMap<String, (Instant, Asset)>>,
    pub resolve_locks: Mutex<HashMap<String, Arc<Mutex<()>>>>,
    pub metadata: Mutex<HashMap<String, (Instant, Vec<TrackRecord>)>>,
    pub playback_slots: Arc<Semaphore>,
    pub search_slots: Arc<Semaphore>,
    pub music_root: PathBuf,
    pub downloads: Mutex<HashMap<String, crate::downloads::RunningDownload>>,
    pub download_slots: Arc<Semaphore>,
    pub clients: std::sync::Mutex<HashMap<String, Source>>,
    pub source_slots: HashMap<String, Arc<Semaphore>>,
}
impl App {
    pub async fn new(store: Store, music_root: PathBuf) -> Result<Self> {
        let config = store
            .get::<Config>("config", "main")
            .await?
            .unwrap_or_default();
        config.validate()?;
        std::fs::create_dir_all(&music_root)?;
        let concurrency = config.download_concurrency;
        Ok(Self(Arc::new(Inner {
            store,
            config: RwLock::new(config),
            jobs: Mutex::new(HashMap::new()),
            records: RwLock::new(HashMap::new()),
            assets: Mutex::new(HashMap::new()),
            resolve_locks: Mutex::new(HashMap::new()),
            metadata: Mutex::new(HashMap::new()),
            playback_slots: Arc::new(Semaphore::new(8)),
            search_slots: Arc::new(Semaphore::new(10)),
            music_root: music_root.canonicalize()?,
            downloads: Mutex::new(HashMap::new()),
            download_slots: Arc::new(Semaphore::new(concurrency)),
            clients: std::sync::Mutex::new(HashMap::new()),
            source_slots: SOURCES
                .iter()
                .map(|(id, _)| (id.to_string(), Arc::new(Semaphore::new(2))))
                .collect(),
        })))
    }
    pub fn source(&self, id: &str, config: &Config) -> Result<Source> {
        let key = format!("{}:{id}", config.fingerprint());
        let mut clients = self
            .0
            .clients
            .lock()
            .map_err(|_| anyhow::anyhow!("音源连接池失效"))?;
        if let Some(source) = clients.get(&key) {
            return Ok(source.clone());
        }
        if clients.len() >= 20 {
            clients.clear()
        }
        let source = Source::new(
            id,
            config.source_settings.get(id).cloned().unwrap_or_default(),
            config.search_timeout_seconds,
        )?;
        clients.insert(key, source.clone());
        Ok(source)
    }
    pub async fn remember(&self, record: TrackRecord) -> Result<()> {
        self.0.store.put("track", &record.track.id, &record).await?;
        let mut records = self.0.records.write().await;
        if records.len() >= 5000 {
            records.clear()
        }
        records.insert(record.track.id.clone(), record);
        Ok(())
    }
    pub async fn record(&self, id: &str) -> Result<TrackRecord> {
        if let Some(record) = self.0.records.read().await.get(id) {
            return Ok(record.clone());
        }
        self.0
            .store
            .get("track", id)
            .await?
            .ok_or_else(|| anyhow::anyhow!("曲目不存在，请重新搜索"))
    }
    pub async fn metadata(
        &self,
        source: &Source,
        keyword: &str,
        config: &Config,
    ) -> Result<Vec<TrackRecord>> {
        let key = format!(
            "{}:{}:{}",
            config.fingerprint(),
            source.id,
            keyword.trim().to_lowercase()
        );
        if let Some((time, list)) = self.0.metadata.lock().await.get(&key)
            && time.elapsed() < Duration::from_secs(120)
        {
            return Ok(list.clone());
        }
        let _permit = self.0.search_slots.acquire().await?;
        let list = source.search(keyword, config.results_per_source).await?;
        let mut cache = self.0.metadata.lock().await;
        cache.retain(|_, (time, _)| time.elapsed() < Duration::from_secs(120));
        if cache.len() >= 128 {
            cache.clear()
        }
        cache.insert(key, (Instant::now(), list.clone()));
        Ok(list)
    }
    pub async fn asset(
        &self,
        record: &TrackRecord,
        quality: Option<&str>,
        config: &Config,
    ) -> Result<Asset> {
        let key = format!(
            "{}:{}:{}",
            config.fingerprint(),
            record.track.id,
            quality.unwrap_or("playback")
        );
        let lock = {
            let mut locks = self.0.resolve_locks.lock().await;
            locks.retain(|_, l| Arc::strong_count(l) > 1);
            locks
                .entry(key.clone())
                .or_insert_with(|| Arc::new(Mutex::new(())))
                .clone()
        };
        let _guard = lock.lock().await;
        if let Some((time, asset)) = self.0.assets.lock().await.get(&key)
            && time.elapsed() < Duration::from_secs(90)
        {
            return Ok(asset.clone());
        }
        let _source_permit = self
            .0
            .source_slots
            .get(&record.track.source)
            .ok_or_else(|| anyhow::anyhow!("不支持的音源"))?
            .acquire()
            .await?;
        let _permit = self.0.playback_slots.acquire().await?;
        let source = self.source(&record.track.source, config)?;
        let asset = tokio::time::timeout(
            Duration::from_secs(config.playback_timeout_seconds),
            source.resolve(record, quality),
        )
        .await
        .map_err(|_| anyhow::anyhow!("资源解析超时，可重试或选择其他音源"))??;
        let mut cache = self.0.assets.lock().await;
        cache.retain(|_, (time, _)| time.elapsed() < Duration::from_secs(90));
        if cache.len() >= 512 {
            cache.clear()
        };
        cache.insert(key, (Instant::now(), asset.clone()));
        Ok(asset)
    }
    pub async fn playable(&self, mut record: TrackRecord, config: &Config) -> Track {
        match self.asset(&record, None, config).await {
            Ok(asset) => {
                if let Some(duration) = asset.duration {
                    record.track.duration = duration;
                }
                record.track.playback = Playback {
                    state: "ready".into(),
                    url: Some(format!("/api/stream/{}", record.track.id)),
                    codec: Some(asset.codec),
                    bitrate: asset.bitrate,
                    detail: None,
                    preview_only: asset.preview_only,
                }
            }
            Err(error) => {
                record.track.playback = Playback {
                    state: "unavailable".into(),
                    detail: Some(public_error(&error)),
                    ..Default::default()
                }
            }
        }
        record.track
    }
}
pub fn public_error(error: &anyhow::Error) -> String {
    // Network errors can contain signed URLs and account credentials.
    if let Some(err) = error.downcast_ref::<reqwest::Error>() {
        return if err.is_timeout() {
            "音源请求超时"
        } else if err.is_connect() {
            "无法连接音源，请检查网络或代理"
        } else {
            "音源请求失败，请稍后重试"
        }
        .into();
    }
    let message = error.to_string();
    if message.contains("http://") || message.contains("https://") || message.len() > 200 {
        "音源请求失败，请检查配置或更换音源".into()
    } else {
        message
    }
}
