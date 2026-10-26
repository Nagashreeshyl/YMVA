import type { Metadata } from "next";
import { Suspense } from "react";
import { Skeleton } from "@/components/ui";
import { CampaignView } from "./campaign-view";

export const metadata: Metadata = { title: "Campaign" };

export default function CampaignPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <CampaignView />
    </Suspense>
  );
}
