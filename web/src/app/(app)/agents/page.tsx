import { Bot } from "lucide-react";
import type { Metadata } from "next";
import { PlannedSection } from "@/components/planned-section";

export const metadata: Metadata = { title: "Agents" };

export default function AgentsPage() {
  return (
    <PlannedSection
      title="Agents"
      description="Create, test and publish voice agents. Every change is versioned."
      icon={Bot}
      emptyTitle="No agents yet"
      phase="Phase 2"
      points={[
        "Build manually, with the assistant, or describe every rule in plain English",
        "Pipeline (STT, LLM, TTS) or Gemini Live, per agent",
        "Greeting in the customer's language; replies follow the language they speak",
      ]}
    />
  );
}
