"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { toApiError } from "@/lib/api/errors";

export const AUTH_STATE_KEY = ["auth", "state"] as const;

export function useAuthState() {
  return useQuery({
    queryKey: AUTH_STATE_KEY,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/auth/state");
      if (!data) throw toApiError(response.status, error);
      return data.state;
    },
    staleTime: 0,
    retry: 2,
  });
}
