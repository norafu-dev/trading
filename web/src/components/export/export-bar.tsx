"use client";

import { cn } from "cn";
import { DownloadIcon, RotateCwIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { formatGapDuration } from "@/components/history/describe-history";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { toUiError } from "@/lib/api/errors";
import { createTimeFormat } from "@/lib/format/time";

import {
  buildExportUrl,
  type ExportEnd,
  type ExportFormat,
  type ExportSelection,
  type ExportSide,
  toRangeParams,
} from "./export-selection";
import { type ExportPreview, useExportPreview } from "./use-export-preview";

const FORMATS: readonly Readonly<{ value: ExportFormat; label: string }>[] = [
  { value: "csv", label: "CSV" },
  { value: "jsonl", label: "JSONL" },
];

type Props = Readonly<{
  channelId: string;
  selection: ExportSelection;
  timeZone: string;
  onToggleOpen: (side: ExportSide) => void;
  onCancel: () => void;
}>;

function endLabel(end: ExportEnd | undefined, side: ExportSide, timeZone: string): string {
  if (end === undefined) return "未选";
  if (end.kind === "open") return side === "start" ? "最早" : "最新";
  return `${createTimeFormat(timeZone).fullTime(end.createdAt)} · ${end.authorName}`;
}

function endsOf(selection: ExportSelection): Readonly<{ start?: ExportEnd; end?: ExportEnd }> {
  if (selection.step === "ready") return { start: selection.start, end: selection.end };
  if (selection.step === "one") return { start: selection.first };
  return {};
}

function hintFor(selection: ExportSelection): string {
  if (selection.step === "none") return "点一条消息作为起点或终点，也可以直接选下面的快捷按钮";
  if (selection.step === "one-open") return "再点一条消息，或选另一个快捷按钮";
  return "再点一条消息，或选「从最早」「到最新」";
}

// 触发浏览器下载：临时 <a download>，页面不跳走（AC-10）。
function startDownload(url: string) {
  const link = document.createElement("a");
  link.href = url;
  link.download = "";
  document.body.append(link);
  link.click();
  link.remove();
}

function PreviewLine({
  isPending,
  error,
  data,
  onRetry,
}: Readonly<{
  isPending: boolean;
  error: unknown;
  data: ExportPreview | undefined;
  onRetry: () => void;
}>) {
  if (isPending) {
    return (
      <span className="inline-flex items-center gap-1.5 text-muted-foreground">
        <Spinner aria-hidden />
        正在统计
      </span>
    );
  }
  if (error !== null && error !== undefined) {
    const ui = toUiError(error);
    return (
      <span role="alert" className="inline-flex items-center gap-2 text-destructive">
        {ui.title === "统计超时" ? "统计超时，请缩小范围" : `${ui.title}：${ui.detail}`}
        <Button variant="outline" size="xs" onClick={onRetry}>
          <RotateCwIcon data-icon="inline-start" aria-hidden />
          重试
        </Button>
      </span>
    );
  }
  if (!data) return null;
  if (data.over_limit) return <span role="status">超过 10 万条上限，请缩小范围</span>;
  if (data.count === 0) return <span role="status">频道里没有消息</span>;
  return <span role="status">共 {data.count} 条</span>;
}

function GapNotice({ preview }: Readonly<{ preview: ExportPreview }>) {
  if (preview.gaps.length === 0) return null;
  const seconds = preview.gaps.reduce(
    (sum, gap) => sum + (Date.parse(gap.end) - Date.parse(gap.start)) / 1000,
    0,
  );
  return (
    <p className="text-sm text-muted-foreground">
      这段时间里有 {preview.gaps.length} 段没有采集（共约 {formatGapDuration(seconds)}
      ），导出只包含已采集的消息。{" "}
      <Link href="/history" className="underline underline-offset-2">
        去补采
      </Link>
    </p>
  );
}

// 选择模式下固定在频道页底部的操作栏（spec 0014 AC-2 到 AC-10）。
export function ExportBar({ channelId, selection, timeZone, onToggleOpen, onCancel }: Props) {
  const [format, setFormat] = useState<ExportFormat>("csv");
  const range = useMemo(() => toRangeParams(selection), [selection]);
  const preview = useExportPreview(channelId, range);
  const { start, end } = endsOf(selection);
  const startOpen =
    selection.step === "one-open" ? selection.side === "start" : start?.kind === "open";
  const endOpen = selection.step === "one-open" ? selection.side === "end" : end?.kind === "open";
  const canDownload =
    range !== null &&
    preview.data !== undefined &&
    !preview.data.over_limit &&
    preview.data.count > 0;

  return (
    <section
      aria-label="导出"
      className="flex shrink-0 flex-col gap-2 border-t border-border bg-background px-4 py-3"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        <span>
          起点：<strong className="font-semibold">{endLabel(start, "start", timeZone)}</strong>
        </span>
        <span>
          终点：<strong className="font-semibold">{endLabel(end, "end", timeZone)}</strong>
        </span>
        <div className="flex items-center gap-1.5">
          <Button
            variant={startOpen ? "secondary" : "outline"}
            size="sm"
            aria-pressed={startOpen}
            onClick={() => onToggleOpen("start")}
          >
            从最早
          </Button>
          <Button
            variant={endOpen ? "secondary" : "outline"}
            size="sm"
            aria-pressed={endOpen}
            onClick={() => onToggleOpen("end")}
          >
            到最新
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        {range === null ? (
          <span className="text-muted-foreground">{hintFor(selection)}</span>
        ) : (
          <PreviewLine
            isPending={preview.isPending}
            error={preview.error}
            data={preview.data}
            onRetry={() => void preview.refetch()}
          />
        )}
        <div className="ml-auto flex items-center gap-2">
          <fieldset className="flex items-center gap-1" aria-label="文件格式">
            {FORMATS.map((item) => (
              <Button
                key={item.value}
                variant={format === item.value ? "secondary" : "ghost"}
                size="sm"
                aria-pressed={format === item.value}
                onClick={() => setFormat(item.value)}
                className={cn(format === item.value && "font-semibold")}
              >
                {item.label}
              </Button>
            ))}
          </fieldset>
          <Button
            size="sm"
            disabled={!canDownload}
            onClick={() => {
              if (range !== null) startDownload(buildExportUrl(channelId, range, format, timeZone));
            }}
          >
            <DownloadIcon data-icon="inline-start" aria-hidden />
            下载
          </Button>
          <Button variant="ghost" size="sm" onClick={onCancel}>
            <XIcon data-icon="inline-start" aria-hidden />
            取消
          </Button>
        </div>
      </div>
      {preview.data ? <GapNotice preview={preview.data} /> : null}
    </section>
  );
}
