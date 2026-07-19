import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatYield(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(2)}%`;
}

export function formatPrice(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(2);
}

export function formatMarketCap(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(1)}亿`;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return value.slice(0, 10);
}

export function formatUpdatedAt(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}
