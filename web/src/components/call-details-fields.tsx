"use client";

import { Field, Input, Select } from "./ui";
import type { AgentConfig } from "@/lib/api";
import { LANGUAGE_NAMES } from "@/lib/format";
import { callDetails, variableLabel } from "@/lib/variables";

/** The per-call details form shared by browser tests and phone calls. */
export function CallDetailsFields({
  config,
  values,
  onChange,
  disabled,
}: {
  config: AgentConfig;
  values: Record<string, string>;
  onChange: (values: Record<string, string>) => void;
  disabled?: boolean;
}) {
  const notes = Object.fromEntries(config.variables.map((v) => [v.name, v.description || undefined]));
  const set = (name: string, value: string) => onChange({ ...values, [name]: value });
  return (
    <>
      {callDetails(config).map((name) =>
        name === "preferred_language" ? (
          <Field key={name} label="Preferred language" hint="Optional. Picks the greeting language.">
            {(p) => (
              <Select
                {...p}
                value={values[name] ?? ""}
                disabled={disabled}
                onChange={(e) => set(name, e.target.value)}
              >
                <option value="">Use the state or default</option>
                {config.languages.map((code) => (
                  <option key={code} value={code}>
                    {LANGUAGE_NAMES[code] ?? code}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        ) : name === "state" ? (
          <Field key={name} label="State" hint="Optional, e.g. Karnataka. Picks the greeting language.">
            {(p) => (
              <Input
                {...p}
                value={values[name] ?? ""}
                disabled={disabled}
                onChange={(e) => set(name, e.target.value)}
              />
            )}
          </Field>
        ) : (
          <Field key={name} label={variableLabel(name)} hint={notes[name]}>
            {(p) => (
              <Input
                {...p}
                value={values[name] ?? ""}
                disabled={disabled}
                placeholder={`{{${name}}}`}
                onChange={(e) => set(name, e.target.value)}
              />
            )}
          </Field>
        ),
      )}
    </>
  );
}
