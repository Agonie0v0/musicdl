import { useState } from "react";
import {
  Folder,
  Play,
  Plus,
  Pencil,
  Trash2,
  Download,
  Music2,
} from "lucide-react";
import { Button, Cover, Modal } from "./components";
import { api } from "./api";
import { clock } from "./lyrics";
import type { LibraryFile, Track } from "./types";

export function MusicLibrary({
  files,
  selected,
  setSelected,
  current,
  busy,
  error,
  refresh,
  play,
  playAll,
  queue,
  remove,
  changed,
}: {
  files: LibraryFile[];
  selected: string[];
  setSelected: (paths: string[]) => void;
  current: Track | null;
  busy: boolean;
  error: string;
  refresh: () => void;
  play: (track: Track) => void;
  playAll: (tracks: Track[]) => void;
  queue: (tracks: Track[]) => void;
  remove: (files: LibraryFile[]) => void;
  changed: (file: LibraryFile) => void;
}) {
  const [query, setQuery] = useState("");
  const [folder, setFolder] = useState("all");
  const [mode, setMode] = useState("songs");
  const [sort, setSort] = useState("title");
  const [editing, setEditing] = useState<LibraryFile | null>(null);
  const [draft, setDraft] = useState({ title: "", artist: "", album: "" });
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState("");
  const folderKey = (file: LibraryFile) =>
    JSON.stringify([file.root || "", file.folder || "."]);
  const folders = [
    ...new Map(
      files.map((file) => [
        folderKey(file),
        { root: file.root || "", name: file.folder || "." },
      ]),
    ).entries(),
  ].sort((a, b) => a[0].localeCompare(b[0]));
  const visible = files
    .filter(
      (file) =>
        (mode === "songs" ? !!file.track : !file.track) &&
        (folder === "all" || folderKey(file) === folder) &&
        [file.name, file.track?.title, file.track?.artist, file.track?.album]
          .join(" ")
          .toLowerCase()
          .includes(query.trim().toLowerCase()),
    )
    .sort((a, b) => {
      if (sort === "duration")
        return (b.track?.duration || 0) - (a.track?.duration || 0);
      const key = sort as "title" | "artist" | "album";
      return (a.track?.[key] || a.name).localeCompare(
        b.track?.[key] || b.name,
        "zh-CN",
      );
    });
  const chosen = visible.filter((file) => selected.includes(file.path));
  const playable = visible.flatMap((file) => (file.track ? [file.track] : []));
  const selectedTracks = chosen.flatMap((file) =>
    file.track ? [file.track] : [],
  );
  const songs = files.filter((file) => file.track).length;
  function selectFolder(value: string) {
    setFolder(value);
    setSelected([]);
  }
  function edit(file: LibraryFile) {
    if (!file.track) return;
    setDraft({
      title: file.track.title,
      artist: file.track.artist,
      album: file.track.album,
    });
    setEditError("");
    setEditing(file);
  }
  const folderName = (root: string, name: string) =>
    `${files.some((file) => file.root !== root) ? root.split(/[\\/]/).filter(Boolean).pop() + " / " : ""}${name === "." ? "根目录" : name}`;
  return (
    <section className="view music-library">
      <div className="library-heading">
        <div>
          <h2>歌曲库</h2>
          <p>
            {songs} 首歌曲 · {folders.length} 个文件夹
          </p>
        </div>
        <button className="secondary" onClick={refresh} disabled={busy}>
          刷新音乐库
        </button>
      </div>
      <div className="library-layout">
        <aside className="library-folders" aria-label="音乐文件夹">
          <button
            className={folder === "all" ? "active" : ""}
            onClick={() => selectFolder("all")}
          >
            <Music2 />
            全部文件夹
          </button>
          {folders.map(([key, value]) => (
            <button
              key={key}
              className={folder === key ? "active" : ""}
              onClick={() => selectFolder(key)}
              title={value.root + "/" + value.name}
            >
              <Folder />
              <span>{folderName(value.root, value.name)}</span>
            </button>
          ))}
        </aside>
        <div className="library-content">
          <div className="library-filters">
            <div className="library-modes">
              <button
                className={mode === "songs" ? "active" : ""}
                onClick={() => {
                  setMode("songs");
                  setSelected([]);
                }}
              >
                歌曲
              </button>
              <button
                className={mode === "files" ? "active" : ""}
                onClick={() => {
                  setMode("files");
                  setSelected([]);
                }}
              >
                附属文件
              </button>
            </div>
            <input
              className="management-search"
              aria-label="搜索音乐文件"
              placeholder="搜索歌曲、歌手或专辑"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <select
              aria-label="音乐库排序"
              value={sort}
              onChange={(e) => setSort(e.target.value)}
            >
              <option value="title">歌曲名称</option>
              <option value="artist">歌手</option>
              <option value="album">专辑</option>
              <option value="duration">时长</option>
            </select>
            <select
              className="library-folder-select"
              aria-label="选择音乐文件夹"
              value={folder}
              onChange={(e) => selectFolder(e.target.value)}
            >
              <option value="all">全部文件夹</option>
              {folders.map(([key, value]) => (
                <option key={key} value={key}>
                  {folderName(value.root, value.name)}
                </option>
              ))}
            </select>
          </div>
          <div className="management-toolbar">
            <label className="check-label">
              <input
                type="checkbox"
                aria-label="全选音乐文件"
                checked={visible.length > 0 && chosen.length === visible.length}
                disabled={busy || !visible.length}
                onChange={(e) =>
                  setSelected(
                    e.target.checked ? visible.map((file) => file.path) : [],
                  )
                }
              />
              全选当前结果
            </label>
            <span>已选 {chosen.length} 项</span>
            {mode === "songs" && (
              <>
                <button
                  className="primary"
                  disabled={!playable.length || busy}
                  onClick={() =>
                    playAll(selectedTracks.length ? selectedTracks : playable)
                  }
                >
                  <Play />
                  {selectedTracks.length ? "播放选中" : "播放全部"}
                </button>
                <button
                  className="secondary"
                  disabled={!selectedTracks.length || busy}
                  onClick={() => queue(selectedTracks)}
                >
                  加入待播
                </button>
              </>
            )}
            <button
              className="secondary"
              disabled={busy || !chosen.length}
              onClick={() => remove(chosen)}
            >
              删除选中文件
            </button>
            {chosen.length === 1 && (
              <a
                className="secondary"
                href={`/api/files/content?path=${encodeURIComponent(chosen[0].path)}`}
                download
              >
                下载到本机
              </a>
            )}
          </div>
          {error && <p role="alert">{error}</p>}
          <div className="music-table-wrap">
            <table className="music-table">
              <thead>
                <tr>
                  <th aria-label="选择" />
                  <th>{mode === "songs" ? "歌曲" : "文件"}</th>
                  <th className="library-artist">歌手</th>
                  <th className="library-album">专辑</th>
                  <th className="library-duration">时长 / 格式</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((file) => (
                  <tr
                    key={file.path}
                    className={current?.id === file.track?.id ? "current" : ""}
                  >
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`选择文件 ${file.name}`}
                        disabled={busy}
                        checked={selected.includes(file.path)}
                        onChange={(e) =>
                          setSelected(
                            e.target.checked
                              ? [...selected, file.path]
                              : selected.filter((path) => path !== file.path),
                          )
                        }
                      />
                    </td>
                    <td>
                      <div className="library-song">
                        <Cover track={file.track} />
                        <span>
                          <strong>{file.track?.title || file.name}</strong>
                          <small>
                            {file.track
                              ? `${file.track.artist || "未知歌手"} · ${file.track.album || "未知专辑"}`
                              : file.folder || "."}
                          </small>
                          <small title={file.name}>{file.name}</small>
                        </span>
                      </div>
                    </td>
                    <td className="library-artist">
                      {file.track?.artist || "—"}
                    </td>
                    <td className="library-album">
                      {file.track?.album || "—"}
                    </td>
                    <td className="library-duration">
                      {file.track
                        ? clock(file.track.duration)
                        : `${(file.size / 1024).toFixed(1)} KB`}
                      <small>{file.track?.playback.codec?.toUpperCase()}</small>
                    </td>
                    <td>
                      <div className="library-actions">
                        {file.track && (
                          <>
                            <Button
                              label={`播放本地歌曲 ${file.track.title}`}
                              onClick={() => play(file.track!)}
                            >
                              <Play />
                            </Button>
                            <Button
                              label={`加入待播 ${file.track.title}`}
                              onClick={() => queue([file.track!])}
                            >
                              <Plus />
                            </Button>
                            <Button
                              label={`编辑歌曲 ${file.track.title}`}
                              disabled={!file.editable || busy}
                              onClick={() => edit(file)}
                            >
                              <Pencil />
                            </Button>
                          </>
                        )}
                        <a
                          className="icon-button library-download"
                          title="下载到本机"
                          aria-label={`下载到本机 ${file.name}`}
                          href={`/api/files/content?path=${encodeURIComponent(file.path)}`}
                          download
                        >
                          <Download />
                        </a>
                        <Button
                          label={`删除文件 ${file.name}`}
                          disabled={busy}
                          onClick={() => remove([file])}
                        >
                          <Trash2 />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!visible.length && (
            <div className="empty">
              {!files.length
                ? "音乐库为空。下载完成后，歌曲会自动出现在这里，也可以将已有音频放入挂载的音乐目录。"
                : mode === "songs"
                  ? "此范围没有匹配的歌曲。可切换文件夹或查看附属文件。"
                  : "此范围没有匹配的附属文件。"}
            </div>
          )}
          <p className="management-help">
            歌曲直接从音乐目录播放。标签编辑会写回文件；下载到本机会通过浏览器再下载一份。
          </p>
        </div>
      </div>
      {editing && (
        <Modal
          title="编辑歌曲信息"
          close={saving ? undefined : () => setEditing(null)}
        >
          <form
            className="library-edit"
            onSubmit={async (event) => {
              event.preventDefault();
              setSaving(true);
              setEditError("");
              try {
                const updated = await api<LibraryFile>(
                  `/files/tags?path=${encodeURIComponent(editing.path)}`,
                  "PATCH",
                  draft,
                );
                changed(updated);
                setEditing(null);
              } catch (error) {
                setEditError((error as Error).message);
              } finally {
                setSaving(false);
              }
            }}
          >
            <p>{editing.name}</p>
            <p className="management-help">
              修改标题、歌手和专辑标签，文件名与音频内容保持不变。
            </p>
            {(
              [
                ["title", "歌曲标题"],
                ["artist", "歌手"],
                ["album", "专辑"],
              ] as const
            ).map(([key, label]) => (
              <label key={key}>
                {label}
                <input
                  maxLength={300}
                  value={draft[key]}
                  onChange={(e) =>
                    setDraft({ ...draft, [key]: e.target.value })
                  }
                />
              </label>
            ))}
            {editError && <p role="alert">{editError}</p>}
            <div className="management-toolbar">
              <button
                type="button"
                className="secondary"
                disabled={saving}
                onClick={() => setEditing(null)}
              >
                取消
              </button>
              <button className="primary" disabled={saving}>
                {saving ? "正在保存…" : "保存歌曲信息"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </section>
  );
}
