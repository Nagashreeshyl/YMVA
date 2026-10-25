import type { Metadata } from "next";
import { Suspense } from "react";
import { Skeleton } from "@/components/ui";
import { CallView } from "./call-view";

export const metadata: Metadata = { title: "Call" };

export default function CallPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <CallView />
    </Suspense>
  );
}
