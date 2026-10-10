use crate::model::SourceConfig;
use anyhow::{Result, bail};
use reqwest::{
    Client,
    dns::{Name, Resolve, Resolving},
    redirect::Policy,
};
use std::{
    net::{IpAddr, SocketAddr},
    sync::Arc,
    time::Duration,
};
use url::Url;

pub fn public_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(ip) => {
            !(ip.is_private()
                || ip.is_loopback()
                || ip.is_link_local()
                || ip.is_unspecified()
                || ip.is_broadcast()
                || ip.is_multicast()
                || ip.octets()[0] == 0
                || ip.octets()[0] >= 240
                || (ip.octets()[0] == 100 && (64..=127).contains(&ip.octets()[1]))
                || ip.is_documentation())
        }
        IpAddr::V6(ip) => {
            if let Some(v4) = ip.to_ipv4_mapped() {
                public_ip(IpAddr::V4(v4))
            } else {
                ip.segments()[0] & 0xe000 == 0x2000
                    && !(ip.segments()[0] == 0x2001
                        && (ip.segments()[1] < 0x0200 || ip.segments()[1] == 0x0db8))
                    && ip.segments()[0] != 0x2002
            }
        }
    }
}
pub fn validate_url(raw: &str) -> Result<Url> {
    let url = Url::parse(raw)?;
    if !["http", "https"].contains(&url.scheme())
        || !url.username().is_empty()
        || url.password().is_some()
    {
        bail!("资源地址格式无效")
    }
    let host = url
        .host_str()
        .ok_or_else(|| anyhow::anyhow!("资源地址缺少主机"))?;
    if host.eq_ignore_ascii_case("localhost")
        || host.ends_with(".localhost")
        || host.ends_with(".local")
    {
        bail!("禁止访问本地资源地址")
    }
    if let Some(url::Host::Ipv4(ip)) = url.host()
        && !public_ip(IpAddr::V4(ip))
    {
        bail!("禁止访问内网资源地址")
    }
    if let Some(url::Host::Ipv6(ip)) = url.host()
        && !public_ip(IpAddr::V6(ip))
    {
        bail!("禁止访问内网资源地址")
    }
    Ok(url)
}
#[derive(Debug)]
struct PublicResolver;
impl Resolve for PublicResolver {
    fn resolve(&self, name: Name) -> Resolving {
        Box::pin(async move {
            let addresses: Vec<SocketAddr> =
                tokio::net::lookup_host((name.as_str(), 0)).await?.collect();
            if addresses.is_empty() || addresses.iter().any(|a| !public_ip(a.ip())) {
                return Err(std::io::Error::other("DNS 指向非公网地址").into());
            }
            Ok(Box::new(addresses.into_iter()) as reqwest::dns::Addrs)
        })
    }
}
pub fn client(config: &SourceConfig, timeout: u64) -> Result<Client> {
    let mut builder = Client::builder().user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/134.0.0.0 Safari/537.36")
        .connect_timeout(Duration::from_secs(4)).timeout(Duration::from_secs(timeout)).pool_idle_timeout(Duration::from_secs(90))
        .dns_resolver(Arc::new(PublicResolver)).redirect(Policy::custom(|attempt| {
            if attempt.previous().len() >= 5 { attempt.error("重定向次数过多") }
            else if validate_url(attempt.url().as_str()).is_err() { attempt.error("资源重定向到禁止地址") }
            else { attempt.follow() }
        }));
    if !config.proxy.is_empty() {
        builder = builder.proxy(reqwest::Proxy::all(&config.proxy)?);
    }
    Ok(builder.build()?)
}
pub async fn json(response: reqwest::Response) -> Result<serde_json::Value> {
    if !response.status().is_success() {
        bail!("音源返回 HTTP {}", response.status().as_u16())
    }
    let body = limited_bytes(response, 4 * 1024 * 1024).await?;
    serde_json::from_slice(&body).map_err(|_| anyhow::anyhow!("音源返回了无法解析的数据"))
}
pub async fn limited_bytes(mut response: reqwest::Response, max: usize) -> Result<Vec<u8>> {
    let mut out = Vec::new();
    while let Some(chunk) = response.chunk().await? {
        if out.len() + chunk.len() > max {
            bail!("响应超过大小限制")
        }
        out.extend_from_slice(&chunk);
    }
    Ok(out)
}
pub fn detect_codec(bytes: &[u8], _content_type: &str) -> Option<&'static str> {
    if bytes.starts_with(b"fLaC") {
        Some("flac")
    } else if bytes.starts_with(b"ID3")
        || bytes.len() >= 2 && bytes[0] == 0xff && bytes[1] & 0xe0 == 0xe0
    {
        Some("mp3")
    } else if bytes.starts_with(b"OggS") {
        Some("ogg")
    } else if bytes.len() >= 12 && &bytes[4..8] == b"ftyp" {
        Some("m4a")
    } else if bytes.starts_with(b"RIFF") && bytes.len() >= 12 && &bytes[8..12] == b"WAVE" {
        Some("wav")
    } else {
        None
    }
}

