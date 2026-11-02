"use client";

import { useQuery } from "@tanstack/react-query";
import { Field, Input, Select } from "./ui";
import { api, type AgentConfig, type Branch } from "@/lib/api";
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
  const details = callDetails(config);
  const branches = useQuery({
    queryKey: ["branches"],
    queryFn: () => api<Branch[]>("/branches"),
    enabled: details.includes("branch"),
  });
  return (
    <>
      {details.map((name) =>
        name === "branch" && branches.data?.length ? (
          <Field key={name} label="Branch" hint="Where the customer took the loan. Transfers go to its manager.">
            {(p) => (
              <Select
                {...p}
                value={values[name] ?? ""}
                disabled={disabled}
                onChange={(e) => set(name, e.target.value)}
              >
                <option value="">Choose the branch</option>
                {branches.data.map((b) => (
                  <option key={b.id} value={b.name}>
                    {b.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        ) : name === "preferred_language" ? (
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
