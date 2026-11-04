import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";

import { toMessageView } from "./to-message-view";

type MessageOut = components["schemas"]["MessageOut"];

const BASE: MessageOut = {
  id: "9007199254740993",
  channel_id: "1",
  author_id: "2",
  author_name: "阿青",
  author_avatar_hash: "abc",
  created_at: "2026-11-02T00:00:00Z",
  content: "hi",
  embeds: [],
  forwarded: null,
  attachments: [],
  media: [],
  edited_at: null,
  deleted_at: null,
  reply: null,
};

describe("toMessageView", () => {
  // covers: spec 0008 AC-14
  it("字段转成视图，ID 保持字符串", () => {
    const view = toMessageView({
      ...BASE,
      deleted_at: "2026-11-02T01:00:00Z",
      embeds: [{ title: "t", author: { name: "a" }, fields: [{ name: "n", value: "v" }] }],
      attachments: [{ id: "5", filename: "a.png", content_type: null, size_bytes: 3 }],
    });

    expect(view.id).toBe("9007199254740993");
    expect(view.deleted).toBe(true);
    expect(view.authorAvatarUrl).toContain("/avatars/2/abc");
    expect(view.embeds[0]).toMatchObject({
      title: "t",
      description: null,
      author: { name: "a", url: null },
      fields: [{ name: "n", value: "v", inline: false }],
    });
    expect(view.attachments[0]?.sizeBytes).toBe(3);
  });

  // covers: spec 0008 AC-20
  it("回复的三种状态", () => {
    expect(toMessageView(BASE).replyTo).toBeNull();
    expect(toMessageView({ ...BASE, reply: { id: "9", missing: true } }).replyTo).toEqual({
      missing: true,
    });
    expect(
      toMessageView({
        ...BASE,
        reply: { id: "9", missing: false, author_name: "鹿", preview: "原文", deleted: true },
      }).replyTo,
    ).toEqual({ missing: false, authorName: "鹿", preview: "原文", deleted: true });
  });

  it("转发的 ID 和附件名", () => {
    const view = toMessageView({
      ...BASE,
      forwarded: {
        message_id: "7",
        channel_id: "8",
        guild_id: null,
        content: "f",
        embeds: [],
        attachment_names: ["a.png"],
      },
    });
    expect(view.forwarded).toMatchObject({
      messageId: "7",
      guildId: null,
      attachments: [{ filename: "a.png", media: null }],
    });
  });
});

describe("toMessageView media", () => {
  // covers: spec 0011 AC-15
  it("按附件 ID、转发序号和 embed 序号挂上 media", () => {
    const mediaRow = (id: string, over: Partial<components["schemas"]["MediaOut"]>) => ({
      id,
      kind: "attachment" as const,
      attachment_id: null,
      slot: null,
      status: "stored" as const,
      filename: "f.png",
      content_type: "image/png",
      size_bytes: 1,
      width: 2,
      height: 3,
      has_thumb: true,
      ...over,
    });
    const view = toMessageView({
      ...BASE,
      attachments: [{ id: "50", filename: "f.png", content_type: "image/png", size_bytes: 1 }],
      embeds: [{ image_url: "https://cdn.discordapp.com/attachments/1/2/e.png" }],
      forwarded: {
        message_id: "7",
        channel_id: "8",
        guild_id: null,
        content: "",
        embeds: [],
        attachment_names: ["a.png", "b.png"],
      },
      media: [
        mediaRow("1", { attachment_id: "50" }),
        mediaRow("2", { kind: "forwarded_attachment", slot: 1 }),
        mediaRow("3", { kind: "embed_image", slot: 0 }),
      ],
    });

    expect(view.attachments[0]?.media?.id).toBe("1");
    expect(view.forwarded?.attachments.map((a) => a.media?.id ?? null)).toEqual([null, "2"]);
    expect(view.embeds[0]?.imageMedia?.id).toBe("3");
    expect(view.embeds[0]?.thumbnailMedia).toBeNull();
  });
});
