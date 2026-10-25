import { Megaphone } from "lucide-react";
import type { Metadata } from "next";
import { PlannedSection } from "@/components/planned-section";

export const metadata: Metadata = { title: "Campaigns" };

export default function CampaignsPage() {
  return (
    <PlannedSection
      title="Campaigns"
      description="Run outbound calls to a list of customers on a schedule."
      icon={Megaphone}
      emptyTitle="No campaigns yet"
      phase="Phase 5"
      points={[
        "Import from CSV, Google Sheets or a database query",
        "Calling hours (8 AM–7 PM), retries and do-not-call list enforced",
        "Live progress, with results written back to your sheet",
      ]}
    />
  );
}
