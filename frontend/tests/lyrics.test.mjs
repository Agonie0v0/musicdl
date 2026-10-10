import test from "node:test";
import assert from "node:assert/strict";
import { parseLrc, activeLine, clock } from "../src/lyrics.ts";
test("timestamps, repeated lines and offset follow actual audio time", () => {
  const lines = parseLrc(
    "[ar:artist]\n[offset:-500]\n[00:01.00][00:10.50]第一句\n[00:05:250]第二句\n无时间文本",
  );
  assert.deepEqual(lines, [
    { time: 0.5, text: "第一句" },
    { time: 4.75, text: "第二句" },
    { time: 10, text: "第一句" },
  ]);
  assert.equal(activeLine(lines, 0.4), -1);
  assert.equal(activeLine(lines, 5), 1);
  assert.equal(activeLine(lines, 100), 2);
  assert.equal(clock(NaN), "00:00");
});
