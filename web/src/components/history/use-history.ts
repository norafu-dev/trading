import {
  useInfiniteQuery,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";

import { guildChannelsKey, type ManagedChannel } from "@/components/channels/use-guild-channels";
import { CHANNELS_POLL_INTERVAL_MS, useGuilds } from "@/components/channels/use-guilds";
import { SYSTEM_STATUS_KEY, useSystemStatus } from "@/components/system/use-system-status";
import { api } from "@/lib/api/client";
import { toApiError, toUiError } from "@/lib/api/errors";
import { HISTORY_KEY } from "@/lib/events/event-stream";

import {
  type BatchAction,
  type CreateJobsResult,
  describeFill,
  FINISHED_RANGE_DAYS,
  type FinishedRange,
  type HistorySettings,
  type JobAction,
  type SettingsValues,
} from "./describe-history";

export const HISTORY_JOBS_KEY = [...HISTORY_KEY, "jobs", "active"] as const;

// SSE 事件丢了时的兜底（spec 0012 AC-14）。
const HISTORY_POLL_INTERVAL_MS = 30_000;

export function useActiveHistoryJobs() {
  return useQuery({
    queryKey: HISTORY_JOBS_KEY,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/history/jobs", {
        params: { query: { view: "active" } },
      });
      if (!data) throw toApiError(response.status, error);
      return data;
    },
    refetchInterval: HISTORY_POLL_INTERVAL_MS,
  });
}

const FINISHED_PAGE_SIZE = 30;

/** 「已结束」列表，按结束时间倒序分页加载（AC-14）。 */
export function useFinishedHistoryJobs(range: FinishedRange) {
  const sinceDays = FINISHED_RANGE_DAYS[range];
  return useInfiniteQuery({
    queryKey: [...HISTORY_KEY, "jobs", "finished", sinceDays] as const,
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) => {
      const { data, error, response } = await api.GET("/api/history/jobs", {
        params: {
          query: {
            view: "finished",
            since_days: sinceDays,
            limit: FINISHED_PAGE_SIZE,
            ...(pageParam ? { before_id: pageParam } : {}),
          },
        },
      });
      if (!data) throw toApiError(response.status, error);
      return data;
    },
    getNextPageParam: (last) => last.next_before_id,
    refetchInterval: HISTORY_POLL_INTERVAL_MS,
  });
}

function useActionMutation<T>(run: (variables: T) => Promise<void>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: run,
    onError: (error) => {
      const { title, detail } = toUiError(error);
      toast.error(title, { description: detail });
    },
    // 成功和失败（比如状态刚被别处改了）都重新取一次，让列表回到真实状态。
    onSettled: () => void queryClient.invalidateQueries({ queryKey: HISTORY_KEY }),
  });
}

/** 单个任务的暂停、继续、取消、重试。 */
export function useJobAction() {
  return useActionMutation(
    async ({ jobId, action }: Readonly<{ jobId: string; action: JobAction }>) => {
      const { data, error, response } = await api.POST("/api/history/jobs/{job_id}/{action}", {
        params: { path: { job_id: jobId, action } },
      });
      if (!data) throw toApiError(response.status, error);
    },
  );
}

/** 整批暂停、继续、取消；只作用于状态符合的行。 */
export function useBatchAction() {
  return useActionMutation(
    async ({ batchId, action }: Readonly<{ batchId: string; action: BatchAction }>) => {
      const { data, error, response } = await api.POST("/api/history/batches/{batch_id}/{action}", {
        params: { path: { batch_id: batchId, action } },
      });
      if (!data) throw toApiError(response.status, error);
    },
  );
}

/** 批次里一个频道的所有子任务一起暂停、继续、取消。 */
export function useChannelAction() {
  return useActionMutation(
    async ({
      batchId,
      channelId,
      action,
    }: Readonly<{ batchId: string; channelId: string; action: BatchAction }>) => {
      const { data, error, response } = await api.POST(
        "/api/history/batches/{batch_id}/channels/{channel_id}/{action}",
        { params: { path: { batch_id: batchId, channel_id: channelId, action } } },
      );
      if (!data) throw toApiError(response.status, error);
    },
  );
}

export type CreateJobsBody = Readonly<{
  channel_ids: readonly string[];
  range_start: string | null;
  range_end: string | null;
  refetch: boolean;
}>;

export function useCreateHistoryJobs() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: CreateJobsBody): Promise<CreateJobsResult> => {
      const { data, error, response } = await api.POST("/api/history/jobs", {
        body: { ...body, channel_ids: [...body.channel_ids] },
      });
      if (!data) throw toApiError(response.status, error);
      return data;
    },
    onSettled: () => void queryClient.invalidateQueries({ queryKey: HISTORY_KEY }),
  });
}

