import test from "node:test";
import assert from "node:assert/strict";
import { logTime } from "../src/log-time.ts";

test("worker log times convert to the browser timezone", () => {
  const previous = process.env.TZ;
  try {
    process.env.TZ = "Asia/Shanghai";
    assert.equal(logTime("2026-10-14T03:01:50+00:00"), "2026-10-14 11:01:50");
    assert.equal(logTime("2026-10-14T11:01:50+08:00"), "2026-10-14 11:01:50");
    process.env.TZ = "America/Los_Angeles";
    assert.equal(logTime("2026-10-14T03:01:50+00:00"), "2026-10-13 20:01:50");
    assert.equal(logTime("invalid timestamp"), "invalid timestamp");
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
});
