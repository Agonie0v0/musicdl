import { useState, type ReactNode } from "react";
import {
  ArrowLeft,
  Download,
  ListMusic,
  Pencil,
  Play,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
import { Cover, Modal } from "./components";
import { api } from "./api";
import type { PlaylistSummary, SavedPlaylist, Track } from "./types";

export function PlaylistLibrary({
  lists,
  current,
  sourceName,
  open,
  back,
  onImport,
  changed,
  deleted,
  playAll,
  queueAll,
  downloadAll,
  search,
  logs,
  children,
}: {
  lists: PlaylistSummary[];
  current: SavedPlaylist | null;
  sourceName: (id: string) => string;
  open: (id: string) => void;
  back: () => void;
  onImport: () => void;
  changed: (list: SavedPlaylist) => void;
  deleted: (id: string) => void;
  playAll: (tracks: Track[]) => void;
  queueAll: (tracks: Track[]) => void;
  downloadAll: (tracks: Track[]) => void;
  search: (query: string) => void;
  logs: () => void;
  children: ReactNode;
}) {
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    if (!current) return;
    setBusy(true);
    setError("");
    try {
      changed(
        await api<SavedPlaylist>(`/playlists/${current.id}`, "PATCH", {
          name: name.trim(),
        }),
      );
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (!current) return;
    setBusy(true);
    setError("");
    try {
      await api(`/playlists/${current.id}`, "DELETE");
      deleted(current.id);
      setRemoving(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  if (!current)
    return (
      <section className="view playlist-library">
        <div className="library-toolbar">
          <label className="library-search">
            <Search />
            <input
              aria-label="查找歌单"
              placeholder="查找歌单名称或音源"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <span className="muted">{lists.length} 个歌单</span>
          <button className="primary" onClick={onImport}>
            <Plus />
            导入歌单
          </button>
        </div>
        {!lists.length ? (
          <div className="library-empty">
            <ListMusic />
            <h2>还没有保存的歌单</h2>
            <p>导入平台歌单，在这里独立保存、播放和管理。</p>
            <button className="secondary" onClick={onImport}>
              导入第一个歌单
            </button>
          </div>
        ) : (
          <div className="saved-playlists">
            {lists
              .filter((list) =>
                `${list.name} ${sourceName(list.source)}`
                  .toLowerCase()
                  .includes(query.trim().toLowerCase()),
              )
              .map((list) => (
                <button
                  className="saved-playlist"
                  key={list.id}
                  onClick={() => open(list.id)}
                >
                  <Cover track={{ cover: list.cover }} large />
                  <strong>{list.name}</strong>
                  <span>
                    {sourceName(list.source)} · {list.track_count} 首
                  </span>
                  {list.issue_count > 0 && (
                    <small className="playlist-warning">
                      {list.issue_count} 首未导入
                    </small>
                  )}
                </button>
              ))}
          </div>
        )}
        {!!lists.length &&
          !lists.some((list) =>
            `${list.name} ${sourceName(list.source)}`
              .toLowerCase()
              .includes(query.trim().toLowerCase()),
          ) && <div className="empty">没有匹配的歌单。</div>}
      </section>
    );
  return (
    <section className="view playlist-detail">
      <button className="playlist-back" onClick={back}>
        <ArrowLeft />
        所有歌单
      </button>
      <div className="playlist-heading">
        <Cover
          track={current.tracks.find((t) => t.cover) || current.tracks[0]}
          large
        />
        <div className="playlist-heading-copy">
          <span className="muted">
            {sourceName(current.source)} ·{" "}
            {new Date(current.created_at * 1000).toLocaleDateString()} 导入
          </span>
          <h2>{current.name}</h2>
          <p>
            {current.tracks.length} 首曲目
            {current.issues.length
              ? `，另有 ${current.issues.length} 首未导入`
              : ""}
          </p>
          <div className="playlist-controls">
            <button
              className="primary"
              disabled={!current.tracks.length}
              onClick={() => playAll(current.tracks)}
            >
              <Play />
              播放全部
            </button>
            <button
              className="secondary"
              disabled={!current.tracks.length}
              onClick={() => queueAll(current.tracks)}
            >
              <Plus />
              加入待播
            </button>
            <button
              className="secondary"
              disabled={!current.tracks.length}
              onClick={() => downloadAll(current.tracks)}
            >
              <Download />
              下载全部
            </button>
            <button
              className="secondary"
              onClick={() => {
                setName(current.name);
                setError("");
                setEditing(true);
              }}
            >
              <Pencil />
              重命名
            </button>
            <button
              className="secondary"
              onClick={() => {
                setError("");
                setRemoving(true);
              }}
            >
              <Trash2 />
              删除歌单
            </button>
          </div>
        </div>
      </div>
      {!!current.issues.length && (
        <details className="playlist-issues" open>
          <summary>
            {current.issues.length} 首曲目未导入 · 查看原因与换源搜索
          </summary>
          <p>
            这些曲目未获得可用音频地址，不影响其他已导入歌曲。可尝试在其他音源查找。
          </p>
          <ul>
            {current.issues.map((issue, i) => (
              <li key={`${issue.source_id}-${i}`}>
                <div>
                  <strong>{issue.title}</strong>
                  <span>
                    {issue.artist || "歌手未知"}
                    {issue.source_id && ` · ID ${issue.source_id}`}
                  </span>
                  <p>{issue.reason}</p>
                </div>
                <button
                  className="secondary"
                  onClick={() =>
                    search(`${issue.title} ${issue.artist}`.trim())
                  }
                >
                  <Search />
                  换源搜索
                </button>
              </li>
            ))}
          </ul>
          <button className="text-link" onClick={logs}>
            查看运行日志
          </button>
        </details>
      )}
      {children}
      {editing && (
        <Modal title="重命名歌单" close={() => !busy && setEditing(false)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
          >
            <label className="form-label">
              歌单名称
              <input
                autoFocus
                maxLength={100}
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </label>
            {error && <p role="alert">{error}</p>}
            <div className="dialog-actions">
              <button
                className="secondary"
                type="button"
                disabled={busy}
                onClick={() => setEditing(false)}
              >
                取消
              </button>
              <button className="primary" disabled={busy || !name.trim()}>
                {busy ? "保存中…" : "保存"}
              </button>
            </div>
          </form>
        </Modal>
      )}
      {removing && (
        <Modal title="删除歌单" close={() => !busy && setRemoving(false)}>
          <p>删除「{current.name}」？已下载文件、收藏和待播列表会保留。</p>
          {error && <p role="alert">{error}</p>}
          <div className="dialog-actions">
            <button
              className="secondary"
              disabled={busy}
              onClick={() => setRemoving(false)}
            >
              取消
            </button>
            <button
              className="primary"
              disabled={busy}
              onClick={() => void remove()}
            >
              {busy ? "删除中…" : "确认删除"}
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}
