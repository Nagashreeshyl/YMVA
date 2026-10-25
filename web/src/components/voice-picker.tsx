"use client";

import { Loader2, Pause, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";

type Engine = "sarvam" | "gemini";

/** Voices shown as cards with a play button, so the voice is chosen by ear. */
export function VoicePicker({
  engine,
  voices,
  value,
  onChange,
  language,
  disabled,
}: {
  engine: Engine;
  voices: string[];
  value: string;
  onChange: (voice: string) => void;
  language: string;
  disabled?: boolean;
}) {
  const [playing, setPlaying] = useState<string | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const urls = useRef(new Map<string, string>());

  useEffect(() => {
    const cache = urls.current;
    return () => {
      audio.current?.pause();
      cache.forEach((u) => URL.revokeObjectURL(u));
    };
  }, []);

  async function preview(voice: string) {
    setError(null);
    if (playing === voice) {
      audio.current?.pause();
      setPlaying(null);
      return;
    }
    audio.current?.pause();
    const key = `${engine}:${voice}:${language}`;
    let url = urls.current.get(key);
    if (!url) {
      setLoading(voice);
      try {
        const res = await fetch("/api/agents/voice-preview", {
          method: "POST",
          credentials: "same-origin",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ engine, voice, language }),
        });
        if (!res.ok) {
          const body = await res.json().catch(() => null);
          throw new Error(body?.detail ?? "Couldn't load the sample");
        }
        url = URL.createObjectURL(await res.blob());
        urls.current.set(key, url);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Couldn't load the sample");
        setLoading(null);
        return;
      }
      setLoading(null);
    }
    const el = new Audio(url);
    audio.current = el;
    el.onended = () => setPlaying(null);
    setPlaying(voice);
    void el.play();
  }

  return (
    <div className="flex flex-col gap-2">
      <div
        role="radiogroup"
        aria-label="Voice"
        className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5"
      >
        {voices.map((v) => {
          const selected = v === value;
          return (
            <div
              key={v}
              className={cn(
                "flex items-center gap-1 rounded-full border py-1 pr-3 pl-1 transition-colors duration-150",
                selected ? "border-ink bg-ink text-on-ink" : "border-line-strong hover:bg-surface",
              )}
            >
              <button
                type="button"
                onClick={() => preview(v)}
                aria-label={`${playing === v ? "Stop" : "Play"} ${v} sample`}
                className={cn(
                  "grid size-7 shrink-0 place-items-center rounded-full",
                  selected ? "bg-on-ink/15 hover:bg-on-ink/25" : "bg-surface hover:bg-surface-hover",
                )}
              >
                {loading === v ? (
                  <Loader2 className="size-3.5 animate-spin" aria-hidden />
                ) : playing === v ? (
                  <Pause className="size-3.5" aria-hidden />
                ) : (
                  <Play className="ml-0.5 size-3.5" aria-hidden />
                )}
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={selected}
                onClick={() => onChange(v)}
                disabled={disabled}
                className="min-w-0 flex-1 truncate text-left text-[13px] font-medium"
              >
                {v.charAt(0).toUpperCase() + v.slice(1)}
              </button>
            </div>
          );
        })}
      </div>
      {error && <p className="text-2xs text-danger">{error}</p>}
    </div>
  );
}
