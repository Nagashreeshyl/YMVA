import { Phone } from "lucide-react";
import type { Metadata } from "next";
import { PlannedSection } from "@/components/planned-section";

export const metadata: Metadata = { title: "Numbers" };

export default function NumbersPage() {
  return (
    <PlannedSection
      title="Numbers"
      description="Phone numbers from your providers, and which agent answers each one."
      icon={Phone}
      emptyTitle="No numbers yet"
      phase="Phase 1"
      points={[
        "Import numbers from Plivo, Vobiz, Exotel, Twilio, Telnyx, Vonage or SIP",
        "Assign an agent to answer inbound calls",
        "Set a limit on simultaneous calls per number",
      ]}
    />
  );
}
