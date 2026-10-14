import assert from "node:assert/strict";
import { chromium, expect } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
const base = "http://127.0.0.1:18766";
const server = spawn(
  process.execPath,
  [
    "node_modules/vite/bin/vite.js",
    "preview",
    "--host",
    "127.0.0.1",
    "--port",
    "18766",
  ],
  { stdio: "ignore" },
);
let browser;
try {
  for (let i = 0; i < 50; i++) {
    try {
      if ((await fetch(base)).ok) break;
    } catch {}
    await new Promise((r) => setTimeout(r, 100));
  }
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.MUSICDL_BROWSER_EXECUTABLE || undefined,
  });
  const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
    }),
    errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/test-cover-missing.png", (route) =>
    route.fulfill({ status: 404, body: "" }),
  );
  await page.route("**/test-cover.svg", (route) =>
    route.fulfill({
      status: 200,
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="480"><rect width="480" height="480" fill="#4b477b"/><circle cx="240" cy="220" r="145" fill="#c4b2e3"/><circle cx="240" cy="220" r="90" fill="#68628e"/><circle cx="240" cy="220" r="12" fill="#4b477b"/><text x="40" y="420" fill="#fff" font-family="sans-serif" font-size="32">测试专辑 / TEST FIXTURE</text></svg>',
    }),
  );
  const track = {
    id: "test-1",
    source: "netease",
    source_id: "1",
    title: "测试曲目",
    artist: "测试歌手",
    album: "测试专辑",
    duration: 8,
    size_bytes: 31457280,
    cover: `${base}/test-cover-missing.png`,
    qualities: [
      {
        id: "lossless",
        label: "FLAC 无损",
        codec: "flac",
        bitrate: null,
        lossless: true,
        availability: "advertised",
        evidence: "source",
      },
      {
        id: "standard",
        label: "MP3 128",
        codec: "mp3",
        bitrate: 128,
        lossless: false,
        availability: "advertised",
        evidence: "source",
      },
    ],
    playback: {
      state: "ready",
      url: "/api/stream/test-1",
      codec: "wav",
      bitrate: null,
      detail: null,
      preview_only: false,
    },
  };
  const cfg = {
    engine: "musicdl test fixture",
    available_sources: [
      { id: "netease", label: "网易云" },
      { id: "migu", label: "咪咕" },
      { id: "qq", label: "QQ音乐" },
      { id: "kuwo", label: "酷我" },
      { id: "qianqian", label: "千千" },
      { id: "GDStudioMusicClient", label: "GDStudio" },
      { id: "YouTubeMusicClient", label: "YouTube", proxy_recommended: true },
      { id: "SpotifyMusicClient", label: "Spotify", proxy_recommended: true },
    ],
    sources: ["netease", "migu", "qq", "kuwo", "qianqian"],
    results_per_source: 3,
    search_timeout_seconds: 15,
    playback_timeout_seconds: 6,
    download_concurrency: 2,
    download_layout: "artist_album",
    lyrics_mode: "both",
    embed_metadata: true,
    source_settings: {},
  };
  let engineConfig = {
    music_sources: [
      "NeteaseMusicClient",
      "MiguMusicClient",
      "QQMusicClient",
      "KuwoMusicClient",
      "QianqianMusicClient",
    ],
    global_client_cfg: {
      work_dir: "/music",
      search_size_per_source: 3,
      search_size_per_page: 10,
      max_retries: 3,
      strict_limit_search_size_per_page: true,
      enable_search_curl_cffi: false,
      enable_parse_curl_cffi: false,
      enable_download_curl_cffi: false,
      random_update_ua: false,
      auto_set_proxies: false,
      maintain_session: false,
      default_parse_cookies: { preserve: "yes" },
    },
    proxy_url: "",
    proxy_scope: "all",
    proxy_sources: [],
    request_timeout: 15,
    default_threads: 10,
    download_layout: "artist_album",
    lyrics_mode: "both",
    cookies: { MiguMusicClient: { fixture: "keep" } },
    init_music_clients_cfg: { MiguMusicClient: { maintain_session: true } },
    clients_threadings: { MiguMusicClient: 7 },
    requests_overrides: {
      MiguMusicClient: { headers: { "X-Custom": "keep" } },
    },
    search_rules: { MiguMusicClient: { quality: "keep" } },
  };
  let engineSaves = 0;
  let engineSourceErrors = {};
  let logFetches = 0;
  const logEntries = [
    {
      id: 1,
      time: "2026-10-10 14:00:00",
      level: "info",
      category: "search",
      task_id: "search-fixture",
      message: "网易云搜索完成，5 个结果",
    },
    {
      id: 2,
      time: "2026-10-10 14:00:01",
      level: "error",
      category: "download",
      task_id: "download-fixture",
      message:
        "HTTP 403：下载被音源拒绝\nTraceback: provider.py line 42\n请检查音源配置",
    },
  ];
  let favorites = [],
    playlist = [],
    jobs = [],
    savedPlaylists = [];
  let localTrack = {
    ...track,
    id: "local-fixture",
    source: "local",
    title: "本地测试音乐",
    artist: "本地歌手",
    album: "本地专辑",
    cover: null,
    qualities: [],
    playback: { ...track.playback, url: "/api/files/audio?path=fixture-a.wav" },
  };
  let libraryFiles = [
    {
      path: "fixture-a.wav",
      name: "测试音乐.wav",
      size: 1024,
      root: "/music",
      folder: "通勤",
      track: localTrack,
      editable: true,
    },
    {
      path: "fixture-b.lrc",
      name: "测试歌词.lrc",
      size: 128,
      root: "/music",
      folder: "通勤",
    },
  ];
  const wav = Buffer.alloc(44 + 8000 * 2 * 8);
  wav.write("RIFF");
  wav.writeUInt32LE(wav.length - 8, 4);
  wav.write("WAVEfmt ", 8);
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(8000, 24);
  wav.writeUInt32LE(16000, 28);
  wav.writeUInt16LE(2, 32);
  wav.writeUInt16LE(16, 34);
  wav.write("data", 36);
  wav.writeUInt32LE(wav.length - 44, 40);
  const playbackReports = [];
  let brokenAudio = false;
  let botConfig = {
    public_url: "",
    link_ttl_seconds: 86400,
    bot_sources: "",
    tg_enabled: false,
    tg_api_url: "https://api.telegram.org",
    tg_proxy_mode: "direct",
    wecom_enabled: false,
    tg_allowed_users: "",
    wecom_corp_id: "",
    wecom_agent_id: "",
    wecom_allowed_users: "",
    configured: {},
    status: { tg: false, wecom: false },
    callback_url: "",
  };
  let botSaves = 0;
  const botTests = [];
  const searchRequests = [],
    cancelledSearches = [],
    searchKeywords = new Map(),
    heldSearches = new Map();
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      p = new URL(request.url()).pathname.replace("/api", ""),
      method = request.method();
    let result;
    if (p === "/auth/status") result = { authenticated: true, required: false };
    else if (p === "/auth/logout") result = { success: true };
    else if (p === "/bots/config") {
      if (method === "PUT") {
        botSaves++;
        const { clear_secrets, ...draft } = request.postDataJSON();
        if (draft.public_url && !draft.public_url.includes("://"))
          draft.public_url = "http://" + draft.public_url;
        const secretKeys = [
          "tg_token",
          "tg_proxy",
          "wecom_secret",
          "wecom_token",
          "wecom_aes_key",
        ];
        for (const key of secretKeys) {
          if (draft[key]) botConfig.configured[key] = true;
          if (clear_secrets.includes(key)) botConfig.configured[key] = false;
          delete draft[key];
        }
        botConfig = {
          ...botConfig,
          ...draft,
          status: { tg: draft.tg_enabled, wecom: draft.wecom_enabled },
          callback_url: draft.public_url + "/bot/wecom",
        };
      }
      result = botConfig;
    } else if (p === "/bots/test") {
      botTests.push(request.postDataJSON());
      result = { detail: "连接测试成功；未发送消息。" };
    } else if (p === "/playback/events") {
      const report = request.postDataJSON();
      playbackReports.push(report);
      logEntries.push({
        id: logEntries.length + 100,
        time: "2026-10-12 12:00:00",
        level: report.event === "failed" ? "error" : "info",
        category: "playback",
        task_id: report.track_id,
        message: `测试曲目 · ${report.stage} · ${report.detail} · MediaError=${report.code}`,
      });
      result = { recorded: true };
    } else if (p === "/logs") {
      logFetches++;
      result = { logs: logEntries };
    } else if (p === "/config")
      result =
        method === "PUT" ? { saved: true, restart_required: false } : cfg;
    else if (p === "/engine/config") {
      if (method === "PUT") {
        engineConfig = request.postDataJSON();
        engineSaves++;
      }
      result =
        method === "PUT"
          ? { saved: true, source_errors: engineSourceErrors }
          : engineConfig;
    } else if (p === "/favorites") result = favorites;
    else if (p.startsWith("/favorites/")) {
      favorites = method === "PUT" ? [track] : [];
      result = { saved: true };
    } else if (p === "/playlist") {
      if (method === "PUT")
        playlist = request
          .postDataJSON()
          .map((id) => (id === localTrack.id ? localTrack : track));
      result = method === "PUT" ? { saved: true } : playlist;
    } else if (p === "/playlist/import") {
      const input = request.postDataJSON();
      const list = {
        id: "collection-1",
        name: input.name || "测试歌单",
        source: "netease",
        source_url: input.id,
        created_at: 1791623400,
        tracks: [track],
        issues: [
          {
            title: "可能",
            artist: "程响",
            source_id: "1950343972",
            reason: "未获得可用音频地址，音源提示：其它版本可播。",
          },
        ],
        detail: "成功导入 1 首，1 首未获取到可用资源。",
        task_id: "collection-1",
      };
      savedPlaylists = [list];
      result = { playlist: list, tracks: list.tracks, detail: list.detail };
    } else if (p === "/playlists") {
      result = savedPlaylists.map(({ tracks, issues, ...list }) => ({
        ...list,
        track_count: tracks.length,
        issue_count: issues.length,
        cover: tracks[0]?.cover || null,
      }));
    } else if (p.startsWith("/playlists/")) {
      const list = savedPlaylists[0];
      if (p.includes("/tracks/")) {
        list.tracks = [];
        result = list;
      } else if (method === "DELETE") {
        savedPlaylists = [];
        result = { deleted: true };
      } else if (method === "PATCH") {
        list.name = request.postDataJSON().name;
        result = list;
      } else result = list;
    } else if (p === "/downloads") {
      if (method === "POST") {
        const item = request.postDataJSON()[0];
        jobs = [
          {
            id: "job-1",
            track,
            quality_id: item.quality_id,
            quality_label: "FLAC 无损",
            status: "downloading",
            bytes: 4096,
            total_bytes: 16384,
            detail: "",
            file: null,
          },
        ];
      }
      result = jobs;
    } else if (p === "/downloads/job-1" && method === "DELETE") {
      jobs = [];
      result = { removed: true };
    } else if (p.startsWith("/downloads/job-1/")) {
      jobs[0].status = p.endsWith("/pause")
        ? "paused"
        : p.endsWith("/retry") || p.endsWith("/resume")
          ? "downloading"
          : "cancelled";
      result = jobs[0];
    } else if (p === "/search/jobs") {
      const keyword = request.postDataJSON().keyword;
      searchRequests.push(keyword);
      const id = `search-${searchRequests.length}`;
      searchKeywords.set(id, keyword);
      result = { id };
    } else if (p.endsWith("/events")) {
      const id = p.split("/")[3];
      const keyword = searchKeywords.get(id);
      if (keyword === "连接失败") {
        await route.abort();
        return;
      }
      if (keyword === "慢搜索") {
        await new Promise((resolve) => heldSearches.set(id, resolve));
      }
      const body = [
        ["source", { id: "netease", status: { state: "complete", count: 1 } }],
        [
          "candidate",
          {
            ...track,
            playback: { ...track.playback, state: "pending", url: null },
          },
        ],
        ["track", track],
        ["done", { cancelled: false }],
      ]
        .map(
          ([kind, data], i) =>
            `id: ${i + 1}\nevent: ${kind}\ndata: ${JSON.stringify(data)}\n\n`,
        )
        .join("");
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body,
      });
      return;
    } else if (p.endsWith("/lyrics"))
      result = {
        text: "[00:00.00]测试歌词第一句\n[00:02.00]测试歌词第二句\n[00:05.00]测试歌词第三句",
      };
    else if (p.endsWith("/playback") || p.endsWith("/versions"))
      result = p.includes("local-fixture") ? localTrack : track;
    else if (p.startsWith("/stream/") || p === "/files/audio") {
      await route.fulfill({
        status: 200,
        contentType: "audio/wav",
        body: brokenAudio ? Buffer.from("invalid audio") : wav,
      });
      return;
    } else if (p === "/files/tags") {
      const tags = request.postDataJSON();
      localTrack = { ...localTrack, ...tags };
      libraryFiles[0] = { ...libraryFiles[0], track: localTrack };
      result = libraryFiles[0];
    } else if (p === "/files") {
      if (method === "DELETE") {
        const path = new URL(request.url()).searchParams.get("path");
        libraryFiles = libraryFiles.filter((file) => file.path !== path);
        result = { deleted: true };
      } else result = libraryFiles;
    } else if (p.startsWith("/search/jobs/")) {
      const id = p.split("/")[3];
      cancelledSearches.push(id);
      heldSearches.get(id)?.();
      heldSearches.delete(id);
      result = { cancelled: true };
    } else throw new Error(`Unhandled test API ${p}`);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(result),
    });
  });
  await page.goto(base);
  await page.emulateMedia({ colorScheme: "dark" });
  await page.waitForFunction(
    () => document.documentElement.dataset.theme === "dark",
  );
  await page.getByLabel("界面主题").selectOption("light");
  await page.waitForFunction(
    () => document.documentElement.dataset.theme === "light",
  );
  await mkdir("../output", { recursive: true });
  await page.screenshot({ path: "../output/test-ui-home.png" });
  await page.getByLabel("搜索音乐", { exact: true }).fill("测试");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await page
    .getByRole("button", { name: "播放 测试曲目", exact: true })
    .waitFor();
  assert.equal(await page.locator("tbody tr").count(), 1);
  const submitSearch = async (keyword) => {
    await page.getByLabel("搜索音乐", { exact: true }).fill(keyword);
    await page.getByRole("button", { name: "搜索", exact: true }).click();
  };
  await expect(
    page.getByRole("button", { name: "停止搜索", exact: true }),
  ).toHaveCount(0);
  await submitSearch("再次搜索");
  await expect(
    page.getByRole("button", { name: "播放 测试曲目", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "停止搜索", exact: true }),
  ).toHaveCount(0);
  await submitSearch("慢搜索");
  await expect.poll(() => heldSearches.size).toBe(1);
  await submitSearch("替换搜索");
  await expect(
    page.getByRole("button", { name: "播放 测试曲目", exact: true }),
  ).toBeVisible();
  assert.ok(cancelledSearches.includes("search-3"));
  await expect(
    page.getByRole("button", { name: "停止搜索", exact: true }),
  ).toHaveCount(0);
  await submitSearch("连接失败");
  await expect(
    page.getByText("搜索连接已断开，请重新搜索", { exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "停止搜索", exact: true }),
  ).toHaveCount(0);
  await submitSearch("测试");
  await expect(
    page.getByRole("button", { name: "播放 测试曲目", exact: true }),
  ).toBeVisible();
  assert.deepEqual(searchRequests, [
    "测试",
    "再次搜索",
    "慢搜索",
    "替换搜索",
    "连接失败",
    "测试",
  ]);
  await page.getByRole("button", { name: "仅无损", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "仅无损", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByLabel("仅可试听", { exact: true }).check();
  assert.equal(await page.locator("tbody tr").count(), 1);
  await page.getByRole("button", { name: "音质未知", exact: true }).click();
  assert.equal(await page.locator("tbody tr").count(), 0);
  await page.getByRole("button", { name: "全部", exact: true }).click();
  await page.getByLabel("仅可试听", { exact: true }).uncheck();
  for (const sort of [
    "size-desc",
    "size-asc",
    "quality-desc",
    "quality-asc",
    "artist",
    "source",
    "duration-desc",
  ]) {
    await page.getByLabel("结果排序").selectOption(sort);
    assert.equal(await page.locator("tbody tr").count(), 1);
  }
  await page.getByLabel("结果排序").selectOption("relevance");
  for (const label of [
    "收藏 测试曲目",
    "播放 测试曲目",
    "下载 测试曲目",
    "加入播放列表 测试曲目",
  ])
    await expect(page.getByLabel(label, { exact: true })).toBeVisible();
  await expect(page.getByText("30.0 MB", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "播放 测试曲目", exact: true })
    .click();
  await page.waitForFunction(
    () => document.querySelector("audio").currentTime > 0.05,
  );
  assert.equal(await page.locator("audio").evaluate((a) => a.error), null);
  await page.waitForFunction(
    () => document.querySelector(".mini-track img")?.style.display === "none",
  );
  track.cover = `${base}/test-cover.svg`;
  await page
    .getByRole("button", { name: "播放 测试曲目", exact: true })
    .click();
  await page.waitForFunction(() => {
    const image = document.querySelector(".mini-track img");
    return image?.naturalWidth > 0 && image.style.display !== "none";
  });
  await page.getByRole("button", { name: "正在播放", exact: true }).click();
  await page.getByLabel("切换随机播放", { exact: true }).click();
  assert.ok(
    await page
      .getByLabel("切换随机播放", { exact: true })
      .evaluate((e) => e.classList.contains("active")),
  );
  await page.getByLabel("切换列表循环", { exact: true }).click();
  assert.ok(
    await page
      .getByLabel("切换列表循环", { exact: true })
      .evaluate((e) => !e.classList.contains("active")),
  );
  await page.locator(".full-lyrics button").nth(1).click();
  await page
    .waitForFunction(
      () => document.querySelector("audio").currentTime >= 1.95,
      null,
      { timeout: 3000 },
    )
    .catch(async (e) => {
      console.log(
        await page.locator("audio").evaluate((a) => ({
          time: a.currentTime,
          duration: a.duration,
          ready: a.readyState,
          error: a.error?.code,
        })),
      );
      throw e;
    });
  await page.getByLabel("调整歌词字号").click();
  assert.equal(await page.locator(".full-lyrics.large").count(), 1);
  await page.setViewportSize({ width: 2534, height: 1106 });
  const lyricColumn = await page.locator(".lyrics-column").boundingBox();
  const lineText = await page
    .locator(".full-lyrics button span")
    .nth(1)
    .boundingBox();
  assert.ok(
    Math.abs(
      lineText.x + lineText.width / 2 - (lyricColumn.x + lyricColumn.width / 2),
    ) < 12,
    "wide lyrics centered within their column",
  );
  const albumSide = await page.locator(".album-column").boundingBox();
  const fullStage = await page.locator(".playback-grid").boundingBox();
  assert.ok(
    albumSide.x > fullStage.x + 100,
    "wide album is balanced within its column",
  );
  await page.screenshot({ path: "../output/test-ui-player-ultrawide.png" });
  await page.setViewportSize({ width: 1440, height: 1000 });
  assert.equal(
    await page.getByText("点击歌词可跳转到对应位置", { exact: true }).count(),
    0,
  );
  await mkdir("../output", { recursive: true });
  await page.screenshot({ path: "../output/test-ui-player.png" });
  await page.getByLabel("界面主题").selectOption("dark");
  await page.screenshot({ path: "../output/test-ui-player-dark.png" });
  await page.getByLabel("界面主题").selectOption("light");
  await page.getByRole("button", { name: "收藏当前曲目", exact: true }).click();
  assert.equal(favorites.length, 1);
  await page.getByRole("button", { name: "加入播放列表", exact: true }).click();
  assert.equal(playlist.length, 1);
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.screenshot({ path: "../output/test-ui-player-wide.png" });
  const stage = await page.locator(".playback-grid").boundingBox();
  const container = await page.locator(".full-player").boundingBox();
  assert.ok(
    stage.width >= container.width - 80,
    "player should use the available width",
  );
  const album = await page.locator(".album-column").boundingBox();
  const lyrics = await page.locator(".lyrics-column").boundingBox();
  const queue = await page.locator(".album-queue").boundingBox();
  assert.ok(
    album.x + album.width <= lyrics.x && lyrics.x + lyrics.width <= queue.x,
    "wide player has three aligned columns",
  );
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "选择下载音质", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "加入下载队列", exact: true })
    .click();
  assert.equal(jobs[0].quality_id, "lossless");
  await page
    .getByRole("button", { name: /下载队列/ })
    .first()
    .click();
  await page.getByLabel("暂停下载").click();
  await page.getByRole("button", { name: "继续下载", exact: true }).waitFor();
  assert.equal(jobs[0].status, "paused");
  await page.getByRole("button", { name: "继续下载", exact: true }).click();
  assert.equal(jobs[0].status, "downloading");
  await page.getByLabel("全选下载任务").check();
  await page.getByRole("button", { name: "暂停选中", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "继续选中", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "继续选中", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "暂停选中", exact: true }),
  ).toBeEnabled();
  assert.equal(jobs[0].status, "downloading");
  await page.getByLabel("全选下载任务").check();
  await page.getByRole("button", { name: "取消选中", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "清理已结束记录", exact: true }),
  ).toBeEnabled();
  await page.screenshot({ path: "../output/test-ui-queue-management.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.screenshot({
    path: "../output/test-ui-queue-management-mobile.png",
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page
    .getByRole("button", { name: "清理已结束记录", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("音乐文件会保留");
  await page.getByRole("button", { name: "确认操作", exact: true }).click();
  await expect(page.locator(".download-row")).toHaveCount(0);
  await page.getByRole("button", { name: "音乐文件", exact: true }).click();
  await expect(page.locator(".music-table tbody tr")).toHaveCount(1);
  await expect(page.getByText("本地测试音乐", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "通勤", exact: true }).click();
  await page.getByLabel("播放本地歌曲 本地测试音乐", { exact: true }).click();
  await page.waitForFunction(
    () => document.querySelector("audio").currentTime > 0.05,
  );
  assert.ok(
    await page
      .locator("audio")
      .evaluate((audio) => audio.src.includes("/files/audio")),
  );
  await page.getByLabel("编辑歌曲 本地测试音乐", { exact: true }).click();
  await page.getByLabel("歌曲标题", { exact: true }).fill("修改后的本地歌曲");
  await page.getByRole("button", { name: "保存歌曲信息", exact: true }).click();
  await expect(page.locator(".music-table")).toContainText("修改后的本地歌曲");
  await page.getByRole("button", { name: "播放全部", exact: true }).click();
  await page.waitForFunction(() => !document.querySelector("audio").paused);
  assert.equal(playlist[0].id, "local-fixture");
  await page.getByLabel("搜索音乐文件").fill("音乐");
  await expect(page.locator(".music-table tbody tr")).toHaveCount(1);
  await page.getByLabel("全选音乐文件").check();
  await page.screenshot({ path: "../output/test-ui-file-management.png" });
  const localDuration = await page
    .locator(".music-table tbody .library-duration")
    .boundingBox();
  const localActions = await page.locator(".library-actions").boundingBox();
  assert.ok(
    localDuration.x + localDuration.width <= localActions.x,
    "local song actions do not overlap duration",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.screenshot({
    path: "../output/test-ui-file-management-mobile.png",
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "删除选中文件", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("永久删除 1 个文件");
  await page.getByRole("button", { name: "取消", exact: true }).click();
  assert.equal(libraryFiles.length, 2);
  await page.getByRole("button", { name: "删除选中文件", exact: true }).click();
  await page.getByRole("button", { name: "确认操作", exact: true }).click();
  await expect(page.locator(".music-table tbody tr")).toHaveCount(0);
  await page.getByLabel("搜索音乐文件").fill("");
  await page.getByRole("button", { name: "附属文件", exact: true }).click();
  await expect(page.locator(".music-table tbody tr")).toHaveCount(1);
  await page.getByRole("button", { name: "搜索音乐", exact: true }).click();
  await page.getByLabel("播放 测试曲目", { exact: true }).click();
  await page.getByLabel("加入播放列表 测试曲目", { exact: true }).click();
  if (await page.getByLabel("关闭提示", { exact: true }).count())
    await page.getByLabel("关闭提示", { exact: true }).click();
  await page.screenshot({ path: "../output/test-ui-search.png" });
  await page.getByLabel("界面主题").selectOption("dark");
  await page.screenshot({ path: "../output/test-ui-search-dark.png" });
  await page.getByLabel("界面主题").selectOption("light");
  await page.keyboard.press("Control+k");
  await expect(page.getByLabel("搜索音乐", { exact: true })).toBeFocused();
  for (const width of [390, 768, 900, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      `overflow at ${width}`,
    );
    if (width <= 900) {
      const titleBox = await page.locator(".song-name").boundingBox();
      assert.ok(titleBox.width >= 80, `title squeezed at ${width}`);
      const actionBox = await page
        .getByLabel("下载 测试曲目", { exact: true })
        .boundingBox();
      assert.ok(actionBox.height >= 44, `small touch target at ${width}`);
    }
    if (width === 768)
      await page.screenshot({ path: "../output/test-ui-tablet.png" });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByLabel("播放列表", { exact: true }).click();
  await page.getByRole("dialog").getByLabel("移除 测试曲目").waitFor();
  await page.getByRole("dialog").getByLabel("关闭", { exact: true }).click();
  await page.getByRole("button", { name: "正在播放", exact: true }).click();
  await page.locator(".full-lyrics button").nth(2).click();
  await expect(page.getByLabel("切换随机播放", { exact: true })).toBeVisible();
  await expect(page.getByLabel("切换列表循环", { exact: true })).toBeVisible();
  await page.waitForFunction(() =>
    document
      .querySelector(".full-lyrics button.active")
      ?.textContent.includes("第三句"),
  );
  await page.waitForFunction(() => {
    const container = document.querySelector(".full-lyrics");
    const active = container?.querySelector("button.active");
    if (!container || !active) return false;
    const c = container.getBoundingClientRect(),
      a = active.getBoundingClientRect();
    return Math.abs(a.top + a.height / 2 - (c.top + c.height / 2)) < 80;
  });
  await page.screenshot({ path: "../output/test-ui-player-mobile.png" });
  await page.getByRole("button", { name: "搜索音乐", exact: true }).click();
  if (await page.getByLabel("关闭提示", { exact: true }).count())
    await page.getByLabel("关闭提示", { exact: true }).click();
  await page.screenshot({ path: "../output/test-ui-mobile.png" });
  for (const label of [
    "收藏 测试曲目",
    "播放 测试曲目",
    "下载 测试曲目",
    "加入播放列表 测试曲目",
  ])
    await expect(page.getByLabel(label, { exact: true })).toBeVisible();
  await page.getByLabel("加入播放列表 测试曲目", { exact: true }).click();
  await page.getByLabel("下载 测试曲目", { exact: true }).click();
  await page.getByRole("dialog").getByLabel("关闭", { exact: true }).click();
  await page.getByRole("button", { name: "音源与设置", exact: true }).click();
  await page.locator(".source-setting").first().waitFor({ state: "attached" });
  assert.equal(await page.locator(".source-setting").count(), 8);
  const sourceDisclosure = page.locator(".settings-sources .source-disclosure");
  await expect(sourceDisclosure).not.toHaveAttribute("open");
  await sourceDisclosure.locator("summary").first().click();
  await expect(page.locator(".settings-advanced")).not.toHaveAttribute("open");
  await page.getByRole("button", { name: "取消全选", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "保存设置", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "全选", exact: true }).click();
  assert.equal(
    await page.locator(".source-setting input[type=checkbox]:checked").count(),
    8,
  );
  await page.getByLabel("启用 GDStudio", { exact: true }).uncheck();
  await page.locator(".settings-advanced > summary").click();
  assert.ok(await page.getByText("GDStudio", { exact: true }).isVisible());
  await page.getByLabel("每页大小", { exact: true }).fill("25");
  await page.getByLabel("最大重试次数", { exact: true }).fill("5");
  await page.getByLabel("默认下载线程数", { exact: true }).fill("12");
  await page
    .getByLabel("请求超时（秒，0 表示不限）", { exact: true })
    .fill("0");
  await page.getByLabel("搜索使用浏览器指纹", { exact: false }).check();
  await page
    .getByLabel("音源代理作用范围", { exact: true })
    .selectOption("selected");
  const proxyDisclosure = page.locator(".settings-proxy .source-disclosure");
  await expect(proxyDisclosure).not.toHaveAttribute("open");
  await proxyDisclosure.locator("summary").click();
  await page.getByRole("button", { name: "选择推荐音源", exact: true }).click();
  await expect(
    page.locator(".source-picker").getByLabel("YouTube", { exact: false }),
  ).toBeChecked();
  await expect(
    page.locator(".source-picker").getByLabel("Spotify", { exact: false }),
  ).toBeChecked();
  await expect(
    page.locator(".source-picker").getByLabel("网易云", { exact: true }),
  ).not.toBeChecked();
  await page.getByRole("button", { name: "清空代理选择", exact: true }).click();
  await page
    .locator(".source-picker")
    .getByLabel("GDStudio", { exact: true })
    .check();
  await page
    .getByLabel("项目 HTTP / HTTPS 代理", { exact: true })
    .fill("http://localhost:7890");
  const neteaseSettings = page.locator(".source-setting").first();
  await neteaseSettings.locator("summary").click();
  await neteaseSettings
    .getByLabel("Cookie", { exact: true })
    .fill("fixture=netease");
  await neteaseSettings.getByLabel("分源下载线程数", { exact: true }).fill("9");
  await neteaseSettings
    .getByLabel("音源代理", { exact: true })
    .selectOption("project");
  await page
    .getByLabel("分源搜索规则（search_rules）", { exact: false })
    .fill("{");
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("JSON 格式无效");
  assert.equal(engineSaves, 0);
  await page
    .getByLabel("分源搜索规则（search_rules）", { exact: false })
    .fill('{"MiguMusicClient":{"quality":"keep"}}');
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(
    page.locator(".settings-save").getByRole("status"),
  ).toContainText("全部设置已保存并生效");
  assert.equal(engineConfig.global_client_cfg.search_size_per_page, 25);
  assert.equal(engineConfig.global_client_cfg.max_retries, 5);
  assert.equal(engineConfig.global_client_cfg.enable_search_curl_cffi, true);
  assert.deepEqual(engineConfig.global_client_cfg.default_parse_cookies, {
    preserve: "yes",
  });
  assert.equal(engineConfig.default_threads, 12);
  assert.equal(engineConfig.request_timeout, 0);
  assert.deepEqual(engineConfig.proxy_sources, [
    "GDStudioMusicClient",
    "NeteaseMusicClient",
  ]);
  assert.deepEqual(engineConfig.cookies.MiguMusicClient, { fixture: "keep" });
  assert.deepEqual(engineConfig.init_music_clients_cfg.MiguMusicClient, {
    maintain_session: true,
  });
  assert.equal(engineConfig.clients_threadings.MiguMusicClient, 7);
  assert.equal(engineConfig.clients_threadings.NeteaseMusicClient, 9);
  assert.equal(engineConfig.cookies.NeteaseMusicClient, "fixture=netease");
  assert.equal(
    engineConfig.requests_overrides.NeteaseMusicClient.proxies,
    undefined,
  );
  assert.deepEqual(engineConfig.requests_overrides.MiguMusicClient.headers, {
    "X-Custom": "keep",
  });
  engineSourceErrors = { GDStudioMusicClient: "需要补全分源配置" };
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(
    page.locator(".settings-save").getByRole("status"),
  ).toContainText("设置已保存，1 个音源暂不可用，其他音源已生效");
  await expect(
    page.locator(".settings-save").getByRole("status"),
  ).toContainText("GDStudio：需要补全分源配置");
  engineSourceErrors = {};
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(
    page.locator(".settings-save").getByRole("status"),
  ).toContainText("全部设置已保存并生效");
  await page.locator(".settings-advanced > summary").click();
  await sourceDisclosure
    .getByRole("button", { name: "完成选择，收起" })
    .click();
  await proxyDisclosure.getByRole("button", { name: "完成选择，收起" }).click();
  await expect(sourceDisclosure).not.toHaveAttribute("open");
  await expect(proxyDisclosure).not.toHaveAttribute("open");
  await expect(proxyDisclosure.locator("summary")).toContainText("已选 2 个");
  const numberWidth = await page
    .getByLabel("每页大小", { exact: true })
    .evaluate((el) => el.getBoundingClientRect().width);
  assert.ok(numberWidth <= 120, "numeric settings remain compact");
  for (const theme of ["light", "dark"]) {
    await page.getByLabel("界面主题").selectOption(theme);
    for (const width of [390, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      const material = await page.locator(".player").evaluate((el) => {
        const style = getComputedStyle(el);
        return {
          background: style.backgroundColor,
          blur: style.backdropFilter,
          opacity: style.opacity,
        };
      });
      assert.ok(
        material.background.includes(theme === "light" ? "0.42" : "0.48"),
        "player reveals the page in both themes and layouts",
      );
      assert.equal(material.blur, "none", "page content is not blurred away");
      assert.equal(material.opacity, "1", "player controls keep full contrast");
    }
  }
  await page.getByLabel("界面主题").selectOption("light");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.evaluate(() => scrollTo(0, 0));
  for (const width of [768, 1200, 1440, 1920, 2560]) {
    await page.setViewportSize({ width, height: 1000 });
    const form = await page.locator(".settings-form").boundingBox();
    const view = await page.locator(".view").boundingBox();
    assert.ok(
      Math.abs(form.width - view.width) < 1,
      "settings use available space",
    );
    const basics = await page.locator(".settings-basics").boundingBox();
    const proxy = await page.locator(".settings-proxy").boundingBox();
    if (width > 1200) {
      assert.equal(basics.y, proxy.y, "basic and proxy settings share a row");
      assert.ok(
        basics.x + basics.width < proxy.x,
        "settings columns do not overlap",
      );
    } else {
      assert.ok(
        proxy.y >= basics.y + basics.height,
        "settings stack on smaller screens",
      );
    }
    const player = await page.locator(".player").boundingBox();
    assert.ok(player.width <= 1240, "bottom player has a bounded width");
    assert.ok(
      Math.abs(player.x + player.width / 2 - width / 2) < 1,
      "bottom player is centered",
    );
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      `settings overflow at ${width}`,
    );
  }
  await page.setViewportSize({ width: 1920, height: 1000 });
  await page.screenshot({ path: "../output/test-ui-settings-wide.png" });
  await page.getByLabel("界面主题").selectOption("dark");
  await page.screenshot({ path: "../output/test-ui-settings-dark.png" });
  await page.getByLabel("界面主题").selectOption("light");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({
    path: "../output/test-ui-settings.png",
    fullPage: true,
    style: ".player, #toast { visibility: hidden; }",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "../output/test-ui-settings-mobile.png",
    fullPage: true,
    style: ".player, #toast { visibility: hidden; }",
  });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.reload();
  await page.waitForFunction(
    () => document.documentElement.dataset.theme === "light",
  );
  assert.ok(await page.getByText("最近搜索", { exact: true }).isVisible());
  await page.getByLabel("界面主题").selectOption("system");
  await page.waitForFunction(
    () => document.documentElement.dataset.theme === "dark",
  );
  await page.emulateMedia({ colorScheme: "light" });
  await page.waitForFunction(
    () => document.documentElement.dataset.theme === "light",
  );
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "运行日志", exact: true }).click();
  await expect(page.getByRole("log")).toContainText("HTTP 403");
  await page.getByLabel("日志级别").selectOption("error");
  assert.equal(await page.locator(".log-row").count(), 1);
  await page.getByLabel("日志类别").selectOption("search");
  assert.equal(await page.locator(".log-row").count(), 0);
  await page.getByLabel("日志类别").selectOption("all");
  await page.getByLabel("搜索日志").fill("download-fixture");
  assert.equal(await page.locator(".log-row").count(), 1);
  const exported = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出当前日志", exact: true }).click();
  assert.ok((await exported).suggestedFilename().endsWith(".log"));
  await page.getByRole("button", { name: "暂停更新", exact: true }).click();
  const frozen = logFetches;
  await page.waitForTimeout(1100);
  assert.equal(logFetches, frozen);
  await page.getByLabel("搜索日志").fill("");
  await page.getByLabel("日志级别").selectOption("all");
  logEntries.push({
    id: 3,
    time: "2026-10-10 14:00:02",
    level: "warning",
    category: "download",
    task_id: "download-fixture",
    message: "网络重试，已保留下载进度",
  });
  await page.getByRole("button", { name: "继续更新", exact: true }).click();
  await expect(page.getByRole("log")).toContainText("网络重试");
  await page.screenshot({ path: "../output/test-ui-logs.png" });
  await page.getByLabel("界面主题").selectOption("dark");
  await page.screenshot({ path: "../output/test-ui-logs-dark.png" });
  await page.getByLabel("界面主题").selectOption("light");
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.screenshot({ path: "../output/test-ui-logs-mobile.png" });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.getByRole("button", { name: "歌单导入", exact: true }).click();
  await page.getByLabel("歌单名称（可选）").fill("通勤歌单");
  await page.getByLabel("歌单链接 / ID").fill("1234");
  await page
    .getByRole("button", { name: "导入并保存歌单", exact: true })
    .click();
  await expect(page.locator(".playlist-heading h2")).toHaveText("通勤歌单");
  await expect(page.locator(".playlist-issues")).toContainText("可能");
  await expect(page.locator(".playlist-issues")).toContainText("其它版本可播");
  assert.equal(await page.locator(".track-table tbody tr").count(), 1);
  await expect(
    page.getByRole("button", { name: "移出歌单 测试曲目", exact: true }),
  ).toBeVisible();
  await page.screenshot({ path: "../output/test-ui-playlist-detail.png" });
  await page.getByRole("button", { name: "重命名", exact: true }).click();
  await page.getByLabel("歌单名称", { exact: true }).fill("晚间歌单");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator(".playlist-heading h2")).toHaveText("晚间歌单");
  await page.getByRole("button", { name: "所有歌单", exact: true }).click();
  await expect(page.locator(".saved-playlist")).toContainText("晚间歌单");
  await page.screenshot({ path: "../output/test-ui-playlist-library.png" });
  await page.reload();
  await page.getByRole("button", { name: "我的歌单", exact: true }).click();
  await expect(page.locator(".saved-playlist")).toContainText("晚间歌单");
  await page.locator(".saved-playlist").click();
  await expect(page.locator(".playlist-heading h2")).toHaveText("晚间歌单");
  await page.getByRole("button", { name: "播放全部", exact: true }).click();
  await page.waitForFunction(() => !document.querySelector("audio").paused);
  await expect(page.locator(".playlist-heading h2")).toHaveText("晚间歌单");
  await page.getByLabel("界面主题").selectOption("dark");
  await page.screenshot({ path: "../output/test-ui-playlist-dark.png" });
  await page.getByLabel("界面主题").selectOption("light");
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.screenshot({
    path: "../output/test-ui-playlist-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.getByRole("button", { name: "下载全部", exact: true }).click();
  await page.getByRole("button", { name: "加入下载队列", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page
    .getByRole("button", { name: "移出歌单 测试曲目", exact: true })
    .click();
  await expect(page.locator(".track-table tbody tr")).toHaveCount(0);
  await page.getByRole("button", { name: "删除歌单", exact: true }).click();
  await page.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(page.locator(".saved-playlist")).toHaveCount(0);
  await expect(page.locator(".library-empty")).toBeVisible();
  await page.getByRole("button", { name: "通知与机器人", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "测试 Telegram 连接", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "测试企业微信连接", exact: true }),
  ).toBeEnabled();
  await page
    .getByLabel("Telegram Bot Token", { exact: true })
    .fill("fixture-bot-token");
  await page
    .getByLabel("Telegram 代理", { exact: true })
    .selectOption("project");
  await page
    .getByRole("button", { name: "测试 Telegram 连接", exact: true })
    .click();
  await expect(
    page.getByText("连接测试成功；未发送消息。", { exact: true }),
  ).toBeVisible();
  assert.equal(botSaves, 0, "testing does not save the draft");
  assert.equal(botTests.length, 1);
  assert.equal(botTests[0].channel, "tg");
  assert.equal(botTests[0].config.tg_token, "fixture-bot-token");
  assert.equal(botTests[0].config.tg_proxy_mode, "project");
  assert.equal(botTests[0].config.public_url, "");
  assert.equal(botTests[0].config.tg_allowed_users, "");
  assert.equal(botTests[0].config.tg_enabled, false);
  assert.deepEqual(botTests[0].config.clear_secrets, []);
  await expect(
    page.getByLabel("Telegram Bot Token", { exact: true }),
  ).toHaveValue("fixture-bot-token");
  await page
    .getByLabel("公网访问地址", { exact: true })
    .fill("192.168.1.20:8765");
  await expect(
    page.getByLabel("企业微信回调地址", { exact: true }),
  ).toHaveValue("http://192.168.1.20:8765/bot/wecom");
  await page.getByLabel("启用 Telegram", { exact: true }).check();
  await page.getByLabel("Telegram 用户白名单", { exact: true }).fill("123456");
  await page
    .getByLabel("企业微信 CorpID", { exact: true })
    .fill("corp-fixture");
  await page.getByLabel("企业微信 AgentID", { exact: true }).fill("100001");
  await page
    .getByLabel("企业微信 Secret", { exact: true })
    .fill("fixture-secret");
  await page
    .getByRole("button", { name: "测试企业微信连接", exact: true })
    .click();
  await expect(
    page.getByText("连接测试成功；未发送消息。", { exact: true }),
  ).toBeVisible();
  assert.equal(botSaves, 0);
  assert.equal(botTests.length, 2);
  assert.equal(botTests[1].channel, "wecom");
  assert.equal(botTests[1].config.wecom_corp_id, "corp-fixture");
  assert.equal(botTests[1].config.wecom_agent_id, "100001");
  assert.equal(botTests[1].config.wecom_secret, "fixture-secret");
  assert.equal(botTests[1].config.wecom_enabled, false);
  assert.equal(botTests[1].config.wecom_allowed_users, "");
  assert.equal(botTests[1].config.wecom_token, "");
  assert.equal(botTests[1].config.wecom_aes_key, "");
  await page.getByLabel("启用企业微信", { exact: true }).check();
  await page
    .getByLabel("企业微信回调 Token", { exact: true })
    .fill("fixture-token");
  await page
    .getByLabel("企业微信 EncodingAESKey", { exact: true })
    .fill("a".repeat(43));
  await page.getByLabel("企业微信用户白名单", { exact: true }).fill("alice");
  await page.getByRole("button", { name: "保存通知配置", exact: true }).click();
  await expect(
    page.getByText("通知渠道已保存并立即生效，无需重启服务", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Telegram Bot Token", { exact: true }),
  ).toHaveValue("");
  assert.equal(botSaves, 1);
  await expect(page.getByLabel("公网访问地址", { exact: true })).toHaveValue(
    "http://192.168.1.20:8765",
  );
  await expect(
    page.getByLabel("企业微信回调地址", { exact: true }),
  ).toHaveValue("http://192.168.1.20:8765/bot/wecom");
  await page
    .getByRole("button", { name: "测试 Telegram 连接", exact: true })
    .click();
  await expect(
    page.getByText("连接测试成功；未发送消息。", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "测试企业微信连接", exact: true })
    .click();
  await expect(
    page.getByText("连接测试成功；未发送消息。", { exact: true }),
  ).toBeVisible();
  assert.equal(botTests.length, 4);
  assert.equal(botTests[2].config.tg_token, "");
  assert.equal(botTests[3].config.wecom_secret, "");
  assert.equal(botSaves, 1);
  await expect(page.getByLabel("Telegram 代理", { exact: true })).toHaveValue(
    "project",
  );
  await page.screenshot({ path: "../output/test-ui-bots.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  for (const width of [320, 390, 600]) {
    await page.setViewportSize({ width, height: 844 });
    assert.ok(
      await page.locator(".sidebar nav").evaluate((nav) => {
        nav.scrollLeft = nav.scrollWidth;
        const scrolls = nav.scrollWidth > nav.clientWidth && nav.scrollLeft > 0;
        nav.scrollLeft = 0;
        return scrolls;
      }),
      "mobile navigation remains horizontally scrollable",
    );
    const projectLink = page.getByRole("link", {
      name: "项目地址（在新标签页打开）",
    });
    await expect(projectLink).toBeVisible();
    await expect(projectLink).toHaveAttribute(
      "href",
      "https://github.com/Agonie0v0/musicdl",
    );
    await expect(projectLink).toHaveAttribute("target", "_blank");
    const navButtons = page.locator(".sidebar-bottom .nav-item");
    await expect(navButtons).toHaveCount(3);
    const boxes = [];
    for (const button of await navButtons.all()) {
      await expect(button).toBeVisible();
      const box = await button.boundingBox();
      assert.ok(box.width > 44 && box.height >= 44);
      assert.ok(box.x >= 0 && box.x + box.width <= width);
      for (const other of boxes)
        assert.ok(
          box.x >= other.x + other.width ||
            other.x >= box.x + box.width ||
            box.y >= other.y + other.height ||
            other.y >= box.y + box.height,
        );
      boxes.push(box);
    }
    await expect(
      page
        .locator(".top")
        .getByRole("button", { name: "音源设置", exact: true }),
    ).toHaveCount(0);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "../output/test-ui-bots-mobile.png",
    fullPage: true,
  });
  await page.reload();
  await page.getByRole("button", { name: "通知与机器人", exact: true }).click();
  await expect(page.getByLabel("启用 Telegram", { exact: true })).toBeChecked();
  await expect(
    page.getByLabel("Telegram Bot Token", { exact: true }),
  ).toHaveValue("");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "搜索音乐", exact: true }).click();
  await submitSearch("错误试听");
  brokenAudio = true;
  await page
    .getByRole("button", { name: "播放 测试曲目", exact: true })
    .click();
  await expect
    .poll(() =>
      playbackReports.some(
        (report) => report.event === "failed" && report.code === 4,
      ),
    )
    .toBe(true);
  await page.getByRole("button", { name: "运行日志", exact: true }).click();
  await page.getByLabel("日志类别", { exact: true }).selectOption("playback");
  await expect(page.locator(".log-lines")).toContainText("MediaError=4");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/auth/status", (route) =>
    route.fulfill({ json: { required: true, authenticated: false } }),
  );
  const logoutRequest = page.waitForRequest(
    (request) =>
      request.url().endsWith("/api/auth/logout") && request.method() === "POST",
  );
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await logoutRequest;
  await expect(page.getByRole("dialog", { name: "登录音乐库" })).toBeVisible();
  assert.deepEqual(errors, []);
  console.log(
    "Browser integration passed: search, real audio, lyric seeking, favorites, playlist, quality selection, queue controls, settings, responsive layout.",
  );
} finally {
  await browser?.close();
  server.kill();
}
