import { useEffect, useRef, type ReactNode } from "react";
import { Music2, X } from "lucide-react";
import { activeLine, clock, type LyricLine } from "./lyrics";
import type { Track } from "./types";
export function Cover({
  track,
  large = false,
}: {
  track?: Track | null;
  large?: boolean;
}) {
  return (
    <span className={large ? "album-art" : "cover"}>
      {track?.cover && (
        <img
          src={track.cover}
          alt=""
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={(e) => (e.currentTarget.style.display = "none")}
        />
      )}
      <Music2 aria-hidden="true" />
    </span>
  );
}
export function Button({
  label,
  children,
  onClick,
  active = false,
  disabled = false,
}: {
  label: string;
  children: ReactNode;
  onClick: () => void;
  active?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      className={`icon-button ${active ? "active" : ""}`}
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}
export function Lyrics({
  lines,
  text,
  time,
  onSeek,
  large = false,
  follow = true,
  size = false,
}: {
  lines: LyricLine[];
  text: string;
  time: number;
  onSeek: (s: number) => void;
  large?: boolean;
  follow?: boolean;
  size?: boolean;
}) {
  const current = activeLine(lines, time);
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const center = () => {
      if (follow && current >= 0) {
        const element = container.current?.querySelector<HTMLElement>(
          '[data-active="true"]',
        );
        if (element && container.current)
          container.current.scrollTop =
            container.current.scrollTop +
            element.getBoundingClientRect().top -
            container.current.getBoundingClientRect().top -
            container.current.clientHeight / 2 +
            element.offsetHeight / 2;
      }
    };
    center();
    const observer = new ResizeObserver(center);
    if (container.current) observer.observe(container.current);
    return () => observer.disconnect();
  }, [current, follow, size, lines]);
  return (
    <div
      ref={container}
      className={`${large ? "full-lyrics" : "compact-lyrics"} ${size ? "large" : ""}`}
    >
      {lines.length ? (
        lines.map((line, i) => (
          <button
            key={`${line.time}-${i}`}
            className={i === current ? "active" : ""}
            data-active={i === current}
            onClick={() => onSeek(line.time)}
          >
            <span>{line.text}</span>
            {large && <small className="lyric-time">{clock(line.time)}</small>}
          </button>
        ))
      ) : (
        <div className="lyric-empty">{text || "选择曲目后加载歌词"}</div>
      )}
    </div>
  );
}

export function Modal({
  title,
  close,
  children,
}: {
  title: string;
  close?: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
    return () => ref.current?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      onCancel={(e) => {
        if (close) close();
        else e.preventDefault();
      }}
      aria-label={title}
    >
      <div className="dialog-heading">
        <h2>{title}</h2>
        {close && (
          <Button label="关闭" onClick={close}>
            <X />
          </Button>
        )}
      </div>
      {children}
    </dialog>
  );
}
