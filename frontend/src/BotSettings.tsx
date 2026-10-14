import { useEffect, useState } from "react";
import { api } from "./api";

type BotConfig = {
  public_url: string;
  link_ttl_seconds: number;
  bot_sources: string;
  tg_enabled: boolean;
  tg_api_url: string;
  tg_local_enabled: boolean;
  tg_local_api_id: string;
  local_api_status: { available: boolean; running: boolean; detail: string };
  tg_proxy_mode: "direct" | "project" | "custom";
  wecom_enabled: boolean;
  tg_allowed_users: string;
  wecom_corp_id: string;
  wecom_agent_id: string;
  wecom_allowed_users: string;
  configured: Record<string, boolean>;
  status: { tg: boolean; wecom: boolean };
  callback_url: string;
  discord_enabled: boolean;
  feishu_enabled: boolean;
  dingtalk_enabled: boolean;
};
const secretFields = [
  "tg_token",
  "tg_proxy",
  "tg_local_api_hash",
  "wecom_secret",
  "wecom_token",
  "wecom_aes_key",
  "discord_webhook",
  "feishu_webhook",
  "feishu_sign_secret",
  "dingtalk_webhook",
  "dingtalk_sign_secret",
];

export function BotSettings() {
  const [config, setConfig] = useState<BotConfig | null>(null);
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [clear, setClear] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [dirty, setDirty] = useState(false);
  const [testResult, setTestResult] = useState<{
    channel: string;
    state: "loading" | "success" | "error";
    detail: string;
  } | null>(null);
  async function load() {
    try {
      setConfig(await api<BotConfig>("/bots/config"));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    void load();
  }, []);
  if (!config)
    return (
      <section className="settings-section">
        <h2>通知与机器人</h2>
        <p role={error ? "alert" : "status"}>
          {error || "正在读取通知渠道配置…"}
        </p>
        {error && (
          <button className="secondary" onClick={() => void load()}>
            重新读取通知配置
          </button>
        )}
      </section>
    );
  function update(key: string, value: string | number | boolean) {
    setConfig((current) => current && { ...current, [key]: value });
    setDirty(true);
    setMessage("");
    setTestResult(null);
  }
  function field(key: keyof BotConfig, label: string, placeholder = "") {
    return (
      <label key={key}>
        {label}
        <input
          value={String(config![key])}
          placeholder={placeholder}
          onChange={(e) => update(key, e.target.value)}
        />
      </label>
    );
  }
  function secret(key: string, label: string) {
    return (
      <div className="bot-secret" key={key}>
        <label>
          {label}
          <input
            type="password"
            autoComplete="new-password"
            value={secrets[key] || ""}
            placeholder={
              config!.configured[key]
                ? "已配置，留空保留；输入新值可替换"
                : "尚未配置"
            }
            disabled={clear.includes(key)}
            onChange={(e) => {
              setSecrets({ ...secrets, [key]: e.target.value });
              setDirty(true);
              setMessage("");
            }}
          />
        </label>
        {config!.configured[key] && (
          <label className="ready-filter">
            <input
              type="checkbox"
              checked={clear.includes(key)}
              onChange={(e) => {
                setClear(
                  e.target.checked
                    ? [...clear, key]
                    : clear.filter((k) => k !== key),
                );
                setDirty(true);
                setMessage("");
              }}
            />
            清除已保存的{label}
          </label>
        )}
      </div>
    );
  }
  function draftPayload() {
    const { configured, status, callback_url, local_api_status, ...values } =
      config!;
    return {
      ...values,
      ...Object.fromEntries(
        secretFields.map((key) => [key, secrets[key] || ""]),
      ),
      clear_secrets: clear,
    };
  }
  async function test(
    channel: "tg" | "wecom" | "discord" | "feishu" | "dingtalk",
  ) {
    setBusy(true);
    setError("");
    setMessage("");
    setTestResult({ channel, state: "loading", detail: "正在检查，请稍候…" });
    try {
      const result = await api<{ detail: string }>("/bots/test", "POST", {
        channel,
        config: draftPayload(),
      });
      setTestResult({ channel, state: "success", detail: result.detail });
    } catch (e) {
      setTestResult({ channel, state: "error", detail: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }
  function feedback(channel: string) {
    return testResult?.channel === channel ? (
      <p
        className={testResult.state === "error" ? "log-error" : "test-result"}
        role={testResult.state === "error" ? "alert" : "status"}
      >
        {testResult.detail}
      </p>
    ) : null;
  }
  return (
    <form
      className="settings-form bot-settings"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError("");
        setMessage("");
        try {
          setConfig(
            await api<BotConfig>("/bots/config", "PUT", draftPayload()),
          );
          setSecrets({});
          setClear([]);
          setDirty(false);
          setMessage("通知渠道已保存并立即生效，无需重启服务");
        } catch (e) {
          setError((e as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <section className="settings-section settings-basics">
        <h2>文件交付与下载链接</h2>
        <p>
          Telegram 和企业微信选择曲目后直接发送原文件。Telegram 的 MP3 / M4A /
          FLAC 优先以音频消息发送，格式被拒绝时改发普通文件。Telegram
          大文件也先尝试上传，接口拒绝大小后再发送下载链接。
        </p>
        <div className="settings-grid">
          {field("public_url", "公网访问地址", "http://192.168.1.20:8765")}
          <label>
            下载链接有效期（秒）
            <input
              type="number"
              min="60"
              max="604800"
              required
              value={config.link_ttl_seconds}
              onChange={(e) =>
                update("link_ttl_seconds", Number(e.target.value))
              }
            />
          </label>
          {field(
            "bot_sources",
            "机器人音源（逗号分隔）",
            "留空跟随 Web 音源设置",
          )}
        </div>
        <p>
          支持 HTTP / HTTPS 域名或 IP，可带端口；省略协议时使用 HTTP。
          请填写手机能访问的地址，企业微信回调还需平台可访问。设置保存在 data
          目录中，并覆盖对应环境变量。
        </p>
      </section>
      <section className="settings-section settings-proxy">
        <h2>使用方式</h2>
        <p>搜索歌名歌手 → 切换音源 → 选择编号下载 → 收到音频或原文件。</p>
        <p>
          Telegram 发送 /start 或 /menu 打开功能菜单，也可直接发送歌名搜索。
          输入框旁的菜单会自动注册；各音源并行搜索，结果每页 5
          首，显示音质和文件大小。切换到未完成的音源时显示搜索中，完成后自动更新。
          /status
          查看进度和管理任务。可发送“下一页”“上一页”“第2页”，下载只需“下载
          1”。Telegram
          仅允许白名单用户私聊；企业微信白名单留空时允许应用可见成员。
          企业微信输入“音源 2”切换音源，发送“下载 1”下载。
        </p>
        <p>
          连接测试使用当前填写的凭据和代理，无需先保存，也不会发送消息。
          保存后机器人配置才会生效；下载和命令处理的错误可在“运行日志 →
          通知与机器人”中查看。
        </p>
      </section>
      <section className="settings-section settings-behavior">
        <h2>
          Telegram 机器人{" "}
          <small>运行状态：{config.status.tg ? "已启用" : "未启用"}</small>
        </h2>
        <label className="ready-filter">
          <input
            type="checkbox"
            checked={config.tg_enabled}
            onChange={(e) => update("tg_enabled", e.target.checked)}
          />
          启用 Telegram
        </label>
        <div className="settings-grid">
          {secret("tg_token", "Telegram Bot Token")}
          {field(
            "tg_allowed_users",
            "Telegram 用户白名单",
            "数字用户 ID，多个用逗号分隔",
          )}
          {!config.tg_local_enabled &&
            field(
              "tg_api_url",
              "Telegram Bot API 地址",
              "https://api.telegram.org",
            )}
          <label>
            Telegram 代理
            <select
              aria-label="Telegram 代理"
              value={config.tg_proxy_mode}
              onChange={(e) => update("tg_proxy_mode", e.target.value)}
            >
              <option value="direct">不使用代理</option>
              <option value="project">使用项目代理</option>
              {config.tg_proxy_mode === "custom" && (
                <option value="custom">保留原有独立代理</option>
              )}
            </select>
          </label>
        </div>
        <label className="ready-filter">
          <input
            type="checkbox"
            checked={Boolean(config.tg_local_enabled)}
            onChange={(e) => update("tg_local_enabled", e.target.checked)}
          />
          使用内置 Telegram 本地服务（大文件上传）
        </label>
        {config.tg_local_enabled && (
          <div className="local-api-settings">
            <p>
              内置服务：{config.local_api_status?.running ? "运行中" : "未启动"}
              。
              {config.local_api_status?.available
                ? "官方程序已就绪。"
                : "当前环境未找到官方程序，请更新 Docker 镜像；源码部署需安装 telegram-bot-api。"}
              {config.local_api_status?.detail}
            </p>
            <div className="settings-grid">
              {field(
                "tg_local_api_id",
                "Telegram API ID",
                "my.telegram.org 的应用 API ID",
              )}
              {secret("tg_local_api_hash", "Telegram API Hash")}
            </div>
            <p>
              在{" "}
              <a
                href="https://my.telegram.org/apps"
                target="_blank"
                rel="noreferrer"
              >
                my.telegram.org
              </a>{" "}
              获取应用凭据，与 Bot Token 不同。
              保存后自动启动官方本地模式服务，上传支持最高 2000
              MB。首次切换会退出原 API 服务；切回云端可能需要等待 Telegram
              规定的 10 分钟。 本地服务需要服务器网络能直连 Telegram，下面的
              HTTP 代理仅用于外部 Bot API 请求。
            </p>
          </div>
        )}
        <p>
          从 BotFather 获取 Token。白名单填写数字用户
          ID；使用长轮询接收命令，请移除该机器人的已有 webhook。
          项目代理地址在“音源与设置”中配置和修改，选择后与音源共用。Telegram
          文档标注云端上传上限为 50 MB，自建 Local Bot API（需以 --local
          启动）为 2000
          MB；项目不按这两个数值提前拦截，均先尝试发送，以接口实际响应为准。
          上传大小、发送方式、成功或拒绝原因均会记录在运行日志中。FLAC
          直接发送原文件，不转码；聊天内能否播放取决于客户端。
        </p>
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={() => void test("tg")}
        >
          测试 Telegram 连接
        </button>
        {feedback("tg")}
      </section>
      <section className="settings-section settings-sources">
        <h2>
          企业微信自建应用{" "}
          <small>运行状态：{config.status.wecom ? "已启用" : "未启用"}</small>
        </h2>
        <label className="ready-filter">
          <input
            type="checkbox"
            checked={config.wecom_enabled}
            onChange={(e) => update("wecom_enabled", e.target.checked)}
          />
          启用企业微信
        </label>
        <div className="settings-grid">
          {field("wecom_corp_id", "企业微信 CorpID")}
          {field("wecom_agent_id", "企业微信 AgentID")}
          {secret("wecom_secret", "企业微信 Secret")}
          {secret("wecom_token", "企业微信回调 Token")}
          {secret("wecom_aes_key", "企业微信 EncodingAESKey")}
          {field(
            "wecom_allowed_users",
            "企业微信用户白名单",
            "留空允许全部成员；限制时填 UserID，逗号分隔",
          )}
        </div>
        <label>
          企业微信回调地址
          <input
            readOnly
            value={
              config.public_url.trim()
                ? (config.public_url.includes("://") ? "" : "http://") +
                  config.public_url.trim().replace(/\/$/, "") +
                  "/bot/wecom"
                : "填写公网访问地址后生成"
            }
          />
        </label>
        <p>
          在应用的“接收消息”中填入回调地址、Token 和 43 位
          EncodingAESKey；配置应用可见成员和企业可信
          IP。保存后可进行平台回调验证。
          白名单留空时允许应用可见范围内的成员，填写后仅允许指定 UserID。
        </p>
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={() => void test("wecom")}
        >
          测试企业微信连接
        </button>
        {feedback("wecom")}
      </section>
      <section className="settings-section settings-sources">
        <h2>更多下载通知渠道</h2>
        <p>
          飞书、钉钉和 Discord 通过群 Webhook
          通知新下载任务的完成、失败与取消，完成时附原文件下载链接。群 Webhook
          仅提供结果通知，搜索和选曲请使用 Telegram、企业微信或 Web。
        </p>
        {(
          [
            ["discord", "Discord"],
            ["feishu", "飞书 / Lark"],
            ["dingtalk", "钉钉"],
          ] as const
        ).map(([channel, label]) => (
          <details className="source-setting" key={channel}>
            <summary>{label}</summary>
            <div className="settings-grid">
              <label className="ready-filter">
                <input
                  type="checkbox"
                  checked={Boolean(config[`${channel}_enabled`])}
                  onChange={(e) =>
                    update(`${channel}_enabled`, e.target.checked)
                  }
                />
                启用 {label} 下载通知
              </label>
              {secret(`${channel}_webhook`, `${label} Webhook`)}
              {channel !== "discord" &&
                secret(`${channel}_sign_secret`, "加签密钥（可选）")}
            </div>
            <p>
              使用平台生成的 HTTPS
              Webhook。飞书和钉钉可填写加签密钥；使用关键词校验时允许“musicdl”。群成员可访问通知中的下载链接，请按需设置链接有效期。
            </p>
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() => void test(channel)}
            >
              检查 {label} 配置
            </button>
            {feedback(channel)}
          </details>
        ))}
      </section>
      <div className="settings-save">
        {error && (
          <p role="alert" className="log-error">
            {error}
          </p>
        )}
        {message && <p role="status">{message}</p>}
        {dirty && (
          <p className="muted">有未保存的修改，可先测试连接，保存后生效。</p>
        )}
        <button className="primary" disabled={busy} type="submit">
          {busy ? "正在处理…" : "保存通知配置"}
        </button>
      </div>
    </form>
  );
}
