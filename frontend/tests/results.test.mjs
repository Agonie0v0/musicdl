import test from "node:test";
import assert from "node:assert/strict";
import { selectResults } from "../src/results.ts";

const make = (id, codec, lossless, bitrate, size, ready = true) => ({
  id,
  title: id,
  artist: id,
  source: "netease",
  duration: 100,
  size_bytes: size,
  qualities: codec ? [{ codec, lossless, bitrate }] : [],
  playback: { state: ready ? "ready" : "download_only" },
});
const tracks = [
  make("mp3-128", "mp3", false, 128, 4),
  make("unknown", "", false, null, null),
  make("flac", "flac", true, null, 40, false),
  make("mp3-320", "mp3", false, 320, 10),
];
const ids = (quality, ready, sort) =>
  selectResults(tracks, "all", quality, ready, sort).map((t) => t.id);

test("quality and playable filters combine independently", () => {
  assert.deepEqual(ids("lossless", false, "relevance"), ["flac"]);
  assert.deepEqual(ids("lossless", true, "relevance"), []);
  assert.deepEqual(ids("mp3", true, "relevance"), ["mp3-128", "mp3-320"]);
  assert.deepEqual(ids("unknown", false, "relevance"), ["unknown"]);
  assert.equal(tracks.length, 4);
});

test("size and quality sort in both directions, unknowns last", () => {
  assert.deepEqual(ids("all", false, "size-desc"), [
    "flac",
    "mp3-320",
    "mp3-128",
    "unknown",
  ]);
  assert.deepEqual(ids("all", false, "size-asc"), [
    "mp3-128",
    "mp3-320",
    "flac",
    "unknown",
  ]);
  assert.deepEqual(ids("all", false, "quality-desc"), [
    "flac",
    "mp3-320",
    "mp3-128",
    "unknown",
  ]);
  assert.deepEqual(ids("all", false, "quality-asc"), [
    "mp3-128",
    "mp3-320",
    "flac",
    "unknown",
  ]);
  assert.deepEqual(
    tracks.map((t) => t.id),
    ["mp3-128", "unknown", "flac", "mp3-320"],
  );
});
