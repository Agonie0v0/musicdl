import { Cover, Button, Lyrics, Modal } from "./components";
import { useEffect, useRef, useState } from "react";
import {
  AudioLines,
  Search,
  Music2,
  ListMusic,
  Download,
  Folder,
  Settings,
  Heart,
  Play,
  Pause,
  SkipBack,
  SkipForward,
  Volume2,
  Expand,
  Plus,
  X,
  Check,
  Info,
  ArrowLeft,
  ArrowRight,
  Shuffle,
  Repeat,
  LogOut,
  Sun,
  Moon,
  Monitor,
  History,
  MoreHorizontal,
} from "lucide-react";
import { api } from "./api";
import { parseLrc, clock, type LyricLine } from "./lyrics";
import type {
  Track,
  Config,
  DownloadJob,
  LibraryFile,
  SourceStatus,
} from "./types";

const defaultSources = [
  { id: "migu", label: "咪咕" },
  { id: "netease", label: "网易云" },
  { id: "qq", label: "QQ音乐" },
  { id: "kuwo", label: "酷我" },
  { id: "qianqian", label: "千千" },
];
const views = {
  search: ["搜索音乐", "歌曲、歌手、专辑与不同音质版本"],
  player: ["正在播放", "歌词与播放列表"],
  import: ["歌单导入", "导入歌单后选择试听或下载版本"],
  queue: ["下载队列", "下载进度与所选音质"],
  favorites: ["我的收藏", "保存喜欢的曲目与音源"],
  files: ["音乐文件", "本地音乐库"],
  settings: ["音源与设置", "账号权限、代理与下载偏好"],
} as const;
type View = keyof typeof views;
const bytes = (n: number) =>
  n < 1024
    ? `${n} B`
    : n < 1048576
      ? `${(n / 1024).toFixed(1)} KB`
      : `${(n / 1048576).toFixed(1)} MB`;
