import { Wrench } from "lucide-react";
import type { Metadata } from "next";
import { PlannedSection } from "@/components/planned-section";

export const metadata: Metadata = { title: "Tools" };

export default function ToolsPage() {
  return (
    <PlannedSection
      title="Tools"
      description="Actions agents can take during a call."
      icon={Wrench}
      emptyTitle="No tools yet"
      phase="Phase 4"
      points={[
        "Describe a tool in English and the Tool Builder creates it",
        "Google Sheets, databases, HTTP APIs and MCP servers",
        "Test each tool with sample inputs before an agent uses it",
      ]}
    />
  );
}
