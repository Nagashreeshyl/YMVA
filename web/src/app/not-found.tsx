import Link from "next/link";

export default function NotFound() {
  return (
    <main className="grid min-h-dvh place-items-center px-4">
      <div className="flex max-w-sm flex-col items-center gap-3 text-center">
        <h1 className="font-display text-[28px] font-extrabold tracking-[-0.02em]">Page not found</h1>
        <p className="text-muted">This page doesn&apos;t exist or has moved.</p>
        <Link
          href="/"
          className="mt-2 inline-flex h-10 items-center rounded-full bg-ink px-5 text-sm font-medium text-on-ink"
        >
          Back to Overview
        </Link>
      </div>
    </main>
  );
}
