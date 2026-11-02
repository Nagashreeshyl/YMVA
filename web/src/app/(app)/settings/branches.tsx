"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, Plus } from "lucide-react";
import { useState } from "react";
import { useConfirm } from "@/components/dialog";
import { Button, EmptyState, ErrorNotice, Field, Input, Skeleton } from "@/components/ui";
import { api, type Branch } from "@/lib/api";

function BranchForm({ branch, onDone }: { branch?: Branch; onDone: () => void }) {
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      branch
        ? api<Branch>(`/branches/${branch.id}`, { method: "PUT", json: body })
        : api<Branch>("/branches", { method: "POST", json: body }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["branches"] });
      onDone();
    },
  });
  return (
    <form
      className="grid gap-4 rounded-xl border border-line p-5 sm:grid-cols-2"
      onSubmit={(e) => {
        e.preventDefault();
        const f = new FormData(e.currentTarget);
        save.mutate({
          name: f.get("name"),
          other_names: String(f.get("other_names") ?? "").split(","),
          manager_name: f.get("manager_name"),
          manager_phone: f.get("manager_phone"),
        });
      }}
    >
      <h2 className="text-[15px] font-semibold sm:col-span-2">
        {branch ? `Edit ${branch.name}` : "New branch"}
      </h2>
      <Field label="Branch" hint="As your apps send it, e.g. Chintamani.">
        {(p) => <Input {...p} name="name" required autoFocus maxLength={80} defaultValue={branch?.name} />}
      </Field>
      <Field label="Other spellings (optional)" hint="Comma-separated, e.g. Bengaluru, BLR.">
        {(p) => <Input {...p} name="other_names" defaultValue={branch?.other_names.join(", ")} />}
      </Field>
      <Field label="Manager's name">
        {(p) => <Input {...p} name="manager_name" maxLength={120} defaultValue={branch?.manager_name} />}
      </Field>
      <Field label="Manager's phone" hint="Transferred calls ring this number.">
        {(p) => (
          <Input
            {...p}
            name="manager_phone"
            type="tel"
            inputMode="tel"
            required
            placeholder="+91 98450 11111"
            defaultValue={branch?.manager_phone}
          />
        )}
      </Field>
      {save.isError && (
        <div className="sm:col-span-2">
          <ErrorNotice error={save.error} />
        </div>
      )}
      <div className="flex gap-2 sm:col-span-2">
        <Button type="submit" loading={save.isPending}>
          {branch ? "Save" : "Add branch"}
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function BranchRow({ branch, canEdit }: { branch: Branch; canEdit: boolean }) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const [editing, setEditing] = useState(false);
  const remove = useMutation({
    mutationFn: () => api(`/branches/${branch.id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["branches"] }),
  });
  if (editing) {
    return (
      <li className="p-3">
        <BranchForm branch={branch} onDone={() => setEditing(false)} />
      </li>
    );
  }
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
      <div className="min-w-0">
        <p className="font-medium">
          {branch.name}
          {branch.other_names.length > 0 && (
            <span className="font-normal text-muted"> · also {branch.other_names.join(", ")}</span>
          )}
        </p>
        <p className="text-[13px] text-muted">
          {branch.manager_name || "Manager"} · <span className="tabular">{branch.manager_phone}</span>
        </p>
      </div>
      {canEdit && (
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" onClick={() => setEditing(true)}>
            Edit
          </Button>
          <Button
            variant="ghost"
            size="sm"
            loading={remove.isPending}
            onClick={async () => {
              const { ok } = await confirm({
                title: `Remove ${branch.name}?`,
                body: "Its customers' calls will go to each agent's fixed transfer number instead.",
                confirmLabel: "Remove",
                tone: "danger",
              });
              if (ok) remove.mutate();
            }}
          >
            Remove
          </Button>
        </div>
      )}
    </li>
  );
}

export function BranchSettings({ canEdit }: { canEdit: boolean }) {
  const [adding, setAdding] = useState(false);
  const branches = useQuery({ queryKey: ["branches"], queryFn: () => api<Branch[]>("/branches") });
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-[60ch] text-muted">
          When an agent is set to transfer to the customer&apos;s branch manager, the call goes to the manager
          of the branch where the customer took their loan (the call&apos;s{" "}
          <strong className="text-ink">branch</strong> detail, sent by n8n, your apps or a campaign sheet).
        </p>
        {canEdit && !adding && (
          <Button onClick={() => setAdding(true)}>
            <Plus className="size-4" aria-hidden />
            New branch
          </Button>
        )}
      </div>
      {adding && <BranchForm onDone={() => setAdding(false)} />}
      {branches.isPending ? (
        <Skeleton className="h-32 rounded-xl" />
      ) : branches.isError ? (
        <ErrorNotice error={branches.error} onRetry={() => branches.refetch()} />
      ) : branches.data.length === 0 ? (
        <EmptyState icon={Building2} title="No branches yet">
          Add each branch with its manager&apos;s phone, e.g. Chintamani and Bangalore.
        </EmptyState>
      ) : (
        <ul className="divide-y divide-line rounded-xl border border-line">
          {branches.data.map((b) => (
            <BranchRow key={b.id} branch={b} canEdit={canEdit} />
          ))}
        </ul>
      )}
    </div>
  );
}
