import { useEffect, useState } from "react";
import { api } from "./api";

type BotConfig = {
  public_url: string;
  link_ttl_seconds: number;
  bot_sources: string;
  tg_enabled: boolean;
  wecom_enabled: boolean;
  tg_allowed_users: string;
  wecom_corp_id: string;
  wecom_agent_id: string;
  wecom_allowed_users: string;
  configured: Record<string, boolean>;
  status: { tg: boolean; wecom: boolean };
  callback_url: string;
};
const secretFields = [
  "tg_token",
  "tg_proxy",
  "wecom_secret",
  "wecom_token",
  "wecom_aes_key",
];

export function BotSettings() {
  const [config, setConfig] = useState<BotConfig | null>(null);
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [clear, setClear] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [dirty, setDirty] = useState(false);
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
  async function test(channel: "tg" | "wecom") {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      setMessage(
        (await api<{ detail: string }>("/bots/test", "POST", { channel }))
          .detail,
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
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
          const { configured, status, callback_url, ...values } = config;
          const payload = {
            ...values,
            ...Object.fromEntries(
              secretFields.map((key) => [key, secrets[key] || ""]),
            ),
            clear_secrets: clear,
          };
          setConfig(await api<BotConfig>("/bots/config", "PUT", payload));
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
        <h2>下载链接</h2>
        <p>
          机器人通过私聊命令搜索、下载，并返回原文件链接。文件不经过聊天附件上传，没有机器人附件大小限制。
        </p>
        <div className="settings-grid">
          {field("public_url", "公网 HTTPS 地址", "https://music.example.com")}
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
          手机和企业微信需要能访问此 HTTPS 地址；请在部署的
          MUSICDL_TRUSTED_HOSTS 中加入该域名。设置保存在 data
          目录中，并覆盖对应环境变量。
        </p>
      </section>
      <section className="settings-section settings-proxy">
        <h2>使用方式</h2>
        <p>
          搜索歌名歌手 → 打开网页试听确认版本 → 选择编号下载 → 收到下载链接。
        </p>
        <p>
          Telegram 发送 /start 或 /menu 打开功能菜单，也可直接发送歌名搜索。
          输入框旁的菜单会自动注册；搜索结果每页 5 首，显示音质和文件大小。
          /status
          查看进度和管理任务。可发送“下一页”“上一页”“第2页”，下载只需“下载
          1”。Telegram
          仅允许白名单用户私聊；企业微信白名单留空时允许应用可见成员。
        </p>
        <p>
          连接测试只验证凭据，不发送消息。保存配置后再测试；下载和命令处理的错误可在“运行日志
          → 通知与机器人”中查看。
        </p>
      </section>
      <section className="settings-section settings-behavior">
        <h2>
          Telegram 机器人{" "}
          <small>{config.status.tg ? "已启用" : "未启用"}</small>
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
          {secret("tg_proxy", "Telegram HTTP / HTTPS 代理")}
        </div>
        <p>
          从 BotFather 获取 Token。白名单填写数字用户
          ID；使用长轮询接收命令，请移除该机器人的已有 webhook。代理仅用于
          Telegram。
        </p>
        <button
          type="button"
          className="secondary"
          disabled={busy || dirty || !config.status.tg}
          onClick={() => void test("tg")}
        >
          测试 Telegram 连接
        </button>
      </section>
      <section className="settings-section settings-sources">
        <h2>
          企业微信自建应用{" "}
          <small>{config.status.wecom ? "已启用" : "未启用"}</small>
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
              config.public_url
                ? config.public_url.replace(/\/$/, "") + "/bot/wecom"
                : "填写公网 HTTPS 地址后生成"
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
          disabled={busy || dirty || !config.status.wecom}
          onClick={() => void test("wecom")}
        >
          测试企业微信连接
        </button>
      </section>
      <div className="settings-save">
        {error && (
          <p role="alert" className="log-error">
            {error}
          </p>
        )}
        {message && <p role="status">{message}</p>}
        {dirty && <p className="muted">有未保存的修改。保存后再测试连接。</p>}
        <button className="primary" disabled={busy} type="submit">
          {busy ? "正在处理…" : "保存通知配置"}
        </button>
      </div>
    </form>
  );
}
