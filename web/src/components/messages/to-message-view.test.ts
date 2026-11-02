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
      attachmentNames: ["a.png"],
    });
  });
});
