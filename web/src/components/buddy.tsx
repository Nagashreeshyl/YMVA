"use client";

import { useId } from "react";
import { cn } from "@/lib/cn";

// [highlight, body] gradient pairs. Gold is kept for YMA so it always stands out.
const YMA_COLORS: [string, string] = ["#ffe58f", "#f2a900"];
const COLORS: [string, string][] = [
  ["#ffd9c2", "#ff7f50"],
  ["#c8f5e1", "#22b583"],
  ["#d3e8ff", "#3d8bff"],
  ["#e9ddff", "#8f6bff"],
  ["#ffd8e7", "#f0609a"],
  ["#e8ffc4", "#86c22e"],
  ["#c9f2f4", "#16a8b5"],
  ["#ffe6c7", "#f08a24"],
];
const SHAPES = [
  "M32 4a28 28 0 1 1 0 56a28 28 0 1 1 0-56Z", // round
  "M32 5c17 0 27 7 27 27s-10 27-27 27S5 52 5 32 15 5 32 5Z", // soft square
  "M33 4c15 1 27 11 26 27-1 17-12 29-28 28C15 58 4 47 5 31 6 15 18 3 33 4Z", // blob
];

function hash(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return h >>> 0;
}

/**
 * A friendly little character. Each agent gets its own (colour, shape and face come from its id);
 * `yma` is YMA, the Yellow Metal Assistant. `active` makes it bounce, e.g. while it's talking.
 */
export function Buddy({
  seed = "",
  yma = false,
  size = 32,
  active = false,
  className,
}: {
  seed?: string;
  yma?: boolean;
  size?: number;
  active?: boolean;
  className?: string;
}) {
  const id = useId();
  const h = hash(yma ? "YMA" : seed);
  const [light, body] = yma ? YMA_COLORS : COLORS[h % COLORS.length];
  const shape = yma ? SHAPES[0] : SHAPES[(h >>> 4) % SHAPES.length];
  const eyes = yma ? 0 : (h >>> 8) % 3; // 0 ovals, 1 happy arcs, 2 dots
  const mouth = yma ? 0 : (h >>> 12) % 2; // 0 smile, 1 small "o"
  const cheeks = yma || (h >>> 16) % 2 === 0;
  const ink = "#1b1b1f";
  return (
    <svg
      viewBox="0 0 64 64"
      width={size}
      height={size}
      aria-hidden
      className={cn("buddy shrink-0 overflow-visible", active && "buddy-active", className)}
      style={{ animationDelay: `-${h % 4000}ms` }}
    >
      <defs>
        <radialGradient id={`${id}g`} cx="35%" cy="30%" r="75%">
          <stop offset="0%" stopColor={light} />
          <stop offset="65%" stopColor={body} />
        </radialGradient>
      </defs>
      {yma && (
        // YMA's antenna, so the assistant never looks like a plain smiley.
        <>
          <path d="M32 6V-2" stroke={body} strokeWidth="3" strokeLinecap="round" />
          <circle className="buddy-glow" cx="32" cy="-4" r="4" fill={light} stroke={body} strokeWidth="2" />
        </>
      )}
      <path d={shape} fill={`url(#${id}g)`} />
      <ellipse cx="22" cy="17" rx="9" ry="5" fill="#fff" opacity="0.35" transform="rotate(-25 22 17)" />
      <g className="buddy-eyes" style={{ animationDelay: `-${(h >>> 3) % 5000}ms` }}>
        {eyes === 1 ? (
          <path
            d="M18 34q5-6.5 10 0M36 34q5-6.5 10 0"
            fill="none"
            stroke={ink}
            strokeWidth="3.6"
            strokeLinecap="round"
          />
        ) : eyes === 2 ? (
          <>
            <circle cx="23" cy="33" r="4.4" fill={ink} />
            <circle cx="41" cy="33" r="4.4" fill={ink} />
          </>
        ) : (
          <>
            <ellipse cx="23" cy="32" rx="4.6" ry="6.2" fill={ink} />
            <ellipse cx="41" cy="32" rx="4.6" ry="6.2" fill={ink} />
            <circle cx="24.8" cy="29.6" r="1.7" fill="#fff" />
            <circle cx="42.8" cy="29.6" r="1.7" fill="#fff" />
          </>
        )}
      </g>
      {cheeks && (
        <>
          <ellipse cx="14.5" cy="41" rx="4.6" ry="2.8" fill="#ff6f9c" opacity="0.5" />
          <ellipse cx="49.5" cy="41" rx="4.6" ry="2.8" fill="#ff6f9c" opacity="0.5" />
        </>
      )}
      {mouth === 0 && (
        <path d="M27 42.5q5 5 10 0" fill="none" stroke={ink} strokeWidth="3" strokeLinecap="round" />
      )}
      {mouth === 1 && <ellipse cx="32" cy="44" rx="2.8" ry="2.5" fill={ink} />}
    </svg>
  );
}
