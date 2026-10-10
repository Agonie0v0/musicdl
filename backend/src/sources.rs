//! Native adapters: metadata queries never resolve every download version or fetch lyrics.
use crate::{
    http,
    model::{Asset, Quality, SourceConfig, TrackRecord, now},
};
use aes::{
    Aes128,
    cipher::{BlockEncrypt, KeyInit, generic_array::GenericArray},
};
use anyhow::{Result, bail};
use base64::{Engine, engine::general_purpose::STANDARD};
use md5::{Digest, Md5};
use reqwest::Client;
use serde_json::{Value, json};
use std::{collections::BTreeMap, time::Duration};

#[derive(Clone)]
pub struct Source {
    pub id: String,
    pub client: Client,
    pub config: SourceConfig,
}
fn s(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        _ => String::new(),
    }
}
fn n(v: &Value) -> f64 {
    v.as_f64()
        .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
        .unwrap_or(0.)
}
fn artist(v: &Value, keys: &[&str]) -> String {
    v.as_array()
        .map(|list| {
            list.iter()
                .filter_map(|v| keys.iter().find_map(|k| v.get(*k).map(s)))
                .filter(|s| !s.is_empty())
                .collect::<Vec<_>>()
                .join(", ")
        })
        .unwrap_or_default()
}
fn first(v: &Value, keys: &[&str]) -> String {
    keys.iter()
        .map(|k| s(&v[*k]))
        .find(|s| !s.is_empty())
        .unwrap_or_default()
}
fn optional(s: String) -> Option<String> {
    (!s.is_empty()).then_some(s)
}

