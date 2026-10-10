use axum::{
    Json,
    body::Body,
    extract::{Request, State},
    http::{StatusCode, header},
    middleware::Next,
    response::{IntoResponse, Response},
};
use serde::Deserialize;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    sync::Arc,
    time::{Duration, Instant},
};
use subtle::ConstantTimeEq;
use tokio::sync::Mutex;

#[derive(Clone)]
pub struct Auth(Arc<Inner>);
struct Inner {
    user: String,
    password: [u8; 32],
    enabled: bool,
    secure: bool,
    hosts: Vec<String>,
    sessions: Mutex<HashMap<String, Instant>>,
    attempts: Mutex<Vec<Instant>>,
}
impl Auth {
    pub fn new(bind: &str) -> anyhow::Result<Self> {
        let user = std::env::var("MUSICDL_USER").unwrap_or_default();
        let pass = std::env::var("MUSICDL_PASS").unwrap_or_default();
        let local = bind.parse::<std::net::IpAddr>()?.is_loopback();
        anyhow::ensure!(
            local || (!user.is_empty() && pass.len() >= 16),
            "非本地监听必须设置 MUSICDL_USER 和至少 16 字节的 MUSICDL_PASS"
        );
        anyhow::ensure!(
            user.is_empty() == pass.is_empty(),
            "用户名和密码必须同时设置"
        );
        let hosts: Vec<String> = std::env::var("MUSICDL_TRUSTED_HOSTS")
            .unwrap_or_else(|_| "localhost,127.0.0.1,::1".into())
            .split(',')
            .map(|s| s.trim().to_lowercase())
            .filter(|s| !s.is_empty())
            .collect();
        anyhow::ensure!(
            !hosts.is_empty() && !hosts.iter().any(|h| h.contains('*')),
            "MUSICDL_TRUSTED_HOSTS 必须是明确的主机列表"
        );
        let password = Sha256::digest(pass.as_bytes()).into();
        Ok(Self(Arc::new(Inner {
            enabled: !user.is_empty(),
            user,
            password,
            secure: std::env::var("MUSICDL_COOKIE_SECURE").is_ok_and(|v| v == "1"),
            hosts,
            sessions: Mutex::new(HashMap::new()),
            attempts: Mutex::new(vec![]),
        })))
    }
    async fn valid(&self, headers: &axum::http::HeaderMap) -> bool {
        if !self.0.enabled {
            return true;
        }
        let token = session(headers);
        let mut sessions = self.0.sessions.lock().await;
        sessions.retain(|_, time| time.elapsed() < Duration::from_secs(86400));
        sessions.contains_key(&token)
    }
}
fn session(headers: &axum::http::HeaderMap) -> String {
    headers
        .get(header::COOKIE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .split(';')
        .find_map(|part| part.trim().strip_prefix("musicdl_session="))
        .unwrap_or("")
        .to_owned()
}
pub async fn gate(State(auth): State<Auth>, request: Request, next: Next) -> Response {
    let host = request
        .headers()
        .get(header::HOST)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let normalized = if host.starts_with('[') {
        host.split(']').next().unwrap_or("").trim_start_matches('[')
    } else {
        host.split(':').next().unwrap_or("")
    }
    .to_lowercase();
    if !auth.0.hosts.iter().any(|h| h == "*" || h == &normalized) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail":"访问主机未获许可，请设置 MUSICDL_TRUSTED_HOSTS"})),
        )
            .into_response();
    }
    let path = request.uri().path();
    let public = matches!(path, "/api/auth/status" | "/api/auth/login" | "/api/health")
        || !path.starts_with("/api/");
    if !public && !auth.valid(request.headers()).await {
        return (StatusCode::UNAUTHORIZED, Json(json!({"detail":"请先登录"}))).into_response();
    }
    if !matches!(
        *request.method(),
        axum::http::Method::GET | axum::http::Method::HEAD | axum::http::Method::OPTIONS
    ) && request
        .headers()
        .get("x-musicdl-request")
        .is_none_or(|v| v != "1")
    {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail":"请求校验失败，请刷新页面"})),
        )
            .into_response();
    }
    let mut response = next.run(request).await;
    response.headers_mut().insert(
        "x-content-type-options",
        header::HeaderValue::from_static("nosniff"),
    );
    response.headers_mut().insert(
        "referrer-policy",
        header::HeaderValue::from_static("no-referrer"),
    );
    response.headers_mut().insert("content-security-policy",header::HeaderValue::from_static("default-src 'self'; img-src 'self' https: http: data:; style-src 'self' 'unsafe-inline'; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'; object-src 'none'"));
    response
}
pub async fn status(
    State(auth): State<Auth>,
    headers: axum::http::HeaderMap,
) -> Json<serde_json::Value> {
    Json(json!({"required":auth.0.enabled,"authenticated":auth.valid(&headers).await}))
}
#[derive(Deserialize)]
pub struct Login {
    username: String,
    password: String,
}
pub async fn login(State(auth): State<Auth>, Json(login): Json<Login>) -> Response {
    let mut attempts = auth.0.attempts.lock().await;
    attempts.retain(|t| t.elapsed() < Duration::from_secs(60));
    if attempts.len() >= 10 {
        return (
            StatusCode::TOO_MANY_REQUESTS,
            Json(json!({"detail":"登录尝试过多，请一分钟后重试"})),
        )
            .into_response();
    }
    attempts.push(Instant::now());
    drop(attempts);
    let supplied: [u8; 32] = Sha256::digest(login.password.as_bytes()).into();
    if !auth.0.enabled
        || login.username != auth.0.user
        || !bool::from(supplied.ct_eq(&auth.0.password))
    {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail":"用户名或密码错误"})),
        )
            .into_response();
    }
    let token = format!(
        "{}{}",
        uuid::Uuid::new_v4().simple(),
        uuid::Uuid::new_v4().simple()
    );
    let mut sessions = auth.0.sessions.lock().await;
    sessions.retain(|_, t| t.elapsed() < Duration::from_secs(86400));
    if sessions.len() >= 64 {
        sessions.clear()
    }
    sessions.insert(token.clone(), Instant::now());
    let cookie = format!(
        "musicdl_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=86400{}",
        if auth.0.secure { "; Secure" } else { "" }
    );
    (
        [(header::SET_COOKIE, cookie)],
        Json(json!({"authenticated":true})),
    )
        .into_response()
}
pub async fn logout(State(auth): State<Auth>, headers: axum::http::HeaderMap) -> Response {
    auth.0.sessions.lock().await.remove(&session(&headers));
    let mut response = Response::new(Body::from("{}"));
    response.headers_mut().insert(
        header::SET_COOKIE,
        header::HeaderValue::from_static(
            "musicdl_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0",
        ),
    );
    response
}

