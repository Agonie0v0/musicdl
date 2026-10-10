use musicdl_server::{auth::Auth, downloads, server, state::App, store::Store};
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    dotenvy::dotenv().ok();
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "musicdl_server=info".into()),
        )
        .init();
    let bind = std::env::var("MUSICDL_BIND").unwrap_or_else(|_| "127.0.0.1".into());
    let port = std::env::var("MUSICDL_PORT")
        .unwrap_or_else(|_| "8765".into())
        .parse::<u16>()?;
    let auth = Auth::new(&bind)?;
    let data = std::path::PathBuf::from(
        std::env::var("MUSICDL_DATA_DIR").unwrap_or_else(|_| "data".into()),
    );
    let music = std::path::PathBuf::from(
        std::env::var("MUSICDL_DOWNLOAD_DIR").unwrap_or_else(|_| "music".into()),
    );
    let store = Store::open(&data.join("musicdl.db"))?;
    let legacy = std::env::var("MUSICDL_CONFIG")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| {
            if data.join("config.json").exists() {
                data.join("config.json")
            } else {
                "config.json".into()
            }
        });
    if musicdl_server::migration::legacy_config(&store, &legacy).await? {
        tracing::info!("已迁移旧版常用音源设置；原配置文件未修改");
    }
    let app = App::new(store, music).await?;
    downloads::recover(&app).await?;
    let frontend = std::path::PathBuf::from(
        std::env::var("MUSICDL_FRONTEND_DIR").unwrap_or_else(|_| "frontend/dist".into()),
    );
    anyhow::ensure!(
        frontend.join("index.html").exists(),
        "前端尚未构建：请在 frontend 目录运行 npm ci 和 npm run build"
    );
    let listener = tokio::net::TcpListener::bind((bind.as_str(), port)).await?;
    tracing::info!("musicdl Rust 服务已启动，端口 {}", port);
    axum::serve(listener, server::router(app, auth, &frontend))
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}
