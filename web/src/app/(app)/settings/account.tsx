"use client";

import { useMutation } from "@tanstack/react-query";
import { CheckCircle2 } from "lucide-react";
import { useRef } from "react";
import { Button, ErrorNotice, Field, Input } from "@/components/ui";
import { api } from "@/lib/api";
import { ROLE_LABELS } from "@/lib/permissions";
import { useMe } from "@/lib/session";

export function AccountSettings() {
  const { data: me } = useMe();
  const formRef = useRef<HTMLFormElement>(null);
  const change = useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      api("/auth/change-password", { method: "POST", json: body }),
    onSuccess: () => formRef.current?.reset(),
  });

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_1fr]">
      <section className="rounded-xl border border-line p-5">
        <h2 className="mb-4 text-[15px] font-semibold">Profile</h2>
        <dl className="grid grid-cols-[110px_1fr] gap-y-2 text-sm">
          <dt className="text-muted">Name</dt>
          <dd>{me?.name}</dd>
          <dt className="text-muted">Email</dt>
          <dd className="break-all">{me?.email}</dd>
          <dt className="text-muted">Access</dt>
          <dd>{me ? ROLE_LABELS[me.role] : ""}</dd>
        </dl>
      </section>

      <form
        ref={formRef}
        className="flex flex-col gap-4 rounded-xl border border-line p-5"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          change.mutate({
            current_password: String(f.get("current") ?? ""),
            new_password: String(f.get("new") ?? ""),
          });
        }}
      >
        <h2 className="text-[15px] font-semibold">Change password</h2>
        <Field label="Current password">
          {(p) => (
            <Input
              {...p}
              name="current"
              type="password"
              autoComplete="current-password"
              required
            />
          )}
        </Field>
        <Field
          label="New password"
          hint="At least 10 characters. Other devices will be signed out."
        >
          {(p) => (
            <Input
              {...p}
              name="new"
              type="password"
              autoComplete="new-password"
              required
              minLength={10}
            />
          )}
        </Field>
        {change.isError && <ErrorNotice error={change.error} />}
        {change.isSuccess && (
          <p
            role="status"
            className="flex items-center gap-2 text-[13px] text-success"
          >
            <CheckCircle2 className="size-4" aria-hidden /> Password changed.
          </p>
        )}
        <div>
          <Button type="submit" loading={change.isPending}>
            Change password
          </Button>
        </div>
      </form>
    </div>
  );
}
