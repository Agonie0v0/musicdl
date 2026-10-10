use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

pub const SOURCES: &[(&str, &str)] = &[
    ("migu", "咪咕"),
    ("netease", "网易云"),
    ("qq", "QQ音乐"),
    ("kuwo", "酷我"),
    ("qianqian", "千千"),
];

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Quality {
    pub id: String,
    pub label: String,
    pub codec: String,
    pub bitrate: Option<u32>,
    pub lossless: bool,
    pub availability: String,
    pub evidence: String,
}
impl Quality {
    pub fn new(id: &str, codec: &str, bitrate: Option<u32>, lossless: bool) -> Self {
        Self {
            id: id.into(),
            label: if lossless {
                format!("{} 无损", codec.to_uppercase())
            } else if let Some(rate) = bitrate {
                format!("{} {}", codec.to_uppercase(), rate)
            } else {
                codec.to_uppercase()
            },
            codec: codec.into(),
            bitrate,
            lossless,
            availability: "advertised".into(),
            evidence: "source".into(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Playback {
    pub state: String,
    pub url: Option<String>,
    pub codec: Option<String>,
    pub bitrate: Option<u32>,
    pub detail: Option<String>,
    pub preview_only: bool,
}
impl Default for Playback {
    fn default() -> Self {
        Self {
            state: "pending".into(),
            url: None,
            codec: None,
            bitrate: None,
            detail: None,
            preview_only: false,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Track {
    pub id: String,
    pub source: String,
    pub source_id: String,
    pub title: String,
    pub artist: String,
    pub album: String,
    pub duration: f64,
    pub cover: Option<String>,
    pub qualities: Vec<Quality>,
    pub playback: Playback,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TrackRecord {
    pub track: Track,
    pub raw: Value,
}

impl TrackRecord {
    #[allow(clippy::too_many_arguments)] // Mirrors one source metadata record; no mutable builder state.
    pub fn new(
        source: &str,
        source_id: String,
        title: String,
        artist: String,
        album: String,
        duration: f64,
        cover: Option<String>,
        qualities: Vec<Quality>,
        raw: Value,
    ) -> Self {
        let digest = Sha256::digest(format!("{source}\0{source_id}"));
        Self {
            track: Track {
                id: hex::encode(&digest[..12]),
                source: source.into(),
                source_id,
                title,
                artist,
                album,
                duration,
                cover: cover.filter(|s| s.starts_with("https://") || s.starts_with("http://")),
                qualities,
                playback: Playback::default(),
            },
            raw,
        }
    }
}

#[derive(Debug, Clone)]
pub struct Asset {
    pub url: String,
    pub codec: String,
    pub bitrate: Option<u32>,
    pub preview_only: bool,
    pub size: Option<u64>,
    pub referer: String,
    pub duration: Option<f64>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct SourceConfig {
    pub cookie: String,
    pub proxy: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    pub sources: Vec<String>,
    pub results_per_source: usize,
    pub search_timeout_seconds: u64,
    pub playback_timeout_seconds: u64,
    pub download_concurrency: usize,
    pub download_layout: String,
    pub lyrics_mode: String,
    pub embed_metadata: bool,
    pub source_settings: BTreeMap<String, SourceConfig>,
}
impl Default for Config {
    fn default() -> Self {
        Self {
            sources: SOURCES.iter().map(|(id, _)| id.to_string()).collect(),
            results_per_source: 10,
            search_timeout_seconds: 15,
            playback_timeout_seconds: 6,
            download_concurrency: 2,
            download_layout: "artist_album".into(),
            lyrics_mode: "both".into(),
            embed_metadata: true,
            source_settings: BTreeMap::new(),
        }
    }
}
impl Config {
    pub fn validate(&self) -> anyhow::Result<()> {
        anyhow::ensure!(
            !self.sources.is_empty()
                && self
                    .sources
                    .iter()
                    .collect::<std::collections::BTreeSet<_>>()
                    .len()
                    == self.sources.len()
                && self.sources.len() <= SOURCES.len()
                && self
                    .sources
                    .iter()
                    .all(|s| SOURCES.iter().any(|(id, _)| s == id)),
            "请选择至少一个受支持音源"
        );
        anyhow::ensure!(
            (1..=50).contains(&self.results_per_source),
            "每源结果数应为 1–50"
        );
        anyhow::ensure!(
            (3..=60).contains(&self.search_timeout_seconds)
                && (2..=20).contains(&self.playback_timeout_seconds),
            "搜索或试听超时设置超出范围"
        );
        anyhow::ensure!(
            (1..=4).contains(&self.download_concurrency),
            "下载并发应为 1–4"
        );
        anyhow::ensure!(
            ["source", "artist", "artist_album", "flat"].contains(&self.download_layout.as_str()),
            "下载目录方式无效"
        );
        anyhow::ensure!(
            ["lrc", "embed", "both", "none"].contains(&self.lyrics_mode.as_str()),
            "歌词设置无效"
        );
        for (id, cfg) in &self.source_settings {
            anyhow::ensure!(SOURCES.iter().any(|(s, _)| id == s), "包含不支持的音源配置");
            anyhow::ensure!(
                cfg.cookie.len() <= 16_384 && !cfg.cookie.contains(['\r', '\n']),
                "Cookie 格式无效"
            );
            if !cfg.proxy.is_empty() {
                let url = url::Url::parse(&cfg.proxy)?;
                anyhow::ensure!(
                    ["http", "https"].contains(&url.scheme()) && url.host_str().is_some(),
                    "代理必须为 HTTP / HTTPS 地址"
                );
            }
        }
        Ok(())
    }
    pub fn fingerprint(&self) -> String {
        hex::encode(Sha256::digest(serde_json::to_vec(self).unwrap_or_default()))
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DownloadJob {
    pub id: String,
    pub track: Track,
    pub quality_id: String,
    pub quality_label: String,
    pub status: String,
    pub bytes: u64,
    pub total_bytes: Option<u64>,
    pub detail: String,
    pub file: Option<String>,
    pub created_at: u64,
    pub updated_at: u64,
}
pub fn now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn stable_identity_is_source_specific_and_config_is_bounded() {
        let a = TrackRecord::new(
            "migu",
            "1".into(),
            "歌曲".into(),
            "歌手".into(),
            "".into(),
            0.,
            None,
            vec![],
            Value::Null,
        );
        let b = TrackRecord::new(
            "qq",
            "1".into(),
            "歌曲".into(),
            "歌手".into(),
            "".into(),
            0.,
            None,
            vec![],
            Value::Null,
        );
        assert_ne!(a.track.id, b.track.id);
        let cfg = Config {
            results_per_source: 0,
            ..Default::default()
        };
        assert!(cfg.validate().is_err());
    }
}
