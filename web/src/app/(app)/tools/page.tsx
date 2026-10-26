import type { Metadata } from "next";
import { ToolsView } from "./tools-view";

export const metadata: Metadata = { title: "Tools" };

export default function ToolsPage() {
  return <ToolsView />;
}
