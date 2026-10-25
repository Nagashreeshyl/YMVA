import { ArrowDownLeft, ArrowUpRight, Monitor } from "lucide-react";
import { Badge } from "./ui";

const STATUS: Record<string, { label: string; tone: "neutral" | "success" | "warning" | "danger" | "gold" }> =
  {
    queued: { label: "Queued", tone: "neutral" },
    ringing: { label: "Ringing", tone: "gold" },
    in_progress: { label: "Live", tone: "gold" },
    completed: { label: "Completed", tone: "success" },
    no_answer: { label: "No answer", tone: "warning" },
    busy: { label: "Busy", tone: "warning" },
    failed: { label: "Failed", tone: "danger" },
    canceled: { label: "Canceled", tone: "neutral" },
  };

export function CallStatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status, tone: "neutral" as const };
  return (
    <Badge tone={s.tone}>
      {status === "in_progress" && (
        <span className="size-1.5 animate-pulse rounded-full bg-ink" aria-hidden />
      )}
      {s.label}
    </Badge>
  );
}

const DIRECTION = {
  inbound: { label: "Incoming", icon: ArrowDownLeft },
  outbound: { label: "Outgoing", icon: ArrowUpRight },
  web: { label: "Browser test", icon: Monitor },
} as const;

export function DirectionLabel({ direction }: { direction: keyof typeof DIRECTION }) {
  const d = DIRECTION[direction];
  return (
    <span className="inline-flex items-center gap-1.5 text-[13px] text-muted">
      <d.icon className="size-3.5" aria-hidden /> {d.label}
    </span>
  );
}
