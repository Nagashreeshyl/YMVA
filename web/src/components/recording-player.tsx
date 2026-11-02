"use client";

import { Download, Pause, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";

function clock(seconds: number): string {
  if (!Number.isFinite(seconds)) return "0:00";
  const s = Math.floor(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Plays a call's WAV recording. Styled to match the dashboard rather than the browser default. */
// No downloadName: listen only (the role may not download recordings).
export function RecordingPlayer({ src, downloadName }: { src: string; downloadName?: string }) {
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [error, setError] = useState(false);

  useEffect(() => {
    const el = audio.current;
    if (!el) return;
    const onTime = () => setTime(el.currentTime);
    const onMeta = () => setDuration(el.duration);
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onError = () => setError(true);
    el.addEventListener("timeupdate", onTime);
    el.addEventListener("loadedmetadata", onMeta);
    el.addEventListener("play", onPlay);
    el.addEventListener("pause", onPause);
    el.addEventListener("ended", onPause);
    el.addEventListener("error", onError);
    return () => {
      el.removeEventListener("timeupdate", onTime);
      el.removeEventListener("loadedmetadata", onMeta);
      el.removeEventListener("play", onPlay);
      el.removeEventListener("pause", onPause);
      el.removeEventListener("ended", onPause);
      el.removeEventListener("error", onError);
    };
  }, []);

  if (error) return <p className="text-[13px] text-danger">The recording couldn&apos;t be loaded.</p>;

  const progress = duration ? (time / duration) * 100 : 0;
  return (
    <div className="flex items-center gap-3">
      <audio ref={audio} src={src} preload="metadata" />
      <button
        type="button"
        onClick={() => (playing ? audio.current?.pause() : void audio.current?.play())}
        aria-label={playing ? "Pause recording" : "Play recording"}
        className="grid size-10 shrink-0 place-items-center rounded-full bg-ink text-on-ink transition-opacity hover:opacity-85"
      >
        {playing ? <Pause className="size-4" aria-hidden /> : <Play className="ml-0.5 size-4" aria-hidden />}
      </button>
      <span className="tabular w-10 text-right text-[13px] text-muted">{clock(time)}</span>
      <input
        type="range"
        aria-label="Seek"
        min={0}
        max={duration || 0}
        step={0.1}
        value={time}
        onChange={(e) => {
          if (audio.current) audio.current.currentTime = Number(e.target.value);
          setTime(Number(e.target.value));
        }}
        style={{
          background: `linear-gradient(to right, var(--chart-1) ${progress}%, var(--surface-hover) ${progress}%)`,
        }}
        className="h-1.5 min-w-0 flex-1 cursor-pointer appearance-none rounded-full [&::-moz-range-thumb]:size-3.5 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-0 [&::-moz-range-thumb]:bg-ink [&::-webkit-slider-thumb]:size-3.5 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-ink"
      />
      <span className="tabular w-10 text-[13px] text-muted">{clock(duration)}</span>
      {downloadName && (
      <a
        href={src}
        download={downloadName}
        aria-label="Download recording (WAV)"
        title="Download WAV"
        className="grid size-9 shrink-0 place-items-center rounded-full text-muted transition-colors hover:bg-surface hover:text-ink"
      >
        <Download className="size-4" aria-hidden />
      </a>
      )}
    </div>
  );
}
