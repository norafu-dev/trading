import { Fragment } from "react";

import { splitHighlights } from "./highlight";

type HighlightedTextProps = Readonly<{ text: string; terms?: readonly string[] }>;

// 没有命中词时原样返回文字，和不搜索时的显示完全一样。
export function HighlightedText({ text, terms }: HighlightedTextProps) {
  if (!terms || terms.length === 0) return text;
  return splitHighlights(text, terms).map((part, index) =>
    part.hit ? (
      // biome-ignore lint/suspicious/noArrayIndexKey: 片段由文字算出，顺序固定
      <mark key={index} className="rounded-sm bg-highlight px-0.5 text-inherit">
        {part.text}
      </mark>
    ) : (
      // biome-ignore lint/suspicious/noArrayIndexKey: 同上
      <Fragment key={index}>{part.text}</Fragment>
    ),
  );
}
