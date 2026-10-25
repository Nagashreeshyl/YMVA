"use client";

import { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { Button, Textarea } from "./ui";

type ConfirmOptions = {
  title: string;
  body?: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "default" | "danger";
  /** Ask for an optional note, e.g. what changed in a version. */
  note?: { label: string; placeholder?: string };
};

type Pending = ConfirmOptions & { resolve: (result: { ok: boolean; note: string }) => void };

const DialogContext = createContext<
  ((options: ConfirmOptions) => Promise<{ ok: boolean; note: string }>) | null
>(null);

/** In-app replacement for window.confirm / window.prompt, styled like the rest of YMVA. */
export function DialogProvider({ children }: { children: React.ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const ask = useCallback(
    (options: ConfirmOptions) =>
      new Promise<{ ok: boolean; note: string }>((resolve) => setPending({ ...options, resolve })),
    [],
  );
  return (
    <DialogContext.Provider value={ask}>
      {children}
      {pending && (
        <ConfirmDialog
          options={pending}
          onClose={(ok, note) => {
            pending.resolve({ ok, note });
            setPending(null);
          }}
        />
      )}
    </DialogContext.Provider>
  );
}

function ConfirmDialog({
  options,
  onClose,
}: {
  options: ConfirmOptions;
  onClose: (ok: boolean, note: string) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [note, setNote] = useState("");
  const titleId = useId();
  const bodyId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      aria-describedby={options.body ? bodyId : undefined}
      onCancel={(e) => {
        e.preventDefault();
        onClose(false, "");
      }}
      onClick={(e) => {
        if (e.target === ref.current) onClose(false, ""); // click on the backdrop
      }}
      className="m-auto w-[calc(100%-32px)] max-w-md rounded-2xl border border-line bg-paper p-0 text-ink shadow-float backdrop:bg-black/40 backdrop:backdrop-blur-[2px]"
    >
      <form
        method="dialog"
        className="flex flex-col gap-4 p-6"
        onSubmit={(e) => {
          e.preventDefault();
          onClose(true, note.trim());
        }}
      >
        <h2 id={titleId} className="text-lg font-semibold">
          {options.title}
        </h2>
        {options.body && (
          <div id={bodyId} className="text-muted">
            {options.body}
          </div>
        )}
        {options.note && (
          <label className="flex flex-col gap-1.5">
            <span className="text-[13px] font-medium">{options.note.label}</span>
            <Textarea
              rows={2}
              className="min-h-0"
              value={note}
              placeholder={options.note.placeholder}
              maxLength={500}
              onChange={(e) => setNote(e.target.value)}
              autoFocus
            />
          </label>
        )}
        <div className="mt-2 flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={() => onClose(false, "")}>
            {options.cancelLabel ?? "Cancel"}
          </Button>
          <Button
            type="submit"
            autoFocus={!options.note}
            className={cn(options.tone === "danger" && "bg-danger text-paper")}
          >
            {options.confirmLabel ?? "Confirm"}
          </Button>
        </div>
      </form>
    </dialog>
  );
}

export function useConfirm() {
  const ask = useContext(DialogContext);
  if (!ask) throw new Error("useConfirm must be used inside DialogProvider");
  return ask;
}
