import { Plug } from "lucide-react";
import type { Metadata } from "next";
import { PlannedSection } from "@/components/planned-section";

export const metadata: Metadata = { title: "Integrations" };

export default function IntegrationsPage() {
  return (
    <PlannedSection
      title="Integrations"
      description="Telephony providers, AI models, and data sources, in one place."
      icon={Plug}
      emptyTitle="Nothing connected yet"
      phase="Phase 1"
      points={[
        "Telephony: Plivo and Vobiz first, then Exotel, Twilio, Telnyx, Vonage, SIP",
        "AI: Sarvam, Gemini, Deepgram, ElevenLabs, Cartesia",
        "Keys are encrypted and never shown again after saving",
      ]}
    />
  );
}
