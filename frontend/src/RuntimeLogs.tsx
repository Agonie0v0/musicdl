import { useEffect, useRef, useState } from "react";
import { Download, Pause, Play, Search, ArrowDown } from "lucide-react";
import { api } from "./api";
import { logTime } from "./log-time";

type LogEntry = {
  id: number;
  time: string;
  level: string;
  category?: string;
  task_id?: string | null;
  message: string;
  reason?: string | null;
};
const categories: Record<string, string> = {
  system: "服务",
  search: "搜索",
  download: "下载",
  playlist: "歌单",
  playback: "播放",
  engine: "引擎",
  bot: "通知与机器人",
};
const levels: Record<string, string> = {
  debug: "调试",
  info: "信息",
  warning: "警告",
  error: "错误",
};

export function RuntimeLogs({ active }: { active: boolean }) {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [live, setLive] = useState(true);
  const [follow, setFollow] = useState(true);
  const [level, setLevel] = useState("all");
  const [category, setCategory] = useState("all");
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const scroll = useRef<HTMLOListElement>(null);
  useEffect(() => {
    if (!active || !live) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await api<{ logs: LogEntry[] }>(
          "/logs",
          "GET",
          undefined,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setEntries(result.logs);
        setLoaded(true);
        setError("");
      } catch (err) {
        if (controller.signal.aborted) return;
        setError(
          `日志连接失败：${err instanceof Error ? err.message : String(err)}`,
        );
      }
      if (!controller.signal.aborted)
        timer = setTimeout(() => void poll(), 1000);
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [active, live]);
  const needle = query.trim().toLowerCase();
  const filtered = entries.filter(
    (entry) =>
      (level === "all" ||
        (level === "problems"
          ? ["error", "warning"].includes(entry.level)
          : entry.level === level)) &&
      (category === "all" || (entry.category || "system") === category) &&
      (!needle ||
        `${entry.reason || ""} ${entry.message} ${entry.task_id || ""} ${logTime(entry.time)} ${categories[entry.category || "system"] || ""}`
          .toLowerCase()
          .includes(needle)),
  );
  const lastId = filtered.at(-1)?.id;
  useEffect(() => {
    if (follow && scroll.current)
      scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [lastId, filtered.length, follow]);
  function exportLogs() {
    const text = filtered
      .map(
        (entry) =>
          `[${logTime(entry.time)}] [${entry.level.toUpperCase()}] [${categories[entry.category || "system"] || entry.category}]${entry.task_id ? ` [${entry.task_id}]` : ""} ${entry.reason ? entry.reason + "\n" : ""}${entry.message}`,
      )
      .join("\n");
    const url = URL.createObjectURL(
      new Blob([text], { type: "text/plain;charset=utf-8" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `musicdl-${new Date().toISOString().replace(/[:.]/g, "-")}.log`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <section className="view runtime-logs">
      <div className="log-controls">
        <div className="log-connection" role="status">
          <span
            className={`log-dot ${error ? "failed" : !live ? "paused" : ""}`}
          />
          {error
            ? "连接中断"
            : !live
              ? "更新已暂停"
              : loaded
                ? "实时更新中"
                : "正在连接"}
          <span className="muted">{entries.length} 条记录</span>
        </div>
        <div className="log-actions">
          <button className="secondary" onClick={() => setLive((v) => !v)}>
            {live ? <Pause /> : <Play />}
            {live ? "暂停更新" : "继续更新"}
          </button>
          <button
            className="secondary"
            disabled={!filtered.length}
            onClick={exportLogs}
          >
            <Download />
            导出当前日志
          </button>
        </div>
      </div>
      <div className="log-filters">
        <label className="log-search">
          <Search />
          <input
            aria-label="搜索日志"
            placeholder="搜索报错、音源或任务编号"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <select
          aria-label="日志级别"
          value={level}
          onChange={(e) => setLevel(e.target.value)}
        >
          <option value="all">全部级别</option>
          <option value="problems">错误与警告</option>
          {Object.entries(levels).map(([id, label]) => (
            <option value={id} key={id}>
              {label}
            </option>
          ))}
        </select>
        <select
          aria-label="日志类别"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
        >
          <option value="all">全部活动</option>
          {Object.entries(categories).map(([id, label]) => (
            <option value={id} key={id}>
              {label}
            </option>
          ))}
        </select>
        <label className="ready-filter">
          <input
            type="checkbox"
            checked={follow}
            onChange={(e) => setFollow(e.target.checked)}
          />
          自动滚动
        </label>
      </div>
      {error && (
        <p className="log-error" role="alert">
          {error}。服务恢复后会自动重新连接。
        </p>
      )}
      <div className="log-heading" aria-hidden="true">
        <span>时间</span>
        <span>级别</span>
        <span>活动</span>
        <span>任务</span>
        <span>日志内容</span>
      </div>
      <ol
        className="log-lines"
        ref={scroll}
        role="log"
        aria-label="运行日志列表"
        aria-live="off"
        onScroll={() => {
          const el = scroll.current;
          if (
            el &&
            follow &&
            el.scrollHeight - el.scrollTop - el.clientHeight > 48
          )
            setFollow(false);
        }}
      >
        {filtered.map((entry) => (
          <li className="log-row" key={entry.id} data-level={entry.level}>
            <time dateTime={entry.time}>{logTime(entry.time)}</time>
            <span className="log-level">
              {levels[entry.level] || entry.level}
            </span>
            <span>
              {categories[entry.category || "system"] || entry.category}
            </span>
            <code className="log-task" title={entry.task_id || undefined}>
              {entry.task_id?.slice(0, 8) || "—"}
            </code>
            <div className="log-content">
              {entry.reason ? (
                <>
                  <p>{entry.reason}</p>
                  <details>
                    <summary>查看原始信息</summary>
                    <pre>{entry.message}</pre>
                  </details>
                </>
              ) : (
                <pre>{entry.message}</pre>
              )}
            </div>
          </li>
        ))}
        {!filtered.length && (
          <li className="empty">
            {loaded
              ? entries.length
                ? "没有匹配的日志，请调整筛选条件。"
                : "暂无运行日志，搜索或下载后将在这里显示。"
              : "正在读取日志…"}
          </li>
        )}
      </ol>
      <div className="log-footer">
        <span>
          {filtered.length} / {entries.length} 条 · 保留最近 800 条
        </span>
        {!follow && (
          <button className="text-button" onClick={() => setFollow(true)}>
            <ArrowDown />
            跳到最新
          </button>
        )}
      </div>
    </section>
  );
}
