export interface LyricLine {
  time: number;
  text: string;
}
export function parseLrc(text: string): LyricLine[] {
  const offset = Number(text.match(/\[offset:([+-]?\d+)\]/i)?.[1] || 0) / 1000;
  const lines: LyricLine[] = [];
  for (const line of text.split(/\r?\n/)) {
    const matches = [
      ...line.matchAll(/\[(\d+):([0-5]?\d)(?:[.:](\d{1,3}))?\]/g),
    ];
    const content = line.replace(/\[[^\]]*\]/g, "").trim();
    if (!content) continue;
    for (const match of matches)
      lines.push({
        time: Math.max(
          0,
          Number(match[1]) * 60 +
            Number(match[2]) +
            Number(`0.${match[3] || "0"}`) +
            offset,
        ),
        text: content,
      });
  }
  return lines.sort((a, b) => a.time - b.time);
}
export function activeLine(lines: LyricLine[], time: number) {
  let low = 0,
    high = lines.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (lines[middle].time <= time) low = middle + 1;
    else high = middle;
  }
  return low - 1;
}
export function clock(seconds: number) {
  const value = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  return `${Math.floor(value / 60)
    .toString()
    .padStart(2, "0")}:${(value % 60).toString().padStart(2, "0")}`;
}
