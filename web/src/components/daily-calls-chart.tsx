"use client";

import { useState } from "react";
import { formatDay, formatNumber } from "@/lib/format";

type Point = { date: string; calls: number; completed: number };

const HEIGHT = 180;
const BAR_MAX = 24;

/** Single-series column chart: calls per day, with completed calls in the tooltip. */
export function DailyCallsChart({ data }: { data: Point[] }) {
  const [active, setActive] = useState<number | null>(null);
  const max = Math.max(...data.map((d) => d.calls), 0);
  const empty = max === 0;
  // Round the axis top to a friendly number so gridlines land on whole values.
  const top = empty ? 4 : niceCeil(max);
  const ticks = [0, top / 2, top];

  return (
    <figure className="flex flex-col gap-3">
      <div className="relative mt-3" style={{ height: HEIGHT + 28 }}>
        <div className="absolute inset-x-0 top-0" style={{ height: HEIGHT }}>
          {ticks.map((t) => (
            <div
              key={t}
              className="absolute inset-x-0 flex items-center gap-2"
              style={{ bottom: `${(t / top) * 100}%` }}
            >
              <span className="tabular w-6 -translate-y-1/2 text-right text-2xs text-faint">
                {Number.isInteger(t) ? t : ""}
              </span>
              <span className={t === 0 ? "h-px flex-1 bg-line-strong" : "h-px flex-1 bg-line"} aria-hidden />
            </div>
          ))}
        </div>

        <div className="absolute inset-y-0 right-0 left-8 grid grid-cols-7">
          {data.map((d, i) => {
            const h = (d.calls / top) * HEIGHT;
            const isActive = active === i;
            return (
              <button
                key={d.date}
                type="button"
                className="group relative flex flex-col items-center justify-end outline-none"
                style={{ height: HEIGHT + 28 }}
                onPointerEnter={() => setActive(i)}
                onPointerLeave={() => setActive(null)}
                onFocus={() => setActive(i)}
                onBlur={() => setActive(null)}
                aria-label={`${formatDay(d.date, "long")}: ${d.calls} calls, ${d.completed} completed`}
              >
                <div className="flex w-full flex-1 items-end justify-center" style={{ maxHeight: HEIGHT }}>
                  {d.calls > 0 && (
                    <div
                      className="rounded-t-[4px] bg-chart-1 transition-opacity duration-150"
                      style={{
                        height: Math.max(h, 2),
                        width: BAR_MAX,
                        opacity: active === null || isActive ? 1 : 0.55,
                      }}
                    />
                  )}
                </div>
                <span
                  className={`mt-2 h-5 text-2xs ${isActive ? "font-medium text-ink" : "text-muted"} group-focus-visible:rounded group-focus-visible:outline-2 group-focus-visible:outline-gold`}
                >
                  {i === data.length - 1 ? "Today" : formatDay(d.date)}
                </span>

                {isActive && (
                  <div
                    role="tooltip"
                    className="pointer-events-none absolute z-10 min-w-[132px] rounded-xl border border-line bg-paper px-3 py-2 text-left shadow-float"
                    style={{ bottom: Math.min(h, HEIGHT) + 36 }}
                  >
                    <p className="text-2xs text-muted">{formatDay(d.date, "long")}</p>
                    <p className="tabular text-sm font-semibold">{formatNumber(d.calls)} calls</p>
                    <p className="tabular text-2xs text-muted">{formatNumber(d.completed)} completed</p>
                  </div>
                )}
              </button>
            );
          })}
        </div>

        {empty && (
          <p className="absolute inset-x-8 top-[38%] text-center text-[13px] text-muted">
            No calls in the last 7 days
          </p>
        )}
      </div>

      <table className="sr-only">
        <caption>Calls per day, last 7 days</caption>
        <thead>
          <tr>
            <th scope="col">Day</th>
            <th scope="col">Calls</th>
            <th scope="col">Completed</th>
          </tr>
        </thead>
        <tbody>
          {data.map((d) => (
            <tr key={d.date}>
              <th scope="row">{formatDay(d.date, "long")}</th>
              <td>{d.calls}</td>
              <td>{d.completed}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}

function niceCeil(n: number): number {
  const magnitude = 10 ** Math.floor(Math.log10(n));
  for (const step of [1, 2, 2.5, 5, 10]) {
    const candidate = step * magnitude;
    if (candidate >= n && (candidate / 2) % 1 === 0) return candidate;
  }
  return Math.ceil(n / magnitude) * magnitude * 2;
}
