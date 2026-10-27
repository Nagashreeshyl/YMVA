const TZ = "Asia/Kolkata";

export const LANGUAGE_NAMES: Record<string, string> = {
  en: "English",
  hi: "Hindi",
  kn: "Kannada",
  te: "Telugu",
  ta: "Tamil",
  ml: "Malayalam",
  mr: "Marathi",
  bn: "Bengali",
  gu: "Gujarati",
  od: "Odia",
  pa: "Punjabi",
};

export function formatNumber(n: number): string {
  return new Intl.NumberFormat("en-IN").format(n);
}

export function formatPercent(ratio: number | null): string {
  return ratio === null ? "—" : `${Math.round(ratio * 100)}%`;
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  const s = Math.round(seconds);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

/** Costs are stored in USD; shown in rupees at the rate the backend uses (pricing.py). */
export const USD_TO_INR = 88;

export function formatUsd(amount: number): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(amount);
}

export function formatDateTime(iso: string): string {
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: TZ,
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(iso));
}

export function formatDay(isoDate: string, style: "short" | "long" = "short"): string {
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "UTC",
    weekday: style === "short" ? "short" : "long",
    ...(style === "long" ? { day: "numeric", month: "short" } : {}),
  }).format(new Date(`${isoDate}T00:00:00Z`));
}

export function relativeTime(iso: string | null): string {
  if (!iso) return "Never";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "Just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  return formatDateTime(iso);
}

export function humanize(value: string): string {
  const s = value.replace(/[._]/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}
