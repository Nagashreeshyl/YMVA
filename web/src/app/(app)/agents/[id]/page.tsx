import type { Metadata } from "next";
import { Suspense } from "react";
import { Skeleton } from "@/components/ui";
import { AgentView } from "./agent-view";

export const metadata: Metadata = { title: "Agent" };

export default function AgentPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <AgentView />
    </Suspense>
  );
}