#[cfg(test)]
mod tests {
    use super::*;
    use tower::ServiceExt;
    #[tokio::test]
    async fn login_cookie_protects_api_and_rejects_forged_requests() {
        let auth = Auth(Arc::new(Inner {
            user: "test".into(),
            password: Sha256::digest(b"test-password").into(),
            enabled: true,
            secure: false,
            hosts: vec!["localhost".into()],
            sessions: Mutex::new(HashMap::new()),
            attempts: Mutex::new(vec![]),
        }));
        let router = axum::Router::new()
            .route("/api/auth/login", axum::routing::post(login))
            .with_state(auth.clone())
            .route(
                "/api/private",
                axum::routing::get(|| async { "ok" }).post(|| async { "ok" }),
            )
            .layer(axum::middleware::from_fn_with_state(auth, gate));
        let request = |path: &str| {
            axum::http::Request::builder()
                .uri(path)
                .header("host", "localhost")
        };
        assert_eq!(
            router
                .clone()
                .oneshot(request("/api/private").body(Body::empty()).unwrap())
                .await
                .unwrap()
                .status(),
            StatusCode::UNAUTHORIZED
        );
        let response = router
            .clone()
            .oneshot(
                request("/api/auth/login")
                    .method("POST")
                    .header("content-type", "application/json")
                    .header("x-musicdl-request", "1")
                    .body(Body::from(
                        r#"{"username":"test","password":"test-password"}"#,
                    ))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let cookie = response.headers()[header::SET_COOKIE].to_str().unwrap();
        assert!(cookie.contains("HttpOnly") && cookie.contains("SameSite=Strict"));
        let cookie = cookie.split(';').next().unwrap();
        assert_eq!(
            router
                .clone()
                .oneshot(
                    request("/api/private")
                        .header("cookie", cookie)
                        .body(Body::empty())
                        .unwrap()
                )
                .await
                .unwrap()
                .status(),
            StatusCode::OK
        );
        assert_eq!(
            router
                .clone()
                .oneshot(
                    request("/api/private")
                        .method("POST")
                        .header("cookie", cookie)
                        .body(Body::empty())
                        .unwrap()
                )
                .await
                .unwrap()
                .status(),
            StatusCode::FORBIDDEN
        );
        assert_eq!(
            router
                .oneshot(
                    axum::http::Request::builder()
                        .uri("/api/private")
                        .header("host", "attacker.invalid")
                        .header("cookie", cookie)
                        .body(Body::empty())
                        .unwrap()
                )
                .await
                .unwrap()
                .status(),
            StatusCode::BAD_REQUEST
        );
    }
}
