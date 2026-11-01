import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api } from "@/lib/api/client";
import { toApiError, toUiError } from "@/lib/api/errors";
import { COLLECTED_KEY, GUILD_CHANNELS_KEY, GUILDS_KEY } from "@/lib/events/event-stream";

import { guildChannelsKey, type ManagedChannel } from "./use-guild-channels";

type SetCollectInput = Readonly<{ channel: ManagedChannel; enabled: boolean }>;

type Context = Readonly<{ previous: readonly ManagedChannel[] | undefined }>;

// 开关用乐观更新：先改页面，请求失败时回滚并提示（spec 0006 AC-12）。
export function useSetCollect() {
  const queryClient = useQueryClient();
  return useMutation<ManagedChannel, unknown, SetCollectInput, Context>({
    mutationFn: async ({ channel, enabled }) => {
      const { data, error, response } = await api.PUT(
        "/api/discord/channels/{channel_id}/collect",
        {
          params: { path: { channel_id: channel.id } },
          body: { enabled },
        },
      );
      if (!data) throw toApiError(response.status, error);
      return data;
    },
    onMutate: async ({ channel, enabled }) => {
      const key = guildChannelsKey(channel.guild_id);
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<readonly ManagedChannel[]>(key);
      queryClient.setQueryData<readonly ManagedChannel[]>(key, (current) =>
        current?.map((item) =>
          item.id === channel.id ? { ...item, collect_enabled: enabled } : item,
        ),
      );
      return { previous };
    },
    onError: (error, { channel }, context) => {
      queryClient.setQueryData(guildChannelsKey(channel.guild_id), context?.previous);
      toast.error(`没能更新「${channel.name}」`, { description: toUiError(error).detail });
    },
    onSettled: () => {
      for (const queryKey of [GUILDS_KEY, GUILD_CHANNELS_KEY, COLLECTED_KEY]) {
        void queryClient.invalidateQueries({ queryKey });
      }
    },
  });
}

// 分类批量不做乐观更新：哪些频道会被跳过要由服务器决定，结果用 toast 说明（AC-9）。
export function useSetCategoryCollect() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      categoryId,
      enabled,
    }: Readonly<{ categoryId: string; enabled: boolean }>) => {
      const { data, error, response } = await api.PUT(
        "/api/discord/categories/{category_id}/collect",
        { params: { path: { category_id: categoryId } }, body: { enabled } },
      );
      if (!data) throw toApiError(response.status, error);
      return data;
    },
    onSuccess: ({ changed, skipped_no_access: skipped }, { enabled }) => {
      const action = enabled ? "开启" : "关闭";
      toast.success(
        skipped > 0
          ? `已${action} ${changed} 个频道，跳过 ${skipped} 个无权限频道`
          : `已${action} ${changed} 个频道`,
      );
    },
    onError: (error) => {
      const { title, detail } = toUiError(error);
      toast.error(title, { description: detail });
    },
    onSettled: () => {
      for (const queryKey of [GUILDS_KEY, GUILD_CHANNELS_KEY, COLLECTED_KEY]) {
        void queryClient.invalidateQueries({ queryKey });
      }
    },
  });
}
