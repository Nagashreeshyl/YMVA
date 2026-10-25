import type { AgentSummary } from "@/lib/api";
import { Badge } from "./ui";

export function AgentStatusBadge({ agent }: { agent: Pick<AgentSummary, "status" | "published_version"> }) {
  if (agent.status === "active") return <Badge tone="success">Live</Badge>;
  if (agent.status === "paused") return <Badge tone="warning">Paused</Badge>;
  if (agent.published_version === null) return <Badge>Draft</Badge>;
  return <Badge>{agent.status}</Badge>;
}
