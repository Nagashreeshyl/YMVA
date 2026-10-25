import clsx, { type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

// Teach tailwind-merge our custom font size so `text-2xs` isn't mistaken for a text colour.
const twMerge = extendTailwindMerge({ extend: { theme: { text: ["2xs"] } } });

/** Join class names; later Tailwind classes override conflicting earlier ones. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