/// Read duration/bitrate from the bounded probe. Claims from metadata are not validation.
pub fn audio_info(bytes: &[u8], size: Option<u64>) -> (Option<f64>, Option<u32>) {
    if bytes.starts_with(b"fLaC") && bytes.len() >= 26 && bytes[4] & 0x7f == 0 {
        let packed = u64::from_be_bytes(bytes[18..26].try_into().unwrap());
        let rate = packed >> 44;
        let samples = packed & 0x0000_000f_ffff_ffff;
        return (
            if rate > 0 && samples > 0 {
                Some(samples as f64 / rate as f64)
            } else {
                None
            },
            None,
        );
    }
    let offset = if bytes.starts_with(b"ID3") && bytes.len() >= 10 {
        10 + bytes[6..10]
            .iter()
            .fold(0usize, |n, b| (n << 7) | usize::from(b & 0x7f))
    } else {
        0
    };
    for i in offset..bytes.len().saturating_sub(3) {
        let b = &bytes[i..];
        if b[0] != 0xff || b[1] & 0xe0 != 0xe0 || b[1] & 6 != 2 {
            continue;
        }
        let version = (b[1] >> 3) & 3;
        let index = usize::from(b[2] >> 4);
        let sample_index = usize::from((b[2] >> 2) & 3);
        if version == 1 || index == 0 || index == 15 || sample_index == 3 {
            continue;
        }
        let rate = if version == 3 {
            [
                0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320,
            ][index]
        } else {
            [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160][index]
        };
        let sample_rate = [44100, 48000, 32000][sample_index]
            / if version == 3 {
                1
            } else if version == 2 {
                2
            } else {
                4
            };
        let side_info = if version == 3 {
            if b[3] >> 6 == 3 { 17 } else { 32 }
        } else if b[3] >> 6 == 3 {
            9
        } else {
            17
        };
        let xing = 4 + side_info + if b[1] & 1 == 0 { 2 } else { 0 };
        if b.len() >= xing + 12 && (&b[xing..xing + 4] == b"Xing" || &b[xing..xing + 4] == b"Info")
        {
            let flags = u32::from_be_bytes(b[xing + 4..xing + 8].try_into().unwrap());
            if flags & 1 != 0 {
                let frames = u32::from_be_bytes(b[xing + 8..xing + 12].try_into().unwrap());
                let duration = f64::from(frames) * if version == 3 { 1152. } else { 576. }
                    / f64::from(sample_rate);
                let bitrate = size.map(|size| (size as f64 * 8. / duration / 1000.).round() as u32);
                return (
                    Some(duration),
                    if &b[xing..xing + 4] == b"Info" {
                        Some(rate)
                    } else {
                        bitrate
                    },
                );
            }
        }
        return (
            size.map(|size| size as f64 * 8. / f64::from(rate) / 1000.),
            Some(rate),
        );
    }
    (None, None)
}
pub fn validate_duration(expected: f64, actual: Option<f64>, declared_preview: bool) -> Result<()> {
    if !declared_preview && expected > 45. && actual.is_some_and(|actual| actual < expected * 0.65)
    {
        bail!("音源返回短片段或提示音，未作为目标曲目提供")
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_private_redirect_targets_and_fake_audio() {
        for url in [
            "http://localhost/a",
            "http://127.0.0.1/a",
            "http://10.0.0.1/a",
            "http://[::ffff:127.0.0.1]/a",
            "file:///a",
            "https://user:pass@example.com/a",
        ] {
            assert!(validate_url(url).is_err(), "{url}")
        }
        assert!(validate_url("https://music.163.com/a").is_ok());
        assert_eq!(detect_codec(b"<html>error", "audio/flac"), None);
        assert_eq!(
            detect_codec(b"fLaCtest", "application/octet-stream"),
            Some("flac")
        );
    }
    #[test]
    fn detects_short_notice_audio_and_actual_bitrate() {
        let header = [0xff, 0xfb, 0x90, 0x00];
        let (dur, rate) = audio_info(&header, Some(181521));
        assert_eq!(rate, Some(128));
        assert!(dur.unwrap() < 12.);
        assert!(validate_duration(226., dur, false).is_err());
        assert!(validate_duration(226., dur, true).is_ok());
        let mut flac = vec![0; 42];
        flac[..4].copy_from_slice(b"fLaC");
        let packed = (44100u64 << 44) | (44100u64 * 180);
        flac[18..26].copy_from_slice(&packed.to_be_bytes());
        assert_eq!(audio_info(&flac, None).0, Some(180.));
    }
}
