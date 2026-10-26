"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Users } from "lucide-react";
import { useState } from "react";
import {
  Badge,
  Button,
  EmptyState,
  ErrorNotice,
  Field,
  Input,
  Select,
  Skeleton,
} from "@/components/ui";
import { useConfirm } from "@/components/dialog";
import { api, type Role, type User } from "@/lib/api";
import { relativeTime } from "@/lib/format";

const ROLE_LABEL: Record<Role, string> = {
  admin: "Admin",
  viewer: "View only",
};

function AddPersonForm({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient();
  const create = useMutation({
    mutationFn: (body: Record<string, FormDataEntryValue | null>) =>
      api<User>("/users", { method: "POST", json: body }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] });
      onDone();
    },
  });

  return (
    <form
      className="grid gap-4 rounded-xl border border-line p-5 sm:grid-cols-2"
      onSubmit={(e) => {
        e.preventDefault();
        const f = new FormData(e.currentTarget);
        create.mutate({
          name: f.get("name"),
          email: f.get("email"),
          role: f.get("role"),
          password: f.get("password"),
        });
      }}
    >
      <h2 className="text-[15px] font-semibold sm:col-span-2">Add a person</h2>
      <Field label="Name">
        {(p) => <Input {...p} name="name" required autoFocus />}
      </Field>
      <Field label="Email">
        {(p) => <Input {...p} name="email" type="email" required />}
      </Field>
      <Field
        label="Access"
        hint="View only can see everything but change nothing."
      >
        {(p) => (
          <Select {...p} name="role" defaultValue="viewer">
            <option value="viewer">View only</option>
            <option value="admin">Admin</option>
          </Select>
        )}
      </Field>
      <Field
        label="Temporary password"
        hint="At least 10 characters. Share it privately."
      >
        {(p) => (
          <Input
            {...p}
            name="password"
            type="password"
            autoComplete="new-password"
            required
            minLength={10}
          />
        )}
      </Field>
      {create.isError && (
        <div className="sm:col-span-2">
          <ErrorNotice error={create.error} />
        </div>
      )}
      <div className="flex gap-2 sm:col-span-2">
        <Button type="submit" loading={create.isPending}>
          Add person
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function PersonRow({ user, isSelf }: { user: User; isSelf: boolean }) {
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const [resetting, setResetting] = useState(false);
  const update = useMutation({
    mutationFn: (
      body: Partial<{ role: Role; is_active: boolean; password: string }>,
    ) => api<User>(`/users/${user.id}`, { method: "PATCH", json: body }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] });
      setResetting(false);
    },
  });

  return (
    <li className="flex flex-col gap-3 px-5 py-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="grid size-9 shrink-0 place-items-center rounded-full bg-surface text-[13px] font-semibold">
          {user.name.charAt(0).toUpperCase()}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">
            {user.name}
            {isSelf && <span className="font-normal text-muted"> (you)</span>}
          </p>
          <p className="truncate text-[13px] text-muted">{user.email}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {!user.is_active && <Badge tone="danger">Deactivated</Badge>}
          <span className="text-2xs text-muted">
            Last sign-in {relativeTime(user.last_login_at).toLowerCase()}
          </span>
          <Select
            aria-label={`Access for ${user.name}`}
            className="h-8 w-[130px] text-[13px]"
            value={user.role}
            disabled={update.isPending}
            onChange={(e) => update.mutate({ role: e.target.value as Role })}
          >
            {Object.entries(ROLE_LABEL).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
          {!isSelf && (
            <>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setResetting((v) => !v)}
              >
                Reset password
              </Button>
              <Button
                variant={user.is_active ? "danger" : "secondary"}
                size="sm"
                loading={
                  update.isPending && update.variables?.is_active !== undefined
                }
                onClick={async () => {
                  if (user.is_active) {
                    const { ok } = await confirm({
                      title: `Deactivate ${user.name}?`,
                      body: "They'll be signed out immediately and can't sign in until you reactivate them.",
                      confirmLabel: "Deactivate",
                      tone: "danger",
                    });
                    if (!ok) return;
                  }
                  update.mutate({ is_active: !user.is_active });
                }}
              >
                {user.is_active ? "Deactivate" : "Reactivate"}
              </Button>
            </>
          )}
        </div>
      </div>
      {resetting && (
        <form
          className="flex flex-wrap items-end gap-2 sm:pl-[52px]"
          onSubmit={(e) => {
            e.preventDefault();
            const password = String(
              new FormData(e.currentTarget).get("password") ?? "",
            );
            update.mutate({ password });
          }}
        >
          <div className="w-full max-w-xs">
            <Field
              label={`New password for ${user.name}`}
              hint="They will be signed out everywhere."
            >
              {(p) => (
                <Input
                  {...p}
                  name="password"
                  type="password"
                  autoComplete="new-password"
                  minLength={10}
                  required
                  autoFocus
                />
              )}
            </Field>
          </div>
          <Button type="submit" size="md" loading={update.isPending}>
            Set password
          </Button>
        </form>
      )}
      {update.isError && <ErrorNotice error={update.error} />}
    </li>
  );
}

export function TeamSettings({ currentUserId }: { currentUserId: string }) {
  const [adding, setAdding] = useState(false);
  const users = useQuery({
    queryKey: ["users"],
    queryFn: () => api<User[]>("/users"),
  });

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-[60ch] text-muted">
          Admins can change everything. View-only people see calls, recordings
          and results but can&apos;t change agents or settings.
        </p>
        {!adding && (
          <Button onClick={() => setAdding(true)}>
            <Plus className="size-4" aria-hidden />
            Add person
          </Button>
        )}
      </div>
      {adding && <AddPersonForm onDone={() => setAdding(false)} />}
      {users.isPending ? (
        <Skeleton className="h-40 rounded-xl" />
      ) : users.isError ? (
        <ErrorNotice error={users.error} onRetry={() => users.refetch()} />
      ) : users.data.length === 0 ? (
        <EmptyState icon={Users} title="No people yet">
          Add the people who should be able to sign in.
        </EmptyState>
      ) : (
        <ul className="divide-y divide-line rounded-xl border border-line">
          {users.data.map((u) => (
            <PersonRow key={u.id} user={u} isSelf={u.id === currentUserId} />
          ))}
        </ul>
      )}
    </div>
  );
}
