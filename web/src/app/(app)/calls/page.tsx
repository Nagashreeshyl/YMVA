import { PhoneCall } from "lucide-react";
import type { Metadata } from "next";
import { PlannedSection } from "@/components/planned-section";

export const metadata: Metadata = { title: "Calls" };

export default function CallsPage() {
  return (
    <PlannedSection
      title="Calls"
      description="Every inbound, outbound and test call, with recording and transcript."
      icon={PhoneCall}
      emptyTitle="No calls yet"
      phase="Phase 1"
      points={[
        "Recording, transcript with language tags, and an AI summary",
        "Outcome and captured details, such as promise-to-pay date",
        "Every tool call, latency per step and cost per call",
      ]}
    />
  );
}
