import { useEffect, useState } from "react";
import { Check } from "lucide-react";
import { api } from "./api";

type JsonObject = Record<string, unknown>;
interface EngineConfig {
  music_sources: string[];
  global_client_cfg: JsonObject;
  proxy_url: string;
  proxy_scope: string;
  proxy_sources: string[];
  request_timeout: number;
  default_threads: number;
  download_layout: string;
  lyrics_mode: string;
  cookies: Record<string, string | JsonObject>;
  init_music_clients_cfg: JsonObject;
  clients_threadings: JsonObject;
  requests_overrides: JsonObject;
  search_rules: JsonObject;
}
const advancedFields = [
  [
    "init_music_clients_cfg",
    "分源客户端参数",
    "覆盖音源的目录、Cookie、解析器及客户端初始化参数。",
  ],
  [
    "clients_threadings",
    "分源线程数",
    "按完整音源名称设置线程数；未设置的音源使用默认线程数。",
  ],
  [
    "requests_overrides",
    "分源请求覆盖",
    "保留各音源的代理、超时、请求头及其他请求参数。",
  ],
  [
    "search_rules",
    "分源搜索规则",
    "将音源专用的搜索与解析规则直接传给原引擎。",
  ],
] as const;
type AdvancedKey = (typeof advancedFields)[number][0];
const switches = [
  ["enable_search_curl_cffi", "搜索使用浏览器指纹", "搜索请求使用 curl_cffi。"],
  [
    "enable_parse_curl_cffi",
    "解析使用浏览器指纹",
    "播放和下载链接解析使用 curl_cffi。",
  ],
  [
    "enable_download_curl_cffi",
    "下载使用浏览器指纹",
    "下载文件请求使用 curl_cffi。",
  ],
  ["random_update_ua", "随机更新 User-Agent", "请求时更新浏览器标识。"],
  [
    "auto_set_proxies",
    "自动获取代理",
    "由原引擎获取公共代理，可能增加请求耗时。",
  ],
  ["maintain_session", "保持会话", "复用连接与 Cookie。"],
  [
    "strict_limit_search_size_per_page",
    "严格分页限制",
    "按每页大小限制返回结果。",
  ],
] as const;
const aliases: Record<string, string> = {
  migu: "MiguMusicClient",
  netease: "NeteaseMusicClient",
  qq: "QQMusicClient",
  kuwo: "KuwoMusicClient",
  qianqian: "QianqianMusicClient",
};
const parseObject = (text: string, label: string): JsonObject => {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error(`${label}：JSON 格式无效，请检查引号和逗号`);
  }
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error(`${label}必须是 JSON 对象`);
  return value as JsonObject;
};

