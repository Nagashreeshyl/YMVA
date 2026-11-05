import type { AgentConfig } from "./api";

const VARIABLE_RE = /\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g;

/** Every {{name}} used in the instructions, greetings or announcement, in order of first use. */
export function detectVariables(config: AgentConfig): string[] {
  const texts = [
    config.system_prompt,
    ...Object.values(config.greetings),
    config.announcement.text,
    ...Object.values(config.announcement.translations),
  ];
  const seen = new Set<string>();
  for (const text of texts)
    for (const m of text.matchAll(VARIABLE_RE)) seen.add(m[1]);
  return [...seen];
}

export function variableLabel(name: string): string {
  const s = name.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Last values typed for an agent's call form, so repeat tests don't need retyping. */
export function rememberedValues(agentId: string): Record<string, string> {
  try {
    return JSON.parse(
      localStorage.getItem(`ymva:call-values:${agentId}`) ?? "{}",
    );
  } catch {
    return {};
  }
}

export function rememberValues(
  agentId: string,
  values: Record<string, string>,
): void {
  try {
    localStorage.setItem(`ymva:call-values:${agentId}`, JSON.stringify(values));
  } catch {
    // Private mode or storage blocked: values just won't be remembered.
  }
}

/** Built-in per-call details: they pick the greeting language and needn't appear in the text. */
export const ROUTING_DETAILS = ["preferred_language", "state"] as const;

/** Detected details plus the built-in ones, without duplicates. An agent that transfers to the
 * customer's branch manager also needs their branch. */
export function callDetails(config: AgentConfig): string[] {
  const branch =
    config.branch_required ||
    (config.transfer?.enabled && config.transfer.to === "branch_manager")
      ? ["branch"]
      : [];
  return [
    ...new Set([...detectVariables(config), ...branch, ...ROUTING_DETAILS]),
  ];
}
