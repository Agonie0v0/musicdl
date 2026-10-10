import assert from "node:assert/strict";
import { chromium } from "@playwright/test";
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
  const track = {
    id: "test-1",
    source: "netease",
    source_id: "1",
    title: "测试曲目",
    artist: "测试歌手",
    album: "测试专辑",
    duration: 8,
    cover: null,
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
  let favorites = [],
    playlist = [],
    jobs = [];
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
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      p = new URL(request.url()).pathname.replace("/api", ""),
      method = request.method();
    let result;
    if (p === "/auth/status") result = { authenticated: true, required: false };
    else if (p === "/config")
      result =
        method === "PUT" ? { saved: true, restart_required: false } : cfg;
    else if (p === "/favorites") result = favorites;
    else if (p.startsWith("/favorites/")) {
      favorites = method === "PUT" ? [track] : [];
      result = { saved: true };
    } else if (p === "/playlist") {
      if (method === "PUT")
        playlist = request.postDataJSON().length ? [track] : [];
      result = method === "PUT" ? { saved: true } : playlist;
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
    } else if (p.startsWith("/downloads/job-1/")) {
      jobs[0].status = p.endsWith("/pause")
        ? "paused"
        : p.endsWith("/retry")
          ? "downloading"
          : "cancelled";
      result = jobs[0];
    } else if (p === "/search/jobs") result = { id: "search-1" };
    else if (p.endsWith("/events")) {
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
    else if (p.endsWith("/playback") || p.endsWith("/versions")) result = track;
    else if (p.startsWith("/stream/")) {
      await route.fulfill({ status: 200, contentType: "audio/wav", body: wav });
      return;
    } else if (p === "/files") result = [];
    else if (p.startsWith("/search/jobs/")) result = { cancelled: true };
    else throw new Error(`Unhandled test API ${p}`);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(result),
    });
  });
  await page.goto(base);
  await page.getByLabel("搜索音乐", { exact: true }).fill("测试");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await page
    .getByRole("button", { name: "播放 测试曲目", exact: true })
    .waitFor();
  assert.equal(await page.locator("tbody tr").count(), 1);
  await page
    .getByRole("button", { name: "播放 测试曲目", exact: true })
    .click();
  await page.waitForFunction(
    () => document.querySelector("audio").currentTime > 0.05,
  );
  assert.equal(await page.locator("audio").evaluate((a) => a.error), null);
  await page.getByRole("button", { name: "正在播放", exact: true }).click();
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
  await mkdir("../output", { recursive: true });
  await page.screenshot({ path: "../output/test-ui-player.png" });
  await page.getByRole("button", { name: "收藏当前曲目", exact: true }).click();
  assert.equal(favorites.length, 1);
  await page.getByRole("button", { name: "加入播放列表", exact: true }).click();
  assert.equal(playlist.length, 1);
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
  await page.getByRole("button", { name: "重新下载", exact: true }).waitFor();
  assert.equal(jobs[0].status, "paused");
  await page.getByRole("button", { name: "重新下载", exact: true }).click();
  assert.equal(jobs[0].status, "downloading");
  await page.getByRole("button", { name: "搜索音乐", exact: true }).click();
  await page.screenshot({ path: "../output/test-ui-search.png" });
  for (const width of [390, 768, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      `overflow at ${width}`,
    );
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByLabel("播放列表", { exact: true }).click();
  await page.getByRole("dialog").getByLabel("移除 测试曲目").waitFor();
  await page.getByRole("dialog").getByLabel("关闭", { exact: true }).click();
  await page.getByRole("button", { name: "正在播放", exact: true }).click();
  await page.locator(".full-lyrics button").nth(2).click();
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
  await page.screenshot({ path: "../output/test-ui-mobile.png" });
  await page.getByRole("button", { name: "音源与设置", exact: true }).click();
  assert.equal(await page.locator(".source-setting").count(), 6);
  assert.ok(await page.getByText("GDStudio", { exact: true }).isVisible());
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  assert.deepEqual(errors, []);
  console.log(
    "Browser integration passed: search, real audio, lyric seeking, favorites, playlist, quality selection, queue controls, settings, responsive layout.",
  );
} finally {
  await browser?.close();
  server.kill();
}
