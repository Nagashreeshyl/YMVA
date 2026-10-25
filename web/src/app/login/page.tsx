"use client";

import { AudioLines } from "lucide-react";
import { useState } from "react";
import { Button, Field, Input } from "@/components/ui";
import { api } from "@/lib/api";

function safeNext(): string {
  const next = new URLSearchParams(window.location.search).get("next");
  // Only allow same-site relative paths.
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

export default function LoginPage() {
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(null);
    try {
      await api("/auth/login", {
        method: "POST",
        json: { email: form.get("email"), password: form.get("password") },
      });
      window.location.assign(safeNext());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed. Please try again.");
      setPending(false);
    }
  }

  return (
    <main className="grid min-h-dvh place-items-center bg-rail px-4 py-10">
      <div className="w-full max-w-[400px]">
        <div className="mb-8 flex flex-col items-center gap-3 text-center">
          <span className="grid size-12 place-items-center rounded-2xl bg-[#0a0a0a] text-gold ring-1 ring-line">
            <AudioLines className="size-6" strokeWidth={2} aria-hidden />
          </span>
          <div>
            <p className="text-[13px] font-bold tracking-[0.08em]">YELLOW METAL</p>
            <h1 className="font-display text-[28px] leading-9 font-extrabold tracking-[-0.02em]">
              Voice Agent
            </h1>
          </div>
        </div>

        <form
          onSubmit={onSubmit}
          noValidate
          className="flex flex-col gap-5 rounded-2xl border border-line bg-paper p-6 sm:p-8"
        >
          <Field label="Email">
            {(props) => (
              <Input
                {...props}
                name="email"
                type="email"
                autoComplete="username"
                required
                autoFocus
                placeholder="you@yellowmetal.co"
              />
            )}
          </Field>
          <Field label="Password">
            {(props) => (
              <Input {...props} name="password" type="password" autoComplete="current-password" required />
            )}
          </Field>
          {error && (
            <p role="alert" className="rounded-xl bg-danger-soft px-3.5 py-2.5 text-[13px] text-danger">
              {error}
            </p>
          )}
          <Button type="submit" loading={pending} className="mt-1 w-full">
            {pending ? "Signing in" : "Sign in"}
          </Button>
        </form>
        <p className="mt-6 text-center text-2xs text-muted">
          Forgot your password? Ask an admin to reset it in Settings.
        </p>
      </div>
    </main>
  );
}