export type SelectableGuild = Readonly<{
  id: string;
  name: string;
  channels: readonly ManagedChannel[];
}>;

/** 能建历史任务的频道：小号能读、没删除、类型能采集；不要求开启实时采集（AC-1）。 */
export function isSelectable(channel: ManagedChannel): boolean {
  return channel.can_read && channel.status !== "deleted" && channel.collectable;
}

/** 按服务器分组的可选频道；每个服务器的频道列表来自现有的频道接口。 */
export function useSelectableChannels(enabled: boolean) {
  const guilds = useGuilds();
  const active = (guilds.data?.guilds ?? []).filter((g) => g.status === "active");
  const channelQueries = useQueries({
    queries: active.map((guild) => ({
      queryKey: guildChannelsKey(guild.id),
      enabled,
      queryFn: async () => {
        const { data, error, response } = await api.GET("/api/discord/guilds/{guild_id}/channels", {
          params: { path: { guild_id: guild.id } },
        });
        if (!data) throw toApiError(response.status, error);
        return data.channels;
      },
      refetchInterval: CHANNELS_POLL_INTERVAL_MS,
    })),
  });
  // 频道数量不多，每次渲染重新分组就够了。
  const groups: readonly SelectableGuild[] = active
    .map((guild, index) => ({
      id: guild.id,
      name: guild.name,
      channels: (channelQueries[index]?.data ?? []).filter(isSelectable),
    }))
    .filter((g) => g.channels.length > 0);
  return {
    groups,
    isPending: guilds.isPending || channelQueries.some((q) => q.isPending && enabled),
    isError: guilds.isError || channelQueries.some((q) => q.isError),
    error: guilds.error ?? channelQueries.find((q) => q.error)?.error ?? null,
  };
}

export const HISTORY_GAPS_KEY = [...HISTORY_KEY, "gaps"] as const;

/** 有缺口的频道；给了频道 ID 就只看这一个（AC-15）。 */
export function useHistoryGaps(channelId?: string) {
  return useQuery({
    queryKey: [...HISTORY_GAPS_KEY, channelId ?? "all"] as const,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/history/gaps", {
        params: { query: channelId ? { channel_id: channelId } : {} },
      });
      if (!data) throw toApiError(response.status, error);
      return data;
    },
    refetchInterval: HISTORY_POLL_INTERVAL_MS,
  });
}

/** 「补缺口」和「全部补上」：一段缺口一行（AC-15）。 */
export function useFillGaps() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (channelIds: readonly string[]): Promise<CreateJobsResult> => {
      const { data, error, response } = await api.POST("/api/history/gaps/fill", {
        body: { channel_ids: [...channelIds] },
      });
      if (!data) throw toApiError(response.status, error);
      return data;
    },
    onSuccess: (data) => {
      toast.success(describeFill(data));
    },
    onError: (error) => {
      const { title, detail } = toUiError(error);
      toast.error(title, { description: detail });
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: HISTORY_KEY });
      void queryClient.invalidateQueries({ queryKey: SYSTEM_STATUS_KEY });
    },
  });
}

export const HISTORY_SETTINGS_KEY = [...HISTORY_KEY, "settings"] as const;

/** 总开关、整体等待和用量都在系统状态里；历史事件和 30 秒轮询会刷新它。 */
export function useHistoryStatus() {
  const system = useSystemStatus();
  return { ...system, data: system.data?.history };
}

export function useHistoryControl() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (paused: boolean) => {
      const { data, error, response } = await api.POST("/api/history/control", {
        body: { paused },
      });
      if (!data) throw toApiError(response.status, error);
      return data;
    },
    onError: (error) => {
      const { title, detail } = toUiError(error);
      toast.error(title, { description: detail });
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: HISTORY_KEY });
      void queryClient.invalidateQueries({ queryKey: SYSTEM_STATUS_KEY });
    },
  });
}

export function useHistorySettings() {
  return useQuery({
    queryKey: HISTORY_SETTINGS_KEY,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/history/settings");
      if (!data) throw toApiError(response.status, error);
      return data;
    },
    refetchInterval: HISTORY_POLL_INTERVAL_MS,
  });
}

export function useSaveHistorySettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: SettingsValues) => {
      const { data, error, response } = await api.PUT("/api/history/settings", { body });
      if (!data) throw toApiError(response.status, error);
      return data;
    },
    onSuccess: (data: HistorySettings) => {
      queryClient.setQueryData(HISTORY_SETTINGS_KEY, data);
      toast.success("已保存，几秒内生效");
    },
    onError: (error) => {
      const { title, detail } = toUiError(error);
      toast.error(title, { description: detail });
    },
    onSettled: () => void queryClient.invalidateQueries({ queryKey: SYSTEM_STATUS_KEY }),
  });
}
