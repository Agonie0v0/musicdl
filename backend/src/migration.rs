use crate::{
    model::{Config, SOURCES, SourceConfig},
    store::Store,
};
use anyhow::Result;
use serde_json::Value;
use std::path::Path;
pub async fn legacy_config(store: &Store, path: &Path) -> Result<bool> {
    if store.get::<Config>("config", "main").await?.is_some() || !path.is_file() {
        return Ok(false);
    }
    let raw: Value = serde_json::from_slice(&std::fs::read(path)?)?;
    let mut config = Config::default();
    let old_id = |id: &str| {
        format!(
            "{}MusicClient",
            match id {
                "migu" => "Migu",
                "netease" => "Netease",
                "qq" => "QQ",
                "kuwo" => "Kuwo",
                _ => "Qianqian",
            }
        )
    };
    if let Some(list) = raw["music_sources"].as_array() {
        let sources: SizedSources = list
            .iter()
            .filter_map(|value| {
                SOURCES
                    .iter()
                    .find(|(id, _)| {
                        value.as_str() == Some(old_id(id).as_str()) || value.as_str() == Some(id)
                    })
                    .map(|(id, _)| id.to_string())
            })
            .collect();
        if !sources.is_empty() {
            config.sources = sources
        }
    }
    if let Some(limit) = raw["global_client_cfg"]["search_size_per_source"].as_u64() {
        config.results_per_source = (limit as usize).clamp(1, 50)
    }
    if let Some(timeout) = raw["request_timeout"].as_u64() {
        config.search_timeout_seconds = timeout.clamp(3, 60)
    }
    for field in ["download_layout", "lyrics_mode"] {
        if let Some(value) = raw[field].as_str() {
            if field == "download_layout" {
                config.download_layout = value.into()
            } else {
                config.lyrics_mode = value.into()
            }
        }
    }
    for (id, _) in SOURCES {
        let legacy = old_id(id);
        let cookie = raw["cookies"]
            .get(&legacy)
            .or_else(|| raw["cookies"].get(*id))
            .unwrap_or(&Value::Null);
        let cookie = match cookie {
            Value::String(s) => s.clone(),
            Value::Object(map) => map
                .iter()
                .filter_map(|(k, v)| v.as_str().map(|v| format!("{k}={v}")))
                .collect::<Vec<_>>()
                .join("; "),
            _ => String::new(),
        };
        let global = raw["proxy_url"].as_str().unwrap_or("");
        let selected = raw["proxy_scope"] == "selected";
        let applies = !selected
            || raw["proxy_sources"]
                .as_array()
                .is_some_and(|s| s.iter().any(|s| s.as_str() == Some(legacy.as_str())));
        let proxy = raw["requests_overrides"][&legacy]["proxies"]["https"]
            .as_str()
            .unwrap_or(if applies { global } else { "" })
            .to_owned();
        if !cookie.is_empty() || !proxy.is_empty() {
            config
                .source_settings
                .insert(id.to_string(), SourceConfig { cookie, proxy });
        }
    }
    config.validate()?;
    store.put("config", "main", &config).await?;
    Ok(true)
}
type SizedSources = Vec<String>;
#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn migration_preserves_own_cookie_and_never_overwrites_new_config() {
        let dir = tempfile::tempdir().unwrap();
        let old = dir.path().join("config.json");
        std::fs::write(&old,r#"{"music_sources":["NeteaseMusicClient","OtherMusicClient"],"cookies":{"NeteaseMusicClient":{"session":"user-cookie"}},"global_client_cfg":{"search_size_per_source":5}}"#).unwrap();
        let db = Store::open(&dir.path().join("state.db")).unwrap();
        assert!(legacy_config(&db, &old).await.unwrap());
        let cfg = db.get::<Config>("config", "main").await.unwrap().unwrap();
        assert_eq!(cfg.sources, vec!["netease"]);
        assert_eq!(cfg.source_settings["netease"].cookie, "session=user-cookie");
        assert!(!legacy_config(&db, &old).await.unwrap());
    }
}