impl Source {
    pub fn new(id: &str, config: SourceConfig, timeout: u64) -> Result<Self> {
        Ok(Self {
            id: id.into(),
            client: http::client(&config, timeout)?,
            config,
        })
    }
    fn request(&self, method: reqwest::Method, url: &str) -> reqwest::RequestBuilder {
        let referer = match self.id.as_str() {
            "netease" => "https://music.163.com/",
            "qq" => "https://y.qq.com/",
            "kuwo" => "https://www.kuwo.cn/",
            "qianqian" => "https://music.91q.com/player",
            _ => "https://h5.nf.migu.cn/",
        };
        let mut request = self.client.request(method, url).header("Referer", referer);
        if !self.config.cookie.is_empty() {
            request = request.header("Cookie", &self.config.cookie)
        }
        if self.id == "migu" {
            request = request
                .header("Origin", "https://h5.nf.migu.cn")
                .header("ua", "Android_migu")
                .header("version", "6.8.8")
                .header("channel", "014021I")
                .header("subchannel", "014021I")
        }
        if self.id == "qianqian" {
            request = request.header("from", "web");
            if let Some(token) = cookie(&self.config.cookie, "access_token") {
                request = request.header("Authorization", format!("access_token {token}"))
            }
        }
        request
    }
    pub async fn search(&self, keyword: &str, limit: usize) -> Result<Vec<TrackRecord>> {
        let value=match self.id.as_str(){
            "migu"=>http::json(self.request(reqwest::Method::GET,"https://c.musicapp.migu.cn/v1.0/content/search_all.do").query(&[("text",keyword.to_owned()),("pageNo","1".into()),("pageSize",limit.to_string()),("isCopyright","1".into()),("sort","1".into()),("searchSwitch",json!({"song":1,"album":0,"singer":0,"tagSong":1,"mvSong":0,"bestShow":1}).to_string())]).send().await?).await?,
            "netease"=>http::json(self.request(reqwest::Method::POST,"https://music.163.com/api/cloudsearch/pc").form(&[("s",keyword.to_owned()),("type","1".into()),("limit",limit.to_string()),("offset","0".into())]).send().await?).await?,
            "qq"=>self.qq_call("music.search.SearchCgiService","DoSearchForQQMusicMobile",json!({"searchid":now().to_string(),"query":keyword,"search_type":0,"num_per_page":limit,"page_num":1,"highlight":0,"grp":1})).await?,
            "kuwo"=>http::json(self.request(reqwest::Method::GET,"https://www.kuwo.cn/search/searchMusicBykeyWord").query(&[("all",keyword.to_owned()),("vipver","1".into()),("client","kt".into()),("ft","music".into()),("cluster","0".into()),("strategy","2012".into()),("encoding","utf8".into()),("rformat","json".into()),("mobi","1".into()),("issubtitle","1".into()),("show_copyright_off","1".into()),("pn","0".into()),("rn",limit.to_string())]).send().await?).await?,
            "qianqian"=>http::json(self.request(reqwest::Method::GET,"https://music.91q.com/v1/search").query(&qianqian_sign([("word",keyword.to_owned()),("type","1".into()),("pageNo","1".into()),("pageSize",limit.to_string()),("appid","16073360".into())])).send().await?).await?,
            _=>bail!("音源尚未迁移"),
        };
        let items = match self.id.as_str() {
            "migu" => &value["songResultData"]["result"],
            "netease" => &value["result"]["songs"],
            "qq" => &value["data"]["body"]["item_song"],
            "kuwo" => &value["abslist"],
            _ => &value["data"]["typeTrack"],
        };
        if items.is_null() {
            let empty = match self.id.as_str() {
                "netease" => value["code"] == 200 && n(&value["result"]["songCount"]) == 0.,
                "migu" => {
                    value["code"] == "000000" && n(&value["songResultData"]["totalCount"]) == 0.
                }
                "kuwo" => n(&value["TOTAL"]) == 0.,
                _ => false,
            };
            if empty {
                return Ok(vec![]);
            }
            bail!("音源接口返回异常或账号权限不足")
        }
        let tracks = items
            .as_array()
            .ok_or_else(|| anyhow::anyhow!("音源列表格式发生变化"))?
            .iter()
            .take(limit)
            .filter_map(|raw| parse_track(&self.id, raw.clone()))
            .collect();
        Ok(tracks)
    }
    async fn qq_call(&self, module: &str, method: &str, param: Value) -> Result<Value> {
        let mut comm = json!({"cv":13020508,"v":13020508,"ct":"11","tmeAppID":"qqmusic","format":"json","inCharset":"utf-8","outCharset":"utf-8","QIMEI36":"6c9d3cd110abca9b16311cee10001e717614"});
        if let (Some(uin), Some(key)) = (
            cookie(&self.config.cookie, "uin").or_else(|| cookie(&self.config.cookie, "musicid")),
            cookie(&self.config.cookie, "qqmusic_key")
                .or_else(|| cookie(&self.config.cookie, "musickey")),
        ) {
            comm["qq"] = json!(uin);
            comm["authst"] = json!(key);
            comm["tmeLoginType"] = json!("2")
        }
        let key = format!("{module}.{method}");
        let data = json!({"comm":comm,key.clone():{"module":module,"method":method,"param":param}});
        let value = http::json(
            self.request(
                reqwest::Method::POST,
                "https://u.y.qq.com/cgi-bin/musicu.fcg",
            )
            .json(&data)
            .send()
            .await?,
        )
        .await?;
        let result = value
            .get(&key)
            .or_else(|| value.get("req_0"))
            .cloned()
            .unwrap_or(value);
        if result["code"].as_i64().is_some_and(|c| c != 0) {
            bail!("QQ 接口拒绝请求，请检查 Cookie 或稍后重试")
        }
        Ok(result)
    }
    pub async fn resolve(&self, record: &TrackRecord, quality: Option<&str>) -> Result<Asset> {
        let track = &record.track;
        let mut expected_duration = track.duration;
        let chosen = match quality {
            Some(id) => track
                .qualities
                .iter()
                .find(|q| q.id == id)
                .ok_or_else(|| anyhow::anyhow!("所选音质不属于这首曲目"))?
                .clone(),
            None => track
                .qualities
                .iter()
                .filter(|q| !q.lossless && ["mp3", "m4a", "ogg"].contains(&q.codec.as_str()))
                .min_by_key(|q| {
                    (
                        if q.codec == "mp3" { 0 } else { 1 },
                        q.bitrate.unwrap_or(u32::MAX),
                    )
                })
                .cloned()
                .or_else(|| track.qualities.first().cloned())
                .unwrap_or_else(|| Quality::new("playback", "mp3", None, false)),
        };
        if chosen.availability == "login_required"
            && self.config.cookie.is_empty()
            && quality.is_some()
        {
            bail!("所选音质需要登录，请配置音源 Cookie")
        }
        let mut result = match self.id.as_str() {
            "migu" => {
                let flag = if chosen.id == "playback" {
                    "PQ"
                } else {
                    chosen.id.as_str()
                };
                let copyright = first(&record.raw, &["copyrightId", "copyrightid"]);
                let response = self
                    .request(
                        reqwest::Method::GET,
                        "https://c.musicapp.migu.cn/strategy/listen-url/h5/v2.4",
                    )
                    .header("signature", "1")
                    .header("birth", "h5page")
                    .query(&[
                        ("contentId", track.source_id.as_str()),
                        ("copyrightId", copyright.as_str()),
                        (
                            "resourceType",
                            record.raw["resourceType"].as_str().unwrap_or("2"),
                        ),
                        ("netType", "01"),
                        ("toneFlag", flag),
                        ("scene", ""),
                        ("lowerQualityContentId", track.source_id.as_str()),
                    ])
                    .send()
                    .await?;
                if !response.status().is_success() {
                    bail!("咪咕资源接口返回 HTTP {}", response.status().as_u16())
                }
                let bytes = http::limited_bytes(response, 512 * 1024).await?;
                let value = migu_json(&bytes)?;
                expected_duration = n(&value["data"]["song"]["duration"]);
                let url = s(&value["data"]["url"]);
                if url.is_empty() {
                    bail!("咪咕未提供可用地址，请检查账号权限")
                }
                Asset {
                    url,
                    codec: chosen.codec.clone(),
                    bitrate: chosen.bitrate,
                    preview_only: false,
                    size: None,
                    duration: None,
                    referer: "https://h5.nf.migu.cn/".into(),
                }
            }
            "netease" => {
                let level = if chosen.id == "playback" {
                    "exhigh"
                } else {
                    chosen.id.as_str()
                };
                let payload = json!({"ids":[track.source_id.parse::<u64>()?],"level":level,"encodeType":"flac","header":json!({"os":"pc","appver":"","osver":"","deviceId":"musicdl-native","requestId":now().to_string()}).to_string()});
                let response = self
                    .request(
                        reqwest::Method::POST,
                        "https://interface3.music.163.com/eapi/song/enhance/player/url/v1",
                    )
                    .header("Cookie", format!("os=pc; appver=; {};", self.config.cookie))
                    .form(&[("params", eapi("/api/song/enhance/player/url/v1", &payload))])
                    .send()
                    .await?;
                let value = http::json(response).await?;
                let item = &value["data"][0];
                let url = s(&item["url"]);
                if url.is_empty() {
                    bail!("网易云未提供资源，可能需要账号权限")
                }
                let preview = !item["freeTrialInfo"].is_null();
                if quality.is_some() && preview {
                    bail!("当前账号仅有试听片段权限，不能下载完整曲目")
                }
                let codec = first(item, &["type", "encodeType"]);
                Asset {
                    url,
                    codec: if codec.is_empty() {
                        chosen.codec.clone()
                    } else {
                        codec.to_lowercase()
                    },
                    bitrate: Some((n(&item["br"]) / 1000.) as u32),
                    preview_only: preview,
                    size: item["size"].as_u64(),
                    duration: None,
                    referer: "https://music.163.com/".into(),
                }
            }
            "qq" => {
                let prefix = if chosen.id == "playback" {
                    "M500"
                } else {
                    chosen.id.as_str()
                };
                let media = first(&record.raw["file"], &["media_mid"]);
                let media = if media.is_empty() {
                    track.source_id.clone()
                } else {
                    media
                };
                let filename = format!("{prefix}{media}{media}.{}", chosen.codec);
                let value=self.qq_call("vkey.GetVkeyServer","CgiGetVkey",json!({"guid":now().to_string(),"songmid":[track.source_id],"songtype":[0],"uin":cookie(&self.config.cookie,"uin").unwrap_or("0"),"loginflag":1,"platform":"20","filename":[filename]})).await?;
                let path = s(&value["data"]["midurlinfo"][0]["purl"]);
                if path.is_empty() {
                    bail!("QQ 未提供所选版本，请配置有权限的 Cookie")
                }
                let base = value["data"]["sip"][0]
                    .as_str()
                    .unwrap_or("https://isure.stream.qqmusic.qq.com/");
                Asset {
                    url: url::Url::parse(base)?.join(&path)?.to_string(),
                    codec: chosen.codec.clone(),
                    bitrate: chosen.bitrate,
                    preview_only: false,
                    size: None,
                    duration: None,
                    referer: "https://y.qq.com/".into(),
                }
            }
            "kuwo" => {
                let response = self
                    .request(reqwest::Method::GET, "https://antiserver.kuwo.cn/anti.s")
                    .query(&[
                        ("type", "convert_url3"),
                        ("rid", track.source_id.as_str()),
                        ("format", chosen.codec.as_str()),
                        ("response", "url"),
                        (
                            "br",
                            if chosen.id == "playback" {
                                "320kmp3"
                            } else {
                                chosen.id.as_str()
                            },
                        ),
                    ])
                    .send()
                    .await?;
                if !response.status().is_success() {
                    bail!("酷我资源接口返回 HTTP {}", response.status().as_u16())
                }
                let text =
                    String::from_utf8_lossy(&http::limited_bytes(response, 32 * 1024).await?)
                        .trim()
                        .to_string();
                let url = if text.starts_with("http") {
                    text
                } else {
                    serde_json::from_str::<Value>(&text)
                        .ok()
                        .and_then(|v| v["url"].as_str().map(str::to_owned))
                        .unwrap_or_default()
                };
                if url.is_empty() {
                    bail!("酷我未提供可用资源，请检查账号权限")
                }
                Asset {
                    url,
                    codec: chosen.codec.clone(),
                    bitrate: chosen.bitrate,
                    preview_only: false,
                    size: None,
                    duration: None,
                    referer: "https://www.kuwo.cn/".into(),
                }
            }
            "qianqian" => {
                let value = http::json(
                    self.request(
                        reqwest::Method::GET,
                        "https://music.91q.com/v1/song/tracklink",
                    )
                    .query(&qianqian_sign([
                        ("TSID", track.source_id.clone()),
                        ("appid", "16073360".into()),
                        (
                            "rate",
                            if chosen.id == "playback" {
                                "320".into()
                            } else {
                                chosen.id.clone()
                            },
                        ),
                    ]))
                    .send()
                    .await?,
                )
                .await?;
                let full = s(&value["data"]["path"]);
                let preview = full.is_empty();
                let url = if preview {
                    s(&value["data"]["trail_audio_info"]["path"])
                } else {
                    full
                };
                if url.is_empty() {
                    bail!("千千未提供资源，请检查账号权限")
                }
                if quality.is_some() && preview {
                    bail!("当前账号只允许试听片段")
                }
                Asset {
                    url,
                    codec: chosen.codec.clone(),
                    bitrate: chosen.bitrate,
                    preview_only: preview,
                    size: None,
                    duration: None,
                    referer: "https://music.91q.com/player".into(),
                }
            }
            _ => bail!("音源尚未迁移"),
        };
        http::validate_url(&result.url)?;
        // Probe a bounded prefix only; a server ignoring Range must not download the whole song.
        let response = self
            .client
            .get(&result.url)
            .header("Referer", &result.referer)
            .header("Range", "bytes=0-8191")
            .header("Accept-Encoding", "identity")
            .timeout(Duration::from_secs(4))
            .send()
            .await?;
        if !response.status().is_success() {
            bail!("资源探测返回 HTTP {}", response.status().as_u16())
        }
        let content_type = response
            .headers()
            .get("content-type")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_owned();
        let total = response
            .headers()
            .get("content-range")
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.rsplit('/').next())
            .and_then(|v| v.parse::<u64>().ok())
            .or_else(|| {
                if response.status().as_u16() == 200 {
                    response.content_length()
                } else {
                    None
                }
            });
        let mut response = response;
        let mut prefix = Vec::new();
        while prefix.len() < 8192 {
            match response.chunk().await? {
                Some(chunk) => {
                    let count = chunk.len().min(8192 - prefix.len());
                    prefix.extend_from_slice(&chunk[..count])
                }
                None => break,
            }
        }
        let actual = http::detect_codec(&prefix, &content_type)
            .ok_or_else(|| anyhow::anyhow!("资源未通过音频格式验证"))?;
        if quality.is_some() && chosen.lossless && actual != "flac" {
            bail!("音源返回的资源不是所选无损格式，已禁止降级")
        }
        let (duration, bitrate) = http::audio_info(&prefix, total.or(result.size));
        http::validate_duration(expected_duration, duration, result.preview_only)?;
        if quality.is_some()
            && actual == "mp3"
            && chosen
                .bitrate
                .is_some_and(|wanted| bitrate.is_some_and(|actual| actual + 8 < wanted))
        {
            bail!("音源返回的码率低于所选版本，已禁止降级")
        }
        result.bitrate = bitrate.or(result.bitrate);
        result.duration = duration.or((expected_duration > 0.).then_some(expected_duration));
        result.codec = actual.into();
        result.size = total.or(result.size);
        Ok(result)
    }
    pub async fn lyrics(&self, record: &TrackRecord) -> Result<String> {
        match self.id.as_str() {
            "migu" | "qianqian" => {
                let mut url = first(&record.raw, &["lyricUrl", "lyric"]);
                if url.is_empty() && self.id == "migu" {
                    let value = http::json(
                        self.request(
                            reqwest::Method::GET,
                            "https://app.c.nf.migu.cn/MIGUM3.0/strategy/pc/listen/v1.0",
                        )
                        .query(&[
                            ("scene", ""),
                            ("netType", "01"),
                            ("resourceType", "2"),
                            (
                                "copyrightId",
                                record.raw["copyrightId"].as_str().unwrap_or(""),
                            ),
                            ("contentId", record.track.source_id.as_str()),
                            ("toneFlag", "PQ"),
                        ])
                        .send()
                        .await?,
                    )
                    .await?;
                    url = s(&value["data"]["lrcUrl"]);
                }
                if url.is_empty() {
                    return Ok(String::new());
                }
                http::validate_url(&url)?;
                let response = self.client.get(url).send().await?;
                if !response.status().is_success() {
                    bail!("歌词获取失败")
                };
                Ok(
                    String::from_utf8_lossy(&http::limited_bytes(response, 256 * 1024).await?)
                        .to_string(),
                )
            }
            "netease" => {
                let value = http::json(
                    self.request(
                        reqwest::Method::POST,
                        "https://interface3.music.163.com/api/song/lyric",
                    )
                    .form(&[
                        ("id", record.track.source_id.as_str()),
                        ("lv", "-1"),
                        ("kv", "-1"),
                        ("tv", "-1"),
                    ])
                    .send()
                    .await?,
                )
                .await?;
                Ok(s(&value["lrc"]["lyric"]))
            }
            "qq" => {
                let value = http::json(
                    self.request(
                        reqwest::Method::GET,
                        "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg",
                    )
                    .query(&[
                        ("songmid", record.track.source_id.as_str()),
                        ("format", "json"),
                        ("nobase64", "1"),
                    ])
                    .send()
                    .await?,
                )
                .await?;
                let text = s(&value["lyric"]);
                if text.contains('[') {
                    Ok(text)
                } else {
                    Ok(
                        String::from_utf8_lossy(&STANDARD.decode(text).unwrap_or_default())
                            .to_string(),
                    )
                }
            }
            "kuwo" => {
                let value = http::json(
                    self.request(
                        reqwest::Method::GET,
                        "https://m.kuwo.cn/newh5/singles/songinfoandlrc",
                    )
                    .query(&[("musicId", record.track.source_id.as_str())])
                    .send()
                    .await?,
                )
                .await?;
                Ok(value["data"]["lrclist"]
                    .as_array()
                    .map(|list| {
                        list.iter()
                            .map(|line| {
                                let sec = n(&line["time"]);
                                format!(
                                    "[{:02}:{:05.2}]{}",
                                    (sec / 60.) as u64,
                                    sec % 60.,
                                    s(&line["lineLyric"])
                                )
                            })
                            .collect::<Vec<_>>()
                            .join("\n")
                    })
                    .unwrap_or_default())
            }
            _ => Ok(String::new()),
        }
    }
    pub async fn playlist(&self, id: &str, limit: usize) -> Result<Vec<TrackRecord>> {
        if self.id == "qianqian" {
            let mut tracks = vec![];
            for page in 1..=20 {
                let value = http::json(
                    self.request(
                        reqwest::Method::GET,
                        "https://music.91q.com/v1/tracklist/info",
                    )
                    .query(&qianqian_sign([
                        ("pageNo", page.to_string()),
                        ("pageSize", "50".into()),
                        ("appid", "16073360".into()),
                        ("id", id.into()),
                    ]))
                    .send()
                    .await?,
                )
                .await?;
                let list = value["data"]["trackList"]
                    .as_array()
                    .ok_or_else(|| anyhow::anyhow!("歌单不可访问或需要登录"))?;
                tracks.extend(
                    list.iter()
                        .filter_map(|v| parse_track("qianqian", v.clone())),
                );
                if tracks.len() >= limit
                    || list.len() < 50
                    || n(&value["data"]["trackCount"]) <= tracks.len() as f64
                {
                    break;
                }
            }
            tracks.truncate(limit);
            return Ok(tracks);
        }
        let value = match self.id.as_str() {
            "netease" => {
                http::json(
                    self.request(
                        reqwest::Method::POST,
                        "https://music.163.com/api/v6/playlist/detail",
                    )
                    .form(&[("id", id), ("n", "1000"), ("s", "0")])
                    .send()
                    .await?,
                )
                .await?
            }
            "qq" => {
                http::json(
                    self.request(
                        reqwest::Method::GET,
                        "https://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg",
                    )
                    .query(&[
                        ("disstid", id),
                        ("type", "1"),
                        ("json", "1"),
                        ("utf8", "1"),
                        ("onlysong", "0"),
                        ("format", "json"),
                    ])
                    .send()
                    .await?,
                )
                .await?
            }
            "migu" => {
                http::json(
                    self.request(
                        reqwest::Method::GET,
                        "https://app.c.nf.migu.cn/MIGUM3.0/resource/playlist/song/v2.0",
                    )
                    .query(&[("pageNo", "1"), ("pageSize", "1000"), ("playlistId", id)])
                    .send()
                    .await?,
                )
                .await?
            }
            "kuwo" => {
                http::json(
                    self.request(
                        reqwest::Method::GET,
                        "https://www.kuwo.cn/api/www/playlist/playListInfo",
                    )
                    .query(&[("pid", id), ("pn", "1"), ("rn", "1000")])
                    .send()
                    .await?,
                )
                .await?
            }
            _ => bail!("该音源的歌单导入尚未支持"),
        };
        if self.id == "netease"
            && let Some(ids) = value["playlist"]["trackIds"].as_array()
            && ids.len() > value["playlist"]["tracks"].as_array().map_or(0, Vec::len)
        {
            let mut tracks = vec![];
            for batch in ids.iter().take(limit).collect::<Vec<_>>().chunks(200) {
                let c = Value::Array(batch.iter().map(|v| json!({"id":v["id"]})).collect());
                let detail = http::json(
                    self.request(
                        reqwest::Method::POST,
                        "https://music.163.com/api/v3/song/detail",
                    )
                    .form(&[("c", c.to_string())])
                    .send()
                    .await?,
                )
                .await?;
                let songs = detail["songs"]
                    .as_array()
                    .ok_or_else(|| anyhow::anyhow!("部分歌单曲目不可访问"))?;
                tracks.extend(
                    songs
                        .iter()
                        .filter_map(|v| parse_track("netease", v.clone())),
                );
            }
            return Ok(tracks);
        }
        let items = match self.id.as_str() {
            "netease" => &value["playlist"]["tracks"],
            "qq" => &value["cdlist"][0]["songlist"],
            "migu" => &value["data"]["songList"],
            _ => &value["data"]["musicList"],
        };
        let list = items
            .as_array()
            .ok_or_else(|| anyhow::anyhow!("歌单不可访问或需要登录"))?;
        Ok(list
            .iter()
            .take(limit)
            .filter_map(|raw| parse_track(&self.id, raw.clone()))
            .collect())
    }
}

