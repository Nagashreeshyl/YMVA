import { readFileSync } from "node:fs";
import path from "node:path";
import type { Metadata } from "next";
import { marked } from "marked";

export const metadata: Metadata = { title: "API integration guide" };

// Read at build (the page is static): the guide lives in docs/, copied in by web/Dockerfile.
const guide = readFileSync(path.join(process.cwd(), "..", "docs", "INTEGRATION-GUIDE.md"), "utf8");

export default function IntegrationGuide() {
  // Our own checked-in Markdown, not user input, so rendering its HTML is safe.
  const html = marked.parse(guide, { async: false });
  return (
    <main className="mx-auto max-w-3xl px-4 py-10 sm:px-6">
      <article className="doc" dangerouslySetInnerHTML={{ __html: html }} />
    </main>
  );
}