export function EngineSettings({
  sources,
  saved,
}: {
  sources: { id: string; label: string; engine_name?: string }[];
  saved: () => Promise<void>;
}) {
  const [config, setConfig] = useState<EngineConfig | null>(null);
  const [drafts, setDrafts] = useState<Record<AdvancedKey, string>>({
    init_music_clients_cfg: "{}",
    clients_threadings: "{}",
    requests_overrides: "{}",
    search_rules: "{}",
  });
  const [extraGlobal, setExtraGlobal] = useState("{}");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const globalKeys = [
    "work_dir",
    "search_size_per_source",
    "search_size_per_page",
    "max_retries",
    ...switches.map(([key]) => key),
  ];
  function hydrate(value: EngineConfig) {
    setConfig(value);
    setDrafts(
      Object.fromEntries(
        advancedFields.map(([key]) => [
          key,
          JSON.stringify(value[key] || {}, null, 2),
        ]),
      ) as Record<AdvancedKey, string>,
    );
    setExtraGlobal(
      JSON.stringify(
        Object.fromEntries(
          Object.entries(value.global_client_cfg).filter(
            ([key]) => !globalKeys.includes(key),
          ),
        ),
        null,
        2,
      ),
    );
  }
  useEffect(() => {
    let active = true;
    void api<EngineConfig>("/engine/config")
      .then((value) => {
        if (active) hydrate(value);
      })
      .catch((e: Error) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, []);
  function updateGlobal(key: string, value: unknown) {
    setConfig(
      (current) =>
        current && {
          ...current,
          global_client_cfg: { ...current.global_client_cfg, [key]: value },
        },
    );
  }
  function updateAdvanced(key: AdvancedKey, name: string, value: unknown) {
    try {
      const map = parseObject(drafts[key], key);
      if (value === undefined) delete map[name];
      else map[name] = value;
      setDrafts({ ...drafts, [key]: JSON.stringify(map, null, 2) });
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function advancedMap(key: AdvancedKey) {
    try {
      return parseObject(drafts[key], key);
    } catch {
      return config?.[key] || {};
    }
  }
  const cookieText = (value: string | JsonObject | undefined) =>
    typeof value === "string"
      ? value
      : Object.entries(value || {})
          .map(([key, val]) => `${key}=${val}`)
          .join("; ");
  if (!config)
    return (
      <section className="settings-section">
        <p role={error ? "alert" : "status"}>{error || "正在读取完整设置…"}</p>
        {error && (
          <button
            className="secondary"
            onClick={() => {
              setError("");
              void api<EngineConfig>("/engine/config")
                .then(hydrate)
                .catch((e: Error) => setError(e.message));
            }}
          >
            重新读取设置
          </button>
        )}
      </section>
    );
  return (
    <form
      className="settings-form"
      onSubmit={async (event) => {
        event.preventDefault();
        setError("");
        setMessage("");
        setBusy(true);
        try {
          const extra = parseObject(extraGlobal, "其他全局参数");
          if (Object.keys(extra).some((key) => globalKeys.includes(key)))
            throw new Error(
              "其他全局参数包含上方表单已有的字段，请使用对应表单修改",
            );
          const payload = {
            ...config,
            global_client_cfg: {
              ...extra,
              ...Object.fromEntries(
                Object.entries(config.global_client_cfg).filter(([key]) =>
                  globalKeys.includes(key),
                ),
              ),
            },
          };
          for (const [key, label] of advancedFields)
            payload[key] = parseObject(drafts[key], label);
          await api("/engine/config", "PUT", payload);
          hydrate(await api<EngineConfig>("/engine/config"));
          await saved();
          setMessage("全部设置已保存并生效");
        } catch (e) {
          setError((e as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <section className="settings-section settings-basics">
        <h2>基础设置</h2>
        <p>直接使用原引擎的完整配置。保存后重新初始化音源客户端。</p>
        <div className="settings-grid">
          <label>
            下载目录
            <input
              value={String(config.global_client_cfg.work_dir || "")}
              onChange={(e) => updateGlobal("work_dir", e.target.value)}
              required
            />
          </label>
          {[
            ["search_size_per_source", "每个音源结果数", 1, 50],
            ["search_size_per_page", "每页大小", 1, 100],
            ["max_retries", "最大重试次数", 0, 10],
          ].map(([key, label, min, max]) => (
            <label key={key}>
              {label}
              <input
                type="number"
                min={min}
                max={max}
                required
                value={Number(config.global_client_cfg[key] ?? min)}
                onChange={(e) =>
                  updateGlobal(String(key), Number(e.target.value))
                }
              />
            </label>
          ))}
          <label>
            默认下载线程数
            <input
              type="number"
              min="1"
              max="50"
              required
              value={config.default_threads}
              onChange={(e) =>
                setConfig({
                  ...config,
                  default_threads: Number(e.target.value),
                })
              }
            />
          </label>
          <label>
            请求超时（秒，0 表示不限）
            <input
              type="number"
              min="0"
              max="300"
              required
              value={config.request_timeout}
              onChange={(e) =>
                setConfig({
                  ...config,
                  request_timeout: Number(e.target.value),
                })
              }
            />
          </label>
          <label>
            目录组织
            <select
              aria-label="目录组织"
              value={config.download_layout}
              onChange={(e) =>
                setConfig({ ...config, download_layout: e.target.value })
              }
            >
              {[
                ["source", "按音源"],
                ["artist", "按歌手"],
                ["artist_album", "歌手 / 专辑"],
                ["flat", "全部平铺"],
              ].map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            歌词保存
            <select
              aria-label="歌词保存"
              value={config.lyrics_mode}
              onChange={(e) =>
                setConfig({ ...config, lyrics_mode: e.target.value })
              }
            >
              {[
                ["both", "LRC 文件与嵌入"],
                ["lrc", "LRC 文件"],
                ["embed", "嵌入音频"],
                ["none", "不保存"],
              ].map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        </div>
      </section>
      <section className="settings-section settings-proxy">
        <h2>代理设置</h2>
        <div className="settings-grid">
          <label>
            全局 HTTP / HTTPS 代理
            <input
              value={config.proxy_url}
              onChange={(e) =>
                setConfig({ ...config, proxy_url: e.target.value })
              }
              placeholder="留空不使用代理"
            />
          </label>
          <label>
            代理作用范围
            <select
              aria-label="代理作用范围"
              value={config.proxy_scope}
              onChange={(e) =>
                setConfig({ ...config, proxy_scope: e.target.value })
              }
            >
              <option value="all">全部音源</option>
              <option value="selected">仅指定音源</option>
            </select>
          </label>
        </div>
        {config.proxy_scope === "selected" && (
          <fieldset className="source-picker">
            <legend>使用全局代理的音源</legend>
            {sources.map((source) => {
              const name =
                source.engine_name || aliases[source.id] || source.id;
              return (
                <label className="check-label" key={source.id}>
                  <input
                    type="checkbox"
                    checked={config.proxy_sources.includes(name)}
                    onChange={(e) =>
                      setConfig({
                        ...config,
                        proxy_sources: e.target.checked
                          ? [...config.proxy_sources, name]
                          : config.proxy_sources.filter((v) => v !== name),
                      })
                    }
                  />
                  {source.label}
                </label>
              );
            })}
          </fieldset>
        )}
      </section>
      <section className="settings-section settings-behavior">
        <h2>引擎行为</h2>
        <div className="engine-switches">
          {switches.map(([key, label, description]) => (
            <label className="check-label" key={key}>
              <input
                type="checkbox"
                checked={Boolean(config.global_client_cfg[key])}
                onChange={(e) => updateGlobal(key, e.target.checked)}
              />
              <span>
                {label}
                <small>{description}</small>
              </span>
            </label>
          ))}
        </div>
      </section>
      <section className="settings-section settings-sources">
        <h2>全部音源与账号设置</h2>
        <p>
          Cookie
          为可选项。分源线程数和代理留空时使用全局设置；分源代理优先于全局代理。
        </p>
        <div className="source-picker-actions">
          <button
            type="button"
            className="secondary"
            onClick={() =>
              setConfig({
                ...config,
                music_sources: sources.map(
                  (s) => s.engine_name || aliases[s.id] || s.id,
                ),
              })
            }
          >
            启用全部音源
          </button>
          <span>{config.music_sources.length} 个音源参与搜索</span>
        </div>
        <div className="source-settings-grid">
          {sources.map((source) => {
            const name = source.engine_name || aliases[source.id] || source.id;
            const override =
              (advancedMap("requests_overrides")[name] as JsonObject) || {};
            const proxies = (override.proxies as JsonObject) || {};
            return (
              <details key={source.id} className="source-setting">
                <summary>
                  <span>{source.label}</span>
                  <label
                    className="check-label"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <input
                      type="checkbox"
                      checked={config.music_sources.includes(name)}
                      onChange={(e) =>
                        setConfig({
                          ...config,
                          music_sources: e.target.checked
                            ? [...config.music_sources, name]
                            : config.music_sources.filter((v) => v !== name),
                        })
                      }
                    />
                    参与搜索
                  </label>
                </summary>
                <label>
                  Cookie
                  <textarea
                    spellCheck={false}
                    autoComplete="off"
                    value={cookieText(config.cookies[name])}
                    onChange={(e) =>
                      setConfig({
                        ...config,
                        cookies: { ...config.cookies, [name]: e.target.value },
                      })
                    }
                    placeholder="未配置账号"
                  />
                </label>
                <div className="settings-grid">
                  <label>
                    分源下载线程数
                    <input
                      type="number"
                      min="1"
                      max="50"
                      value={String(
                        advancedMap("clients_threadings")[name] ?? "",
                      )}
                      onChange={(e) =>
                        updateAdvanced(
                          "clients_threadings",
                          name,
                          e.target.value ? Number(e.target.value) : undefined,
                        )
                      }
                      placeholder="使用默认线程数"
                    />
                  </label>
                  <label>
                    分源 HTTP / HTTPS 代理
                    <input
                      value={String(proxies.https || proxies.http || "")}
                      onChange={(e) => {
                        const next = { ...override };
                        if (e.target.value.trim())
                          next.proxies = {
                            ...proxies,
                            http: e.target.value,
                            https: e.target.value,
                          };
                        else delete next.proxies;
                        updateAdvanced("requests_overrides", name, next);
                      }}
                      placeholder="使用全局代理设置"
                    />
                  </label>
                </div>
              </details>
            );
          })}
        </div>
      </section>
      <section className="settings-section settings-advanced">
        <h2>分源高级配置</h2>
        <p>
          键使用完整音源名称，例如
          NeteaseMusicClient。已有参数会原样读取、保存；修改普通设置不会清空这些配置。
        </p>
        <div className="advanced-settings-grid">
          {advancedFields.map(([key, label, description]) => (
            <label key={key}>
              {label}（{key}）
              <small className="setting-help">{description}</small>
              <textarea
                className="config-editor"
                rows={6}
                spellCheck={false}
                value={drafts[key]}
                onChange={(e) =>
                  setDrafts({ ...drafts, [key]: e.target.value })
                }
              />
            </label>
          ))}
          <label>
            其他全局参数
            <textarea
              className="config-editor"
              rows={4}
              spellCheck={false}
              value={extraGlobal}
              onChange={(e) => setExtraGlobal(e.target.value)}
            />
          </label>
        </div>
      </section>
      <div className="settings-save">
        <button
          className="primary"
          disabled={busy || !config.music_sources.length}
        >
          <Check />
          {busy ? "正在保存…" : "保存设置"}
        </button>
        {error && <p role="alert">{error}</p>}
        {message && <p role="status">{message}</p>}
      </div>
    </form>
  );
}