pub fn parse_track(source: &str, raw: Value) -> Option<TrackRecord> {
    let (id, title, artists, album, duration, cover, qualities) = match source {
        "migu" => {
            let mut qualities = Vec::new();
            for field in ["rateFormats", "newRateFormats", "audioFormats"] {
                if let Some(items) = raw[field].as_array() {
                    for item in items {
                        let flag = first(item, &["formatType"]);
                        let (codec, rate, lossless) = match flag.as_str() {
                            "LQ" => ("mp3", Some(128), false),
                            "PQ" => ("mp3", Some(320), false),
                            "HQ" => ("mp3", Some(320), false),
                            "SQ" | "ZQ" | "ZQ24" | "ZQ32" => ("flac", None, true),
                            _ => continue,
                        };
                        if !qualities.iter().any(|q: &Quality| q.id == flag) {
                            qualities.push(Quality::new(&flag, codec, rate, lossless))
                        }
                    }
                }
            }
            let image = raw["imgItems"]
                .as_array()
                .and_then(|list| list.last())
                .map(|v| s(&v["img"]))
                .unwrap_or_else(|| first(&raw, &["img3", "img2", "img1", "pic"]));
            (
                first(&raw, &["contentId", "id"]),
                first(&raw, &["name", "songName"]),
                artist(
                    raw.get("singers")
                        .or_else(|| raw.get("singerList"))
                        .unwrap_or(&Value::Null),
                    &["name"],
                ),
                if raw["album"].is_string() {
                    s(&raw["album"])
                } else {
                    artist(&raw["albums"], &["name"])
                },
                n(&raw["duration"]),
                optional(image),
                qualities,
            )
        }
        "netease" => {
            let mut qualities = Vec::new();
            for (key, id, rate, lossless) in [
                ("sq", "lossless", None, true),
                ("h", "exhigh", Some(320), false),
                ("m", "higher", Some(192), false),
                ("l", "standard", Some(128), false),
            ] {
                if raw[key].is_object() {
                    let mut q =
                        Quality::new(id, if lossless { "flac" } else { "mp3" }, rate, lossless);
                    if n(&raw["fee"]) > 0. && n(&raw["privilege"]["pl"]) == 0. {
                        q.availability = "login_required".into()
                    }
                    qualities.push(q)
                }
            }
            (
                s(&raw["id"]),
                s(&raw["name"]),
                artist(
                    raw.get("ar")
                        .or_else(|| raw.get("artists"))
                        .unwrap_or(&Value::Null),
                    &["name"],
                ),
                first(
                    raw.get("al")
                        .or_else(|| raw.get("album"))
                        .unwrap_or(&Value::Null),
                    &["name"],
                ),
                n(raw
                    .get("dt")
                    .or_else(|| raw.get("duration"))
                    .unwrap_or(&Value::Null))
                    / 1000.,
                optional(first(
                    raw.get("al")
                        .or_else(|| raw.get("album"))
                        .unwrap_or(&Value::Null),
                    &["picUrl"],
                )),
                qualities,
            )
        }
        "qq" => {
            let file = &raw["file"];
            let mut qualities = Vec::new();
            for (field, id, codec, rate, lossless) in [
                ("size_flac", "F000", "flac", None, true),
                ("size_320mp3", "M800", "mp3", Some(320), false),
                ("size_128mp3", "M500", "mp3", Some(128), false),
                ("size_192aac", "C600", "m4a", Some(192), false),
            ] {
                if n(&file[field]) > 0. {
                    qualities.push(Quality::new(id, codec, rate, lossless))
                }
            }
            let album_mid = first(&raw["album"], &["mid"]);
            let image = if album_mid.is_empty() {
                None
            } else {
                Some(format!(
                    "https://y.gtimg.cn/music/photo_new/T002R300x300M000{album_mid}.jpg"
                ))
            };
            (
                first(&raw, &["mid", "songmid"]),
                first(&raw, &["title", "songname", "name"]),
                artist(&raw["singer"], &["name"]),
                first(&raw["album"], &["title", "name"])
                    .chars()
                    .collect::<String>(),
                n(&raw["interval"]),
                image,
                qualities,
            )
        }
        "kuwo" => {
            let mut qualities = Vec::new();
            for info in s(&raw["MINFO"]).split(';') {
                let format = info
                    .split(',')
                    .find_map(|item| item.strip_prefix("format:"));
                let rate = info
                    .split(',')
                    .find_map(|item| item.strip_prefix("bitrate:"))
                    .and_then(|s| s.parse::<u32>().ok());
                if let Some(codec) = format
                    && ["flac", "mp3", "ogg", "aac"].contains(&codec)
                {
                    let id = if codec == "flac" {
                        "2000kflac".to_owned()
                    } else {
                        format!("{}k{codec}", rate.unwrap_or(320))
                    };
                    qualities.push(Quality::new(
                        &id,
                        codec,
                        if codec == "flac" { None } else { rate },
                        codec == "flac",
                    ))
                }
            }
            let image = first(&raw, &["pic", "albumpic", "hts_MVPIC"]);
            (
                first(&raw, &["MUSICRID", "musicrid", "rid"])
                    .trim_start_matches("MUSIC_")
                    .to_owned(),
                first(&raw, &["SONGNAME", "name", "songName"]),
                first(&raw, &["ARTIST", "artist"]),
                first(&raw, &["ALBUM", "album"]),
                n(raw
                    .get("DURATION")
                    .or_else(|| raw.get("duration"))
                    .unwrap_or(&Value::Null)),
                optional(image),
                qualities,
            )
        }
        "qianqian" => {
            let mut qualities = Vec::new();
            if let Some(rates) = raw["rate"].as_array() {
                for rate in rates {
                    let code = s(rate);
                    if let Ok(number) = code.parse::<u32>() {
                        qualities.push(Quality::new(
                            &code,
                            if number >= 1000 { "flac" } else { "mp3" },
                            if number >= 1000 { None } else { Some(number) },
                            number >= 1000,
                        ))
                    }
                }
            }
            (
                s(&raw["TSID"]),
                s(&raw["title"]),
                artist(&raw["artist"], &["name"]),
                s(&raw["albumTitle"]),
                n(&raw["duration"]),
                optional(s(&raw["pic"])),
                qualities,
            )
        }
        _ => return None,
    };
    if id.is_empty() || title.is_empty() {
        return None;
    }
    Some(TrackRecord::new(
        source,
        id,
        clean_text(&title),
        clean_text(&artists),
        clean_text(&album),
        duration,
        cover,
        qualities,
        raw,
    ))
}
fn clean_text(s: &str) -> String {
    s.replace("&amp;", "&")
        .replace("&quot;", "\"")
        .replace("&apos;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
}
fn cookie<'a>(text: &'a str, key: &str) -> Option<&'a str> {
    text.split(';').find_map(|part| {
        let (name, value) = part.trim().split_once('=')?;
        (name == key).then_some(value)
    })
}
fn qianqian_sign<const N: usize>(pairs: [(&str, String); N]) -> BTreeMap<String, String> {
    let mut params: BTreeMap<String, String> =
        pairs.into_iter().map(|(k, v)| (k.into(), v)).collect();
    params.insert("timestamp".into(), now().to_string());
    let raw = params
        .iter()
        .map(|(k, v)| format!("{k}={v}"))
        .collect::<Vec<_>>()
        .join("&")
        + "0b50b02fd0d73a9c4c8c3a781c30845f";
    params.insert("sign".into(), hex::encode(Md5::digest(raw)));
    params
}
pub fn eapi(path: &str, payload: &Value) -> String {
    let data = payload.to_string();
    let digest = hex::encode(Md5::digest(format!("nobody{path}use{data}md5forencrypt")));
    let mut bytes = format!("{path}-36cd479b6b5-{data}-36cd479b6b5-{digest}").into_bytes();
    let pad = 16 - bytes.len() % 16;
    bytes.extend(std::iter::repeat_n(pad as u8, pad));
    let cipher = Aes128::new(GenericArray::from_slice(b"e82ckenh8dichen8"));
    for block in bytes.chunks_mut(16) {
        cipher.encrypt_block(GenericArray::from_mut_slice(block))
    }
    hex::encode(bytes)
}
fn migu_json(bytes: &[u8]) -> Result<Value> {
    if bytes.starts_with(&[0xab, 0xcd, 0x01]) && bytes.len() > 4 {
        let key = b"Jk8qzuePiJ1qE3mDYhLQ3T73DtDoAhLP";
        let seed = bytes[3];
        let plain: Vec<_> = bytes[4..]
            .iter()
            .enumerate()
            .map(|(i, b)| b.wrapping_add(seed).wrapping_sub(key[i % key.len()]))
            .collect();
        Ok(serde_json::from_slice(&plain)?)
    } else {
        Ok(serde_json::from_slice(bytes)?)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn metadata_qualities_are_evidence_not_resolved_resources() {
        let record=parse_track("netease",json!({"id":1,"name":"曲目","ar":[{"name":"歌手"}],"al":{"name":"专辑"},"dt":120000,"sq":{"size":20000000},"h":{"br":320000},"fee":1,"privilege":{"pl":0}})).unwrap();
        assert_eq!(record.track.qualities.len(), 2);
        assert!(record.track.qualities[0].lossless);
        assert_eq!(record.track.qualities[0].availability, "login_required");
        assert_eq!(record.track.playback.state, "pending");
        assert!(record.track.playback.url.is_none());
        let unknown = parse_track("qianqian", json!({"TSID":"x","title":"曲目"})).unwrap();
        assert!(unknown.track.qualities.is_empty());
    }
    #[test]
    fn eapi_padding_and_sign_are_repeatable() {
        let value = json!({"ids":[1],"level":"standard"});
        let encrypted = eapi("/api/song/enhance/player/url/v1", &value);
        assert_eq!(encrypted.len() % 32, 0);
        assert_eq!(encrypted, eapi("/api/song/enhance/player/url/v1", &value));
        assert_ne!(
            encrypted,
            eapi("/api/song/enhance/player/url/v1", &json!({"ids":[2]}))
        );
    }
}
