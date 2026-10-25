import { PageHeader, EmptyState, Badge } from "./ui";

/** Honest placeholder for a section whose feature ships in a later build phase. */
export function PlannedSection({
  title,
  description,
  icon,
  emptyTitle,
  phase,
  points,
}: {
  title: string;
  description: string;
  icon: React.ComponentType<{ className?: string; strokeWidth?: number }>;
  emptyTitle: string;
  phase: string;
  points: string[];
}) {
  return (
    <>
      <PageHeader title={title} description={description} />
      <EmptyState icon={icon} title={emptyTitle} action={<Badge tone="gold">Arrives in {phase}</Badge>}>
        <ul className="mt-2 flex flex-col gap-1 text-left sm:inline-flex">
          {points.map((p) => (
            <li key={p} className="flex gap-2">
              <span aria-hidden className="mt-[9px] size-1 shrink-0 rounded-full bg-faint" />
              {p}
            </li>
          ))}
        </ul>
      </EmptyState>
    </>
  );
}