export function App() {
  const [theme, setTheme] = useState<"system" | "light" | "dark">(() => {
    try {
      const saved = localStorage.getItem("musicdl-theme");
      return saved === "light" || saved === "dark" ? saved : "system";
    } catch {
      return "system";
    }
  });
  const [recentSearches, setRecentSearches] = useState<string[]>(() => {
    try {
      const saved: unknown = JSON.parse(
        localStorage.getItem("musicdl-searches") || "[]",
      );
      return Array.isArray(saved)
        ? saved.filter((v): v is string => typeof v === "string").slice(0, 8)
        : [];
    } catch {
      return [];
    }
  });
  const searchInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const system = matchMedia("(prefers-color-scheme: dark)");
    const update = () => {
      document.documentElement.dataset.theme =
        theme === "system" ? (system.matches ? "dark" : "light") : theme;
    };
    update();
    try {
      localStorage.setItem("musicdl-theme", theme);
    } catch {
      /* Storage is optional. */
    }
    system.addEventListener("change", update);
    return () => system.removeEventListener("change", update);
  }, [theme]);
  const [view, setView] = useState<View>("search"),
    [query, setQuery] = useState(""),
    [tracks, setTracks] = useState<Track[]>([]),
    [searching, setSearching] = useState(false),
    [statuses, setStatuses] = useState<Record<string, SourceStatus>>({}),
    [firstMs, setFirstMs] = useState<number | null>(null);
  const [config, setConfig] = useState<Config | null>(null),
    [sourceFilter, setSourceFilter] = useState("all"),
    [filter, setFilter] = useState("all"),
    [sort, setSort] = useState("relevance"),
    [selected, setSelected] = useState<Set<string>>(new Set());
  const sources = config?.available_sources || defaultSources;
  const [engineConfig, setEngineConfig] = useState("");
  const [favorites, setFavorites] = useState<Track[]>([]),
    [playlist, setPlaylist] = useState<Track[]>([]),
    [jobs, setJobs] = useState<DownloadJob[]>([]),
    [files, setFiles] = useState<LibraryFile[]>([]);
  const [current, setCurrent] = useState<Track | null>(null),
    [playing, setPlaying] = useState(false),
    [loadingPlay, setLoadingPlay] = useState(false),
    [time, setTime] = useState(0),
    [duration, setDuration] = useState(0),
    [volume, setVolume] = useState(0.65),
    [shuffle, setShuffle] = useState(false),
    [repeat, setRepeat] = useState(true);
  const [lyricText, setLyricText] = useState(""),
    [lines, setLines] = useState<LyricLine[]>([]),
    [follow, setFollow] = useState(true),
    [largeLyrics, setLargeLyrics] = useState(false),
    [panel, setPanel] = useState("lyrics");
  const [notice, setNotice] = useState(""),
    [loginRequired, setLoginRequired] = useState(false),
    [loginUser, setLoginUser] = useState(""),
    [loginPass, setLoginPass] = useState(""),
    [busy, setBusy] = useState(false);
  const [qualityTracks, setQualityTracks] = useState<Track[] | null>(null),
    [qualities, setQualities] = useState<Record<string, string>>({}),
    [showPlaylist, setShowPlaylist] = useState(false),
    [importSource, setImportSource] = useState("netease"),
    [importId, setImportId] = useState("");
  const audio = useRef<HTMLAudioElement>(null),
    playGeneration = useRef(0),
    searchGeneration = useRef(0),
    searchJob = useRef<string | null>(null),
    eventSource = useRef<EventSource | null>(null),
    startTime = useRef(0),
    currentRef = useRef<Track | null>(null),
    nextRef = useRef<() => void>(() => {});
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if (loginRequired || document.querySelector("dialog[open]")) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setView("search");
        requestAnimationFrame(() => searchInput.current?.focus());
      } else if (
        event.code === "Space" &&
        !(
          event.target instanceof HTMLElement &&
          (event.target.closest("input,textarea,select,button,a") ||
            event.target.isContentEditable)
        )
      ) {
        event.preventDefault();
        const element = audio.current;
        if (currentRef.current && element) {
          if (element.paused) void element.play().catch(fail);
          else element.pause();
        }
      }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [loginRequired]);
  const fail = (error: unknown) =>
    setNotice(error instanceof Error ? error.message : String(error));
  async function load() {
    const [cfg, favs, list, downloads] = await Promise.all([
      api<Config>("/config"),
      api<Track[]>("/favorites"),
      api<Track[]>("/playlist"),
      api<DownloadJob[]>("/downloads"),
    ]);
    setConfig(cfg);
    setFavorites(favs);
    setPlaylist(list);
    setJobs(downloads);
  }
  useEffect(() => {
    const login = () => setLoginRequired(true);
    window.addEventListener("musicdl-login", login);
    api<{ authenticated: boolean }>("/auth/status")
      .then((status) => {
        if (status.authenticated) load().catch(fail);
        else setLoginRequired(true);
      })
      .catch(fail);
    return () => {
      window.removeEventListener("musicdl-login", login);
      eventSource.current?.close();
      if (searchJob.current)
        void api(`/search/jobs/${searchJob.current}`, "DELETE").catch(() => {});
    };
  }, []);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 6500);
    return () => clearTimeout(timer);
  }, [notice]);
  useEffect(() => {
    if (loginRequired) return;
    const timer = setInterval(() => {
      void api<DownloadJob[]>("/downloads")
        .then(setJobs)
        .catch(() => {});
    }, 1500);
    return () => clearInterval(timer);
  }, [loginRequired]);
  useEffect(() => {
    if (view === "files")
      api<LibraryFile[]>("/files").then(setFiles).catch(fail);
  }, [view]);
  useEffect(() => {
    const element = audio.current;
    if (!element) return;
    element.volume = volume;
  }, [volume]);
  currentRef.current = current;
  const favIds = new Set(favorites.map((t) => t.id));
  const display = (view === "favorites" ? favorites : tracks)
    .filter(
      (track) =>
        (sourceFilter === "all" || track.source === sourceFilter) &&
        (filter !== "lossless" || track.qualities.some((q) => q.lossless)) &&
        (filter !== "ready" || track.playback.state === "ready"),
    )
    .sort((a, b) =>
      sort === "name"
        ? a.title.localeCompare(b.title, "zh")
        : sort === "duration"
          ? a.duration - b.duration
          : 0,
    );
  async function search(keyword = query) {
    if (!keyword.trim() || !config) return;
    const generation = ++searchGeneration.current;
    eventSource.current?.close();
    if (searchJob.current)
      void api(`/search/jobs/${searchJob.current}`, "DELETE").catch(() => {});
    setView("search");
    setQuery(keyword);
    setTracks([]);
    setSelected(new Set());
    setStatuses(
      Object.fromEntries(
        config.sources.map((id) => [id, { state: "searching" }]),
      ),
    );
    setSearching(true);
    setFirstMs(null);
    startTime.current = performance.now();
    try {
      const { id } = await api<{ id: string }>("/search/jobs", "POST", {
        keyword,
        sources: config.sources,
      });
      if (generation !== searchGeneration.current) {
        void api(`/search/jobs/${id}`, "DELETE");
        return;
      }
      searchJob.current = id;
      const events = new EventSource(`/api/search/jobs/${id}/events`, {
        withCredentials: true,
      });
      eventSource.current = events;
      const receive = (event: MessageEvent) => {
        if (generation !== searchGeneration.current) return;
        const track: Track = JSON.parse(event.data);
        setTracks((previous) => {
          const index = previous.findIndex((t) => t.id === track.id);
          if (index === -1) return [...previous, track];
          const next = [...previous];
          next[index] = track;
          return next;
        });
        if (track.playback.state === "ready")
          setFirstMs(
            (previous) =>
              previous ?? Math.round(performance.now() - startTime.current),
          );
      };
      events.addEventListener("candidate", receive as EventListener);
      events.addEventListener("track", receive as EventListener);
      events.addEventListener("source", (event) => {
        if (generation !== searchGeneration.current) return;
        const value = JSON.parse((event as MessageEvent).data);
        setStatuses((previous) => ({ ...previous, [value.id]: value.status }));
      });
      events.addEventListener("done", () => {
        if (generation !== searchGeneration.current) return;
        setSearching(false);
        setStatuses((previous) =>
          Object.fromEntries(
            Object.entries(previous).map(([id, status]) => [
              id,
              status.state === "searching"
                ? { state: "stopped", detail: "本次搜索已结束，音源未返回结果" }
                : status,
            ]),
          ),
        );
        events.close();
      });
      events.addEventListener("error", (event) => {
        if ("data" in event) {
          setNotice(JSON.parse(String(event.data)).detail);
          return;
        }
        if (events.readyState === EventSource.CLOSED) {
          setSearching(false);
          setNotice("搜索连接已关闭，请重试");
        }
      });
    } catch (error) {
      if (generation === searchGeneration.current) {
        setSearching(false);
        fail(error);
      }
    }
  }
  async function play(track: Track) {
    const generation = ++playGeneration.current;
    audio.current?.pause();
    audio.current?.removeAttribute("src");
    audio.current?.load();
    setCurrent(track);
    setLoadingPlay(true);
    setLyricText("正在获取歌词…");
    setLines([]);
    setTime(0);
    setDuration(track.duration);
    void api<{ text: string }>(`/tracks/${track.id}/lyrics`)
      .then(({ text }) => {
        if (generation !== playGeneration.current) return;
        const parsed = parseLrc(text);
        setLines(parsed);
        setLyricText(parsed.length ? "" : text || "此音源暂无歌词");
      })
      .catch((error) => {
        if (generation === playGeneration.current)
          setLyricText(error instanceof Error ? error.message : "歌词加载失败");
      });
    try {
      const prepared = await api<Track>(`/tracks/${track.id}/playback`, "POST");
      if (generation !== playGeneration.current) return;
      setCurrent(prepared);
      if (prepared.playback.state !== "ready" || !prepared.playback.url)
        throw new Error(
          prepared.playback.detail || "曲目暂不可试听，请更换音源",
        );
      const element = audio.current!;
      const mediaType: { [key: string]: string } = {
        flac: "audio/flac",
        mp3: "audio/mpeg",
        m4a: "audio/mp4",
        ogg: "audio/ogg",
        wav: "audio/wav",
      };
      if (
        prepared.playback.codec &&
        !element.canPlayType(mediaType[prepared.playback.codec] || "")
      )
        throw new Error("浏览器不支持该试听格式，请选择其他音源");
      element.src = prepared.playback.url;
      element.load();
      await element.play();
    } catch (error) {
      if (generation === playGeneration.current) fail(error);
    } finally {
      if (generation === playGeneration.current) setLoadingPlay(false);
    }
  }
  function seek(seconds: number) {
    if (audio.current?.src && audio.current.readyState > 0) {
      audio.current.currentTime = Math.min(
        seconds,
        audio.current.duration || duration,
      );
      setTime(seconds);
    }
  }
  async function toggle() {
    if (!current) return;
    if (audio.current?.paused) {
      if (!audio.current.src) {
        await play(current);
        return;
      }
      await audio.current.play().catch(fail);
    } else audio.current?.pause();
  }
  function step(direction: number) {
    const list = playlist.length ? playlist : tracks;
    if (!list.length) return;
    const index = list.findIndex((t) => t.id === current?.id);
    let next = shuffle
      ? Math.floor(Math.random() * list.length)
      : index + direction;
    if (next >= list.length) {
      if (!repeat) return;
      next = 0;
    }
    if (next < 0) next = list.length - 1;
    void play(list[next]);
  }
  nextRef.current = () => step(1);
  async function favorite(track: Track) {
    try {
      const exists = favIds.has(track.id);
      await api(`/favorites/${track.id}`, exists ? "DELETE" : "PUT");
      setFavorites((previous) =>
        exists
          ? previous.filter((t) => t.id !== track.id)
          : [track, ...previous],
      );
    } catch (error) {
      fail(error);
    }
  }
  async function addToPlaylist(items: Track[]) {
    try {
      const next = [
        ...playlist,
        ...items.filter((t) => !playlist.some((p) => p.id === t.id)),
      ].slice(0, 1000);
      await api(
        "/playlist",
        "PUT",
        next.map((t) => t.id),
      );
      setPlaylist(next);
      setNotice(`已加入播放列表，共 ${next.length} 首`);
    } catch (error) {
      fail(error);
    }
  }
  async function removeFromPlaylist(id: string) {
    const next = playlist.filter((t) => t.id !== id);
    try {
      await api(
        "/playlist",
        "PUT",
        next.map((t) => t.id),
      );
      setPlaylist(next);
    } catch (error) {
      fail(error);
    }
  }
  async function chooseDownload(items: Track[]) {
    if (!items.length) {
      setNotice("请先选择曲目");
      return;
    }
    try {
      if (items.some((t) => !t.qualities.length))
        setNotice("正在确认可下载音质…");
      items = await Promise.all(
        items.map((t) =>
          t.qualities.length
            ? t
            : api<Track>(`/tracks/${t.id}/versions`, "POST"),
        ),
      );
      const resolved = new Map(items.map((t) => [t.id, t]));
      setTracks((previous) => previous.map((t) => resolved.get(t.id) || t));
      setFavorites((previous) => previous.map((t) => resolved.get(t.id) || t));
      setCurrent((previous) =>
        previous ? resolved.get(previous.id) || previous : null,
      );
    } catch (error) {
      fail(error);
      return;
    }
    setQualityTracks(items);
    setQualities(
      Object.fromEntries(
        items.map((t) => [
          t.id,
          t.qualities.find((q) => q.lossless)?.id || t.qualities[0]?.id || "",
        ]),
      ),
    );
  }
  async function download() {
    if (!qualityTracks) return;
    setBusy(true);
    try {
      const created = await api<DownloadJob[]>(
        "/downloads",
        "POST",
        qualityTracks.map((track) => ({
          track_id: track.id,
          quality_id: qualities[track.id],
        })),
      );
      setJobs((previous) => [...created, ...previous]);
      setQualityTracks(null);
      setNotice(`已加入 ${created.length} 首下载任务`);
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  }
  async function importList() {
    setBusy(true);
    try {
      const id = importId.trim();
      const result = await api<{ tracks: Track[]; detail: string }>(
        "/playlist/import",
        "POST",
        { source: importSource, id },
      );
      setTracks(result.tracks);
      setStatuses({});
      setSourceFilter("all");
      setFilter("all");
      setView("search");
      setNotice(result.detail);
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  }
  const selection = (view === "favorites" ? favorites : tracks).filter((t) =>
    selected.has(t.id),
  );
  const selectedToggle = (id: string) =>
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const statusLabels: Record<string, string> = {
    queued: "等待下载",
    resolving: "验证资源",
    downloading: "正在下载",
    enriching: "保存标签与歌词",
    complete: "已完成",
    failed: "失败",
    paused: "已暂停",
    cancelled: "已取消",
  };
  const playbackLabel =
    current?.playback.state === "ready"
      ? `${current.playback.preview_only ? "片段试听 · " : ""}${current.playback.codec?.toUpperCase()}${current.playback.bitrate ? ` ${current.playback.bitrate} kbps` : ""}`
      : loadingPlay
        ? "正在准备试听"
        : current?.playback.detail || "尚未播放";
  const listContent = (
    <div className="playlist-items">
      {playlist.length ? (
        playlist.map((track) => (
          <div
            className={`playlist-item ${current?.id === track.id ? "active" : ""}`}
            key={track.id}
          >
            <button className="playlist-song" onClick={() => void play(track)}>
              <Cover track={track} />
              <span>
                <strong>{track.title}</strong>
                <small>{track.artist}</small>
              </span>
            </button>
            <Button
              label={`移除 ${track.title}`}
              onClick={() => void removeFromPlaylist(track.id)}
            >
              <X />
            </Button>
          </div>
        ))
      ) : (
        <p className="empty">选择搜索结果，加入待播列表。</p>
      )}
    </div>
  );
  const lyricContent = (
    <Lyrics
      lines={lines}
      text={lyricText}
      time={time}
      onSeek={seek}
      follow={follow}
    />
  );
  const trackTable = (
    <>
      <section className="results">
        <div className="result-tools">
          <strong>
            {view === "favorites" ? "收藏曲目" : "搜索结果"}{" "}
            <span>{display.length}</span>
          </strong>
          <div className="filter-group">
            {[
              ["all", "全部"],
              ["lossless", "含无损版本"],
              ["ready", "可试听"],
            ].map(([id, label]) => (
              <button
                key={id}
                className={filter === id ? "active" : ""}
                onClick={() => setFilter(id)}
              >
                {label}
              </button>
            ))}
          </div>
          <select
            aria-label="结果排序"
            value={sort}
            onChange={(e) => setSort(e.target.value)}
          >
            <option value="relevance">相关度</option>
            <option value="name">曲名</option>
            <option value="duration">时长</option>
          </select>
        </div>
        <table>
          <thead>
            <tr>
              <th>
                <input
                  aria-label="全选当前结果"
                  type="checkbox"
                  checked={
                    display.length > 0 &&
                    display.every((t) => selected.has(t.id))
                  }
                  onChange={(e) =>
                    setSelected(
                      e.target.checked
                        ? new Set(display.map((t) => t.id))
                        : new Set(),
                    )
                  }
                />
              </th>
              <th>曲目 / 专辑</th>
              <th>音源</th>
              <th>音质版本</th>
              <th>时长</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {display.map((track) => (
              <tr
                key={track.id}
                className={`${selected.has(track.id) ? "selected" : ""} ${current?.id === track.id ? "current" : ""}`}
              >
                <td>
                  <input
                    type="checkbox"
                    aria-label={`选择 ${track.title}`}
                    checked={selected.has(track.id)}
                    onChange={() => selectedToggle(track.id)}
                  />
                </td>
                <td>
                  <div className="track">
                    <Cover track={track} />
                    <div className="song-copy">
                      <div className="song-name" title={track.title}>
                        {track.title}
                      </div>
                      {!!track.episodes && (
                        <button
                          className="text-button"
                          onClick={() => {
                            void api<Track[]>(`/tracks/${track.id}/episodes`)
                              .then((items) => {
                                setTracks(items);
                                setSelected(new Set());
                                setView("search");
                              })
                              .catch(fail);
                          }}
                        >
                          展开 {track.episodes} 个分集
                        </button>
                      )}
                      <div
                        className="song-meta"
                        title={`${track.artist} / ${track.album}`}
                      >
                        {track.artist} / {track.album || "未知专辑"}
                      </div>
                    </div>
                  </div>
                </td>
                <td>{sources.find((s) => s.id === track.source)?.label}</td>
                <td>
                  <button
                    className="quality"
                    aria-label={`选择 ${track.title} 下载版本`}
                    onClick={() => chooseDownload([track])}
                  >
                    {track.qualities.slice(0, 3).map((q) => (
                      <span
                        className={`badge ${q.lossless ? "flac" : ""}`}
                        title={
                          q.evidence === "engine"
                            ? "原音源解析器返回的实际版本"
                            : `${q.availability === "login_required" ? "需要登录 · " : ""}音源标注，下载前验证`
                        }
                        key={q.id}
                      >
                        {q.label}
                      </span>
                    ))}
                    {!track.qualities.length && (
                      <span className="badge pending">音质待确认</span>
                    )}
                  </button>
                  <small
                    className="readiness"
                    title={track.playback.detail || ""}
                  >
                    {track.playback.state === "ready"
                      ? "可试听"
                      : track.playback.state === "pending"
                        ? "试听准备中"
                        : track.playback.detail || "暂不可试听"}
                  </small>
                </td>
                <td className="duration">
                  {track.duration ? clock(track.duration) : "—"}
                </td>
                <td>
                  <div className="row-actions">
                    <button
                      className="row-play"
                      aria-label={`播放 ${track.title}`}
                      onClick={() => void play(track)}
                    >
                      <Play />
                    </button>
                    <button
                      className={`icon-button favorite ${favIds.has(track.id) ? "on" : ""}`}
                      aria-label={`收藏 ${track.title}`}
                      onClick={() => void favorite(track)}
                    >
                      <Heart />
                    </button>
                    <details className="track-menu">
                      <summary aria-label={`更多操作 ${track.title}`}>
                        <MoreHorizontal />
                      </summary>
                      <div>
                        <button
                          onClick={(e) => {
                            void addToPlaylist([track]);
                            e.currentTarget
                              .closest("details")
                              ?.removeAttribute("open");
                          }}
                        >
                          <Plus />
                          加入播放列表
                        </button>
                        <button
                          onClick={(e) => {
                            chooseDownload([track]);
                            e.currentTarget
                              .closest("details")
                              ?.removeAttribute("open");
                          }}
                        >
                          <Download />
                          选择下载版本
                        </button>
                        <button
                          onClick={(e) => {
                            void favorite(track);
                            e.currentTarget
                              .closest("details")
                              ?.removeAttribute("open");
                          }}
                        >
                          <Heart />
                          {favIds.has(track.id) ? "取消收藏" : "收藏曲目"}
                        </button>
                      </div>
                    </details>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!display.length && (
          <div className="empty">
            {searching
              ? "正在搜索，结果将逐步显示…"
              : view === "favorites"
                ? "还没有收藏，点击曲目旁的爱心保存。"
                : tracks.length
                  ? "没有符合筛选条件的曲目。"
                  : "输入歌曲、歌手或专辑开始搜索。"}
          </div>
        )}
      </section>
      <div className="result-foot">
        <Info />
        音源标注的音质将在下载前验证
        <span>
          {firstMs !== null
            ? `首个可试听结果 ${(firstMs / 1000).toFixed(2)} 秒`
            : searching
              ? "正在准备试听资源"
              : ""}
        </span>
      </div>
      <div className="selection-bar">
        <span>
          已选择 <b>{selection.length}</b> 首
        </span>
        <div>
          <button onClick={() => setSelected(new Set())}>取消选择</button>
          <button
            className="secondary"
            disabled={!selection.length}
            onClick={() => void addToPlaylist(selection)}
          >
            <Plus />
            加入播放列表
          </button>
          <button
            className="primary"
            disabled={!selection.length}
            onClick={() => chooseDownload(selection)}
          >
            <Download />
            批量下载
          </button>
        </div>
      </div>
    </>
  );
  return (
    <>
      <audio
        ref={audio}
        preload="none"
        onTimeUpdate={() => setTime(audio.current?.currentTime || 0)}
        onDurationChange={() =>
          setDuration(
            Number.isFinite(audio.current?.duration)
              ? audio.current!.duration
              : currentRef.current?.duration || 0,
          )
        }
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => nextRef.current()}
        onError={() => {
          setLoadingPlay(false);
          setNotice("播放资源不可用或格式不支持，请重新试听或更换音源");
        }}
      />
      <div className="app">
        <aside className="sidebar">
          <div className="brand">
            <AudioLines />
            musicdl
          </div>
          <nav aria-label="主导航">
            {(
              [
                "search",
                "player",
                "favorites",
                "import",
                "queue",
                "files",
              ] as View[]
            ).map((name, i) => {
              const Icon = [Search, Music2, Heart, ListMusic, Download, Folder][
                i
              ];
              return (
                <button
                  key={name}
                  className={`nav-item ${view === name ? "active" : ""}`}
                  aria-current={view === name ? "page" : undefined}
                  onClick={() => {
                    setView(name);
                    setSourceFilter("all");
                    setSelected(new Set());
                  }}
                >
                  <Icon />
                  {views[name][0]}
                  {name === "queue" &&
                    jobs.some(
                      (j) => !["complete", "cancelled"].includes(j.status),
                    ) && (
                      <span className="nav-count">
                        {
                          jobs.filter(
                            (j) =>
                              !["complete", "cancelled"].includes(j.status),
                          ).length
                        }
                      </span>
                    )}
                  {name === "favorites" && favorites.length > 0 && (
                    <span className="nav-count">{favorites.length}</span>
                  )}
                </button>
              );
            })}
          </nav>
          <div className="sidebar-bottom">
            {current && (
              <button
                className="sidebar-record"
                onClick={() => setView("player")}
              >
                <Cover track={current} />
                <span>
                  <strong>{current.title}</strong>
                  <small>{current.artist}</small>
                </span>
                <AudioLines />
              </button>
            )}
            <button
              className={`nav-item ${view === "settings" ? "active" : ""}`}
              onClick={() => setView("settings")}
            >
              <Settings />
              音源与设置
            </button>
            <div className="user">
              <span className="avatar">
                <Music2 />
              </span>
              <span>
                本地音乐库
                <small>
                  {favorites.length} 首收藏 · {playlist.length} 首待播
                </small>
              </span>
              <Button
                label="退出登录"
                onClick={() =>
                  void api("/auth/logout", "POST")
                    .then(() => api<{ required: boolean }>("/auth/status"))
                    .then((s) => s.required && setLoginRequired(true))
                    .catch(fail)
                }
              >
                <LogOut />
              </Button>
            </div>
          </div>
        </aside>
        <main className="workspace">
          <header className="top">
            <div>
              <h1>{views[view][0]}</h1>
              <p>{views[view][1]}</p>
            </div>
            <div className="top-actions">
              <label className="theme-control">
                {theme === "system" ? (
                  <Monitor />
                ) : theme === "dark" ? (
                  <Moon />
                ) : (
                  <Sun />
                )}
                <select
                  aria-label="界面主题"
                  value={theme}
                  onChange={(e) => setTheme(e.target.value as typeof theme)}
                >
                  <option value="system">跟随系统</option>
                  <option value="light">浅色</option>
                  <option value="dark">深色</option>
                </select>
              </label>
              {searching && (
                <button
                  className="secondary"
                  onClick={() => {
                    searchGeneration.current++;
                    eventSource.current?.close();
                    if (searchJob.current)
                      void api(
                        `/search/jobs/${searchJob.current}`,
                        "DELETE",
                      ).catch(fail);
                    setSearching(false);
                  }}
                >
                  停止搜索
                </button>
              )}
              <Button label="音源设置" onClick={() => setView("settings")}>
                <Settings />
              </Button>
            </div>
          </header>
          {(view === "search" || view === "favorites") && (
            <section className="view search-view">
              {view === "search" && (
                <>
                  <form
                    className="searchbox"
                    onSubmit={(e) => {
                      e.preventDefault();
                      const keyword = query.trim();
                      if (keyword) {
                        const history = [
                          keyword,
                          ...recentSearches.filter((v) => v !== keyword),
                        ].slice(0, 8);
                        setRecentSearches(history);
                        try {
                          localStorage.setItem(
                            "musicdl-searches",
                            JSON.stringify(history),
                          );
                        } catch {
                          /* Storage is optional. */
                        }
                      }
                      void search();
                    }}
                  >
                    <Search />
                    <input
                      ref={searchInput}
                      aria-label="搜索音乐"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder="搜索歌曲、歌手或专辑"
                      maxLength={200}
                    />
                    <kbd className="search-shortcut" title="聚焦搜索">
                      Ctrl K
                    </kbd>
                    <button
                      type="button"
                      className="scope"
                      onClick={() => setView("settings")}
                    >
                      音源设置
                    </button>
                    <button
                      className="primary"
                      type="submit"
                      disabled={!query.trim() || !config}
                    >
                      搜索
                    </button>
                  </form>
                  {!tracks.length && !searching && (
                    <div className="search-welcome">
                      <div className="welcome-art">
                        <AudioLines />
                      </div>
                      <div>
                        <h2>今天想听什么？</h2>
                        <p>搜索歌曲、歌手或专辑，选择音源与音质版本。</p>
                      </div>
                      <div className="library-shortcuts">
                        <button onClick={() => setView("favorites")}>
                          <Heart />
                          我的收藏<span>{favorites.length} 首</span>
                        </button>
                        <button onClick={() => setShowPlaylist(true)}>
                          <ListMusic />
                          播放列表<span>{playlist.length} 首</span>
                        </button>
                        <button onClick={() => setView("import")}>
                          <Plus />
                          导入歌单
                        </button>
                      </div>
                      {!!recentSearches.length && (
                        <div className="recent-searches">
                          <History />
                          <span>最近搜索</span>
                          {recentSearches.map((keyword) => (
                            <button
                              key={keyword}
                              onClick={() => {
                                setQuery(keyword);
                                searchInput.current?.focus();
                              }}
                            >
                              {keyword}
                            </button>
                          ))}
                          <button
                            aria-label="清空搜索历史"
                            onClick={() => {
                              setRecentSearches([]);
                              try {
                                localStorage.removeItem("musicdl-searches");
                              } catch {
                                /* Storage is optional. */
                              }
                            }}
                          >
                            <X />
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                  {!tracks.length && !searching && !!favorites.length && (
                    <section className="collection-shelf" aria-label="收藏速览">
                      <div className="section-heading">
                        <h2>从收藏开始听</h2>
                        <button
                          className="text-button"
                          onClick={() => setView("favorites")}
                        >
                          查看全部
                          <ArrowRight />
                        </button>
                      </div>
                      <div className="collection-covers">
                        {favorites.slice(0, 6).map((track) => (
                          <button
                            key={track.id}
                            onClick={() => void play(track)}
                          >
                            <Cover track={track} large />
                            <strong>{track.title}</strong>
                            <small>{track.artist}</small>
                          </button>
                        ))}
                      </div>
                    </section>
                  )}
                  <div className="sources">
                    <button
                      className={`source ${sourceFilter === "all" ? "active" : ""}`}
                      onClick={() => setSourceFilter("all")}
                    >
                      全部音源<span>{tracks.length}</span>
                    </button>
                    {sources
                      .filter((s) => config?.sources.includes(s.id))
                      .map((s) => (
                        <button
                          key={s.id}
                          className={`source ${sourceFilter === s.id ? "active" : ""} ${statuses[s.id]?.state === "failed" ? "failed" : ""}`}
                          title={statuses[s.id]?.detail}
                          onClick={() => setSourceFilter(s.id)}
                        >
                          <i />
                          {s.label}
                          <span>
                            {statuses[s.id]?.state === "failed"
                              ? "失败"
                              : statuses[s.id]?.state === "searching"
                                ? "搜索中"
                                : tracks.filter((t) => t.source === s.id)
                                    .length}
                          </span>
                        </button>
                      ))}
                    <span className="source-note">
                      {
                        Object.values(statuses).filter(
                          (s) => s.state === "complete",
                        ).length
                      }{" "}
                      个音源已返回
                    </span>
                  </div>
                  {Object.entries(statuses).some(
                    ([, s]) => s.state === "failed",
                  ) && (
                    <div className="source-errors">
                      {Object.entries(statuses)
                        .filter(([, s]) => s.state === "failed")
                        .map(([id, s]) => (
                          <span key={id}>
                            {sources.find((v) => v.id === id)?.label}：
                            {s.detail}
                          </span>
                        ))}
                    </div>
                  )}
                </>
              )}
              <div className="search-grid">
                <div className="result-column">{trackTable}</div>
                <aside className="now-panel">
                  <div className="panel-heading">
                    <h2>正在试听</h2>
                    <Button
                      label="展开播放界面"
                      onClick={() => setView("player")}
                    >
                      <Expand />
                    </Button>
                  </div>
                  {current ? (
                    <div className="current-track">
                      <Cover track={current} />
                      <div>
                        <strong>{current.title}</strong>
                        <p>
                          {current.artist} / {current.album}
                        </p>
                        <span className="listen-badge">{playbackLabel}</span>
                      </div>
                      <Button
                        label="收藏当前曲目"
                        active={favIds.has(current.id)}
                        onClick={() => void favorite(current)}
                      >
                        <Heart />
                      </Button>
                    </div>
                  ) : (
                    <div className="empty compact">
                      点击曲目的播放按钮开始试听
                    </div>
                  )}
                  <div
                    className="panel-tabs"
                    role="tablist"
                    aria-label="曲目详情"
                  >
                    {[
                      ["lyrics", "歌词"],
                      ["versions", "音质版本"],
                      ["playlist", `待播列表 ${playlist.length}`],
                    ].map(([id, label]) => (
                      <button
                        key={id}
                        role="tab"
                        aria-selected={panel === id}
                        className={panel === id ? "active" : ""}
                        onClick={() => setPanel(id)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  <div className="panel-content" role="tabpanel">
                    {panel === "lyrics" ? (
                      <>
                        {lyricContent}
                        <div className="lyrics-bottom">
                          <span>{lines.length ? "点击歌词跳转" : ""}</span>
                          <button onClick={() => setView("player")}>
                            完整歌词
                            <ArrowRight />
                          </button>
                        </div>
                      </>
                    ) : panel === "playlist" ? (
                      listContent
                    ) : (
                      <>
                        {current?.qualities.map((q) => (
                          <button
                            className="version-option"
                            key={q.id}
                            onClick={() => chooseDownload([current])}
                          >
                            <span
                              className={`badge ${q.lossless ? "flac" : ""}`}
                            >
                              {q.label}
                            </span>
                            <span>
                              <small>
                                {q.availability === "login_required"
                                  ? "需要账号权限"
                                  : "音源标注 · 下载前验证"}
                              </small>
                            </span>
                          </button>
                        ))}
                        <p className="version-note">
                          试听音质与下载版本独立选择。
                        </p>
                      </>
                    )}
                  </div>
                  <div className="panel-download">
                    <button
                      className="primary"
                      disabled={!current}
                      onClick={() => current && chooseDownload([current])}
                    >
                      <Download />
                      选择版本并下载
                    </button>
                  </div>
                  <div className="queue-summary">
                    <div>
                      <strong>下载队列</strong>
                      <button onClick={() => setView("queue")}>
                        查看全部
                        <ArrowRight />
                      </button>
                    </div>
                    {jobs.slice(0, 2).map((job) => (
                      <div className="queue-mini" key={job.id}>
                        <div>
                          <span>{job.track.title}</span>
                          <small>{statusLabels[job.status]}</small>
                        </div>
                        <div className="progress">
                          <span
                            style={{
                              width:
                                job.status === "complete"
                                  ? "100%"
                                  : job.total_bytes
                                    ? `${Math.min(100, (job.bytes / job.total_bytes) * 100)}%`
                                    : "0%",
                            }}
                          />
                        </div>
                      </div>
                    ))}
                    {!jobs.length && <p className="muted">暂无下载任务</p>}
                  </div>
                </aside>
              </div>
            </section>
          )}
          {view === "player" && (
            <section className="view full-player">
              {current?.cover && (
                <div
                  className="player-atmosphere"
                  aria-hidden="true"
                  style={{
                    backgroundImage: `url(${JSON.stringify(current.cover)})`,
                  }}
                />
              )}
              <div className="playback-top">
                <button
                  className="text-button"
                  onClick={() => setView("search")}
                >
                  <ArrowLeft />
                  返回搜索结果
                </button>
                <span className="badge">{playbackLabel}</span>
              </div>
              {current ? (
                <div className="playback-grid">
                  <div className="album-column">
                    <Cover track={current} large />
                    <div className="album-info">
                      <div>
                        <h2>{current.title}</h2>
                        <p>{current.artist}</p>
                      </div>
                      <Button
                        label="收藏当前曲目"
                        active={favIds.has(current.id)}
                        onClick={() => void favorite(current)}
                      >
                        <Heart />
                      </Button>
                    </div>
                    <div className="album-metadata">
                      <span>
                        {sources.find((s) => s.id === current.source)?.label}
                      </span>
                      <span>{current.album}</span>
                      <span>{clock(duration)}</span>
                    </div>
                    <div className="album-actions">
                      <Button
                        label="切换随机播放"
                        active={shuffle}
                        onClick={() => setShuffle((v) => !v)}
                      >
                        <Shuffle />
                      </Button>
                      <Button
                        label="切换列表循环"
                        active={repeat}
                        onClick={() => setRepeat((v) => !v)}
                      >
                        <Repeat />
                      </Button>
                      <button
                        className="secondary"
                        onClick={() => chooseDownload([current])}
                      >
                        <Download />
                        选择下载音质
                      </button>
                      <button
                        className="secondary"
                        onClick={() => void addToPlaylist([current])}
                      >
                        <Plus />
                        加入播放列表
                      </button>
                    </div>
                    <section className="album-queue">
                      <div className="section-heading">
                        <h3>待播列表</h3>
                        <button onClick={() => setShowPlaylist(true)}>
                          查看列表
                          <ArrowRight />
                        </button>
                      </div>
                      {listContent}
                    </section>
                  </div>
                  <section className="lyrics-column">
                    <div className="lyrics-toolbar">
                      <div className="lyrics-label">歌词</div>
                      <div>
                        <Button
                          label="调整歌词字号"
                          onClick={() => setLargeLyrics((v) => !v)}
                          active={largeLyrics}
                        >
                          Aa
                        </Button>
                        <button
                          className={`lyric-follow ${follow ? "active" : ""}`}
                          aria-pressed={follow}
                          onClick={() => setFollow((v) => !v)}
                        >
                          跟随进度
                        </button>
                      </div>
                    </div>
                    <Lyrics
                      lines={lines}
                      text={lyricText}
                      time={time}
                      onSeek={seek}
                      large
                      follow={follow}
                      size={largeLyrics}
                    />
                    <div className="lyrics-info">
                      <Info />
                      点击歌词可跳转到对应位置
                    </div>
                  </section>
                </div>
              ) : (
                <div className="empty">
                  尚未播放曲目。返回搜索，选择一首音乐。
                </div>
              )}
            </section>
          )}
          {view === "queue" && (
            <section className="view">
              <div className="queue-toolbar">
                <span>{jobs.length} 首曲目</span>
                <button
                  className="secondary"
                  onClick={() =>
                    void api<DownloadJob[]>("/downloads")
                      .then(setJobs)
                      .catch(fail)
                  }
                >
                  刷新进度
                </button>
              </div>
              {jobs.map((job) => (
                <div className="download-row" key={job.id}>
                  <Cover track={job.track} />
                  <div>
                    <strong>{job.track.title}</strong>
                    <small>
                      {job.track.artist} · {job.quality_label}
                    </small>
                    <div className="progress">
                      <span
                        style={{
                          width:
                            job.status === "complete"
                              ? "100%"
                              : job.total_bytes
                                ? `${Math.min(100, (job.bytes / job.total_bytes) * 100)}%`
                                : "0%",
                        }}
                      />
                    </div>
                    <small>
                      {bytes(job.bytes)}
                      {job.total_bytes ? ` / ${bytes(job.total_bytes)}` : ""}
                      {job.detail && ` · ${job.detail}`}
                    </small>
                    {job.file && (
                      <a
                        href={`/api/files/content?path=${encodeURIComponent(job.file)}`}
                        download
                      >
                        保存文件
                      </a>
                    )}
                  </div>
                  <span className="badge">{job.quality_label}</span>
                  <span
                    className={`state ${job.status === "complete" ? "done" : ""}`}
                  >
                    {statusLabels[job.status]}
                  </span>
                  <div className="job-actions">
                    {["failed", "paused", "cancelled"].includes(job.status) ? (
                      <button
                        className="secondary"
                        onClick={() =>
                          void api<DownloadJob>(
                            `/downloads/${job.id}/retry`,
                            "POST",
                          )
                            .then((updated) =>
                              setJobs((old) =>
                                old.map((j) =>
                                  j.id === updated.id ? updated : j,
                                ),
                              ),
                            )
                            .catch(fail)
                        }
                      >
                        重新下载
                      </button>
                    ) : (
                      job.status !== "complete" && (
                        <>
                          <Button
                            label="暂停下载"
                            onClick={() =>
                              void api(`/downloads/${job.id}/pause`, "POST")
                                .then(() => api<DownloadJob[]>("/downloads"))
                                .then(setJobs)
                                .catch(fail)
                            }
                          >
                            <Pause />
                          </Button>
                          <Button
                            label="取消下载"
                            onClick={() =>
                              void api(`/downloads/${job.id}/cancel`, "POST")
                                .then(() => api<DownloadJob[]>("/downloads"))
                                .then(setJobs)
                                .catch(fail)
                            }
                          >
                            <X />
                          </Button>
                        </>
                      )
                    )}
                  </div>
                </div>
              ))}
              {!jobs.length && (
                <div className="empty">
                  选择曲目和音质后，下载任务将在这里显示。
                </div>
              )}
            </section>
          )}
          {view === "import" && (
            <section className="view">
              <form
                className="form-panel"
                onSubmit={(e) => {
                  e.preventDefault();
                  void importList();
                }}
              >
                <h2>导入平台歌单</h2>
                <p>选择音源，粘贴公开歌单链接或歌单 ID。</p>
                <label>
                  歌单音源
                  <select
                    value={importSource}
                    onChange={(e) => setImportSource(e.target.value)}
                  >
                    {sources.map((s) => (
                      <option value={s.id} key={s.id}>
                        {s.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  歌单链接 / ID
                  <input
                    value={importId}
                    onChange={(e) => setImportId(e.target.value)}
                    placeholder="https://music.163.com/playlist?id=…"
                    required
                  />
                </label>
                <p>
                  私有歌单可能需要配置音源 Cookie。当前最多读取音源返回的前 1000
                  首。
                </p>
                <button className="primary" disabled={busy || !importId.trim()}>
                  {busy ? "正在导入…" : "导入曲目"}
                </button>
              </form>
            </section>
          )}
          {view === "files" && (
            <section className="view">
              <div className="queue-toolbar">
                <span>{files.length} 个文件</span>
                <button
                  className="secondary"
                  onClick={() =>
                    void api<LibraryFile[]>("/files").then(setFiles).catch(fail)
                  }
                >
                  刷新音乐库
                </button>
              </div>
              <div className="file-list">
                {files.map((file) => (
                  <div key={file.path}>
                    <Folder />
                    <span>
                      <strong>{file.name}</strong>
                      <small>{file.path}</small>
                    </span>
                    <span>{bytes(file.size)}</span>
                    <a
                      className="secondary"
                      download
                      href={`/api/files/content?path=${encodeURIComponent(file.path)}`}
                    >
                      保存文件
                    </a>
                  </div>
                ))}
              </div>
              {!files.length && (
                <div className="empty">
                  音乐库为空。完成下载后，文件会显示在这里。
                </div>
              )}
            </section>
          )}
          {view === "settings" && config && (
            <section className="view">
              <form
                className="settings-form"
                onSubmit={async (e) => {
                  e.preventDefault();
                  setBusy(true);
                  try {
                    const result = await api<{ restart_required: boolean }>(
                      "/config",
                      "PUT",
                      config,
                    );
                    setNotice(
                      result.restart_required
                        ? "已保存，下载并发将在重启服务后生效"
                        : "设置已保存",
                    );
                  } catch (error) {
                    fail(error);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                <section className="settings-section">
                  <h2>搜索与播放</h2>
                  {config.engine && (
                    <p>
                      {config.engine} · 完整音源与原版解析 · {sources.length}{" "}
                      个音源可选
                    </p>
                  )}
                  <div className="settings-grid">
                    <label>
                      每个音源结果数
                      <input
                        type="number"
                        min="1"
                        max="50"
                        value={config.results_per_source}
                        onChange={(e) =>
                          setConfig({
                            ...config,
                            results_per_source: Number(e.target.value),
                          })
                        }
                      />
                    </label>
                    <label>
                      音源单次请求超时（秒）
                      <input
                        type="number"
                        min="3"
                        max="120"
                        value={config.search_timeout_seconds}
                        onChange={(e) =>
                          setConfig({
                            ...config,
                            search_timeout_seconds: Number(e.target.value),
                          })
                        }
                      />
                    </label>
                    {!config.engine && (
                      <label>
                        试听解析超时（秒）
                        <input
                          type="number"
                          min="2"
                          max="20"
                          value={config.playback_timeout_seconds}
                          onChange={(e) =>
                            setConfig({
                              ...config,
                              playback_timeout_seconds: Number(e.target.value),
                            })
                          }
                        />
                      </label>
                    )}
                  </div>
                </section>
                <section className="settings-section">
                  <h2>下载与音乐库</h2>
                  <div className="settings-grid">
                    {!config.engine && (
                      <label>
                        下载并发（重启生效）
                        <input
                          type="number"
                          min="1"
                          max="4"
                          value={config.download_concurrency}
                          onChange={(e) =>
                            setConfig({
                              ...config,
                              download_concurrency: Number(e.target.value),
                            })
                          }
                        />
                      </label>
                    )}
                    <label>
                      目录组织
                      <select
                        value={config.download_layout}
                        onChange={(e) =>
                          setConfig({
                            ...config,
                            download_layout: e.target.value,
                          })
                        }
                      >
                        {[
                          ["artist_album", "歌手 / 专辑"],
                          ["artist", "歌手"],
                          ["source", "音源"],
                          ["flat", "平铺"],
                        ].map(([id, label]) => (
                          <option value={id} key={id}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      歌词保存
                      <select
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
                          <option value={id} key={id}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                  {!config.engine && (
                    <label className="check-label">
                      <input
                        type="checkbox"
                        checked={config.embed_metadata}
                        onChange={(e) =>
                          setConfig({
                            ...config,
                            embed_metadata: e.target.checked,
                          })
                        }
                      />
                      写入曲名、歌手、专辑标签（需要 FFmpeg）
                    </label>
                  )}
                  <p>
                    音乐目录与下载并发由服务配置管理；歌曲交给对应音源的下载器处理。
                  </p>
                </section>
                <section className="settings-section">
                  <h2>全部音源与解析设置</h2>
                  <p>
                    各音源保留原版解析方式。Cookie
                    为可选项，仅在对应音源需要时填写；无法试听的结果仍可交给原下载器处理。
                  </p>
                  {sources.map((source) => {
                    const setting = config.source_settings[source.id] || {
                      cookie: "",
                      proxy: "",
                    };
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
                              checked={config.sources.includes(source.id)}
                              onChange={(e) =>
                                setConfig({
                                  ...config,
                                  sources: e.target.checked
                                    ? [...config.sources, source.id]
                                    : config.sources.filter(
                                        (s) => s !== source.id,
                                      ),
                                })
                              }
                            />
                            参与搜索
                          </label>
                        </summary>
                        <label>
                          Cookie
                          <textarea
                            autoComplete="off"
                            spellCheck={false}
                            value={setting.cookie}
                            onChange={(e) =>
                              setConfig({
                                ...config,
                                source_settings: {
                                  ...config.source_settings,
                                  [source.id]: {
                                    ...setting,
                                    cookie: e.target.value,
                                  },
                                },
                              })
                            }
                            placeholder="未配置账号"
                          />
                        </label>
                        <label>
                          HTTP / HTTPS 代理
                          <input
                            value={setting.proxy}
                            onChange={(e) =>
                              setConfig({
                                ...config,
                                source_settings: {
                                  ...config.source_settings,
                                  [source.id]: {
                                    ...setting,
                                    proxy: e.target.value,
                                  },
                                },
                              })
                            }
                            placeholder="http://127.0.0.1:7890"
                          />
                        </label>
                      </details>
                    );
                  })}
                </section>
                {config.engine && (
                  <section className="settings-section">
                    <h2>原版高级配置</h2>
                    <p>
                      保留全局与分源参数、解析规则、请求覆盖、线程数、代理范围及专用解析器配置。
                    </p>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => {
                        void api<Record<string, unknown>>("/engine/config")
                          .then((value) =>
                            setEngineConfig(JSON.stringify(value, null, 2)),
                          )
                          .catch(fail);
                      }}
                    >
                      读取完整配置
                    </button>
                    {engineConfig && (
                      <>
                        <label>
                          原版配置 JSON
                          <textarea
                            rows={16}
                            spellCheck={false}
                            value={engineConfig}
                            onChange={(event) =>
                              setEngineConfig(event.target.value)
                            }
                          />
                        </label>
                        <button
                          type="button"
                          className="secondary"
                          onClick={async () => {
                            try {
                              await api(
                                "/engine/config",
                                "PUT",
                                JSON.parse(engineConfig),
                              );
                              await load();
                              setNotice("原版完整配置已保存");
                            } catch (error) {
                              fail(error);
                            }
                          }}
                        >
                          保存完整配置
                        </button>
                      </>
                    )}
                  </section>
                )}
                <button
                  className="primary"
                  disabled={busy || !config.sources.length}
                >
                  <Check />
                  {busy ? "正在保存…" : "保存设置"}
                </button>
              </form>
            </section>
          )}
          <footer className="player">
            <button
              className="mini-track"
              onClick={() => setView("player")}
              aria-label="展开播放界面"
            >
              <Cover track={current} />
              <span>
                <strong>{current?.title || "尚未播放"}</strong>
                <small>{current?.artist || "选择一首曲目开始试听"}</small>
              </span>
            </button>
            <div className="player-center">
              <div className="transport">
                <Button
                  label="随机播放"
                  active={shuffle}
                  onClick={() => setShuffle((v) => !v)}
                >
                  <Shuffle />
                </Button>
                <Button
                  label="上一首"
                  onClick={() => step(-1)}
                  disabled={!current}
                >
                  <SkipBack />
                </Button>
                <button
                  className="pause"
                  aria-label={playing ? "暂停" : "播放"}
                  disabled={!current || loadingPlay}
                  onClick={() => void toggle()}
                >
                  {loadingPlay ? (
                    <span className="loading-dot" />
                  ) : playing ? (
                    <Pause />
                  ) : (
                    <Play />
                  )}
                </button>
                <Button
                  label="下一首"
                  onClick={() => step(1)}
                  disabled={!current}
                >
                  <SkipForward />
                </Button>
                <Button
                  label="列表循环"
                  active={repeat}
                  onClick={() => setRepeat((v) => !v)}
                >
                  <Repeat />
                </Button>
              </div>
              <div className="timeline">
                <span>{clock(time)}</span>
                <input
                  aria-label="播放进度"
                  type="range"
                  min="0"
                  max={duration || 1}
                  step=".1"
                  value={Math.min(time, duration || 1)}
                  onChange={(e) => seek(Number(e.target.value))}
                  disabled={!current}
                />
                <span>{clock(duration)}</span>
              </div>
            </div>
            <div className="player-extras">
              <Button
                label="静音"
                onClick={() => setVolume((v) => (v ? 0 : 0.65))}
              >
                <Volume2 />
              </Button>
              <input
                type="range"
                min="0"
                max="1"
                step=".01"
                value={volume}
                onChange={(e) => setVolume(Number(e.target.value))}
                aria-label="音量"
              />
              <button
                className="lyric-toggle"
                onClick={() => setView("player")}
              >
                词
              </button>
              <Button label="播放列表" onClick={() => setShowPlaylist(true)}>
                <ListMusic />
              </Button>
            </div>
          </footer>
        </main>
      </div>
      {notice && (
        <div id="toast" role="status">
          {notice}
          <button aria-label="关闭提示" onClick={() => setNotice("")}>
            <X />
          </button>
        </div>
      )}
      {qualityTracks && (
        <Modal
          title={qualityTracks.length > 1 ? "批量选择下载音质" : "选择下载音质"}
          close={() => setQualityTracks(null)}
        >
          <p>下载前验证所选资源，无损版本不可用时任务会失败。</p>
          <div className="quality-list">
            {qualityTracks.map((track) => (
              <fieldset key={track.id}>
                <legend>
                  {track.title} · {track.artist}
                </legend>
                {track.qualities.length ? (
                  track.qualities.map((q) => (
                    <label className="version-option" key={q.id}>
                      <input
                        type="radio"
                        name={track.id}
                        value={q.id}
                        checked={qualities[track.id] === q.id}
                        onChange={() =>
                          setQualities((previous) => ({
                            ...previous,
                            [track.id]: q.id,
                          }))
                        }
                      />
                      <span>
                        {q.label}
                        <small>
                          {q.availability === "login_required"
                            ? "需要账号权限"
                            : "音源标注，下载前验证"}
                        </small>
                      </span>
                    </label>
                  ))
                ) : (
                  <p>音源未提供音质信息，请选择其他曲目或音源。</p>
                )}
              </fieldset>
            ))}
          </div>
          <div className="dialog-actions">
            <button onClick={() => setQualityTracks(null)}>取消</button>
            <button
              className="primary"
              disabled={busy || qualityTracks.some((t) => !qualities[t.id])}
              onClick={() => void download()}
            >
              {busy ? "正在加入…" : "加入下载队列"}
            </button>
          </div>
        </Modal>
      )}
      {showPlaylist && (
        <Modal title="播放列表" close={() => setShowPlaylist(false)}>
          {listContent}
        </Modal>
      )}
      {loginRequired && (
        <Modal title="登录音乐库">
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              try {
                await api("/auth/login", "POST", {
                  username: loginUser,
                  password: loginPass,
                });
                setLoginRequired(false);
                setLoginPass("");
                await load();
              } catch (error) {
                fail(error);
              } finally {
                setBusy(false);
              }
            }}
          >
            <label className="form-label">
              用户名
              <input
                autoComplete="username"
                value={loginUser}
                onChange={(e) => setLoginUser(e.target.value)}
                required
              />
            </label>
            <label className="form-label">
              密码
              <input
                autoComplete="current-password"
                type="password"
                value={loginPass}
                onChange={(e) => setLoginPass(e.target.value)}
                required
              />
            </label>
            <div className="dialog-actions">
              <button className="primary" disabled={busy}>
                {busy ? "正在登录…" : "登录"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
