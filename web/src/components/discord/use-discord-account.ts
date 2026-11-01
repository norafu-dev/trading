import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { toApiError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { DISCORD_ACCOUNT_KEY } from "@/lib/events/event-stream";

export type DiscordAccount = components["schemas"]["DiscordAccountOut"];
export type AccountAction = "disable" | "enable" | "reconnect" | "switch-identity";

// SSE 断开、或者 collector 挂掉没有事件时，靠这个轮询更新状态（AC-15）。
const POLL_INTERVAL_MS = 30_000;

export function useDiscordAccount() {
  return useQuery({
    queryKey: DISCORD_ACCOUNT_KEY,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/discord/account");
      if (!data) throw toApiError(response.status, error);
      return data.account ?? null;
    },
    refetchInterval: POLL_INTERVAL_MS,
  });
}

function useAccountMutation<Input>(request: (input: Input) => Promise<DiscordAccount>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: request,
    onSuccess: (account) => queryClient.setQueryData(DISCORD_ACCOUNT_KEY, account),
  });
}

export function useAddAccount() {
  return useAccountMutation(async (body: Readonly<{ label: string; token: string }>) => {
    const { data, error, response } = await api.POST("/api/discord/account", { body });
    if (!data) throw toApiError(response.status, error);
    return data;
  });
}

export function useReplaceToken() {
  return useAccountMutation(async (token: string) => {
    const { data, error, response } = await api.PUT("/api/discord/account/token", {
      body: { token },
    });
    if (!data) throw toApiError(response.status, error);
    return data;
  });
}

export function useUpdateLabel() {
  return useAccountMutation(async (label: string) => {
    const { data, error, response } = await api.PUT("/api/discord/account/label", {
      body: { label },
    });
    if (!data) throw toApiError(response.status, error);
    return data;
  });
}

export function useAccountAction() {
  return useAccountMutation(async (action: AccountAction) => {
    const { data, error, response } = await api.POST(`/api/discord/account/${action}`);
    if (!data) throw toApiError(response.status, error);
    return data;
  });
}
