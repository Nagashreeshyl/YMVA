"use client";

import { useQuery } from "@tanstack/react-query";
import { api, type User } from "@/lib/api";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api<User>("/auth/me"), staleTime: 60_000 });
}
