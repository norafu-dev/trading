// 对比度检查（spec 0003，AC-14）：从 src/app/globals.css 读出浅色和深色两套 token，
// 按 spec 列出的每一组前景和背景算 WCAG 对比度。普通文字要求 4.5:1，焦点环和图标 3:1。
// 不达标就以非零状态退出。运行：pnpm check:contrast
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

type Rgba = Readonly<{ r: number; g: number; b: number; a: number }>;
type Tokens = ReadonlyMap<string, Rgba>;
type Pair = Readonly<{ fg: string; bg: string; min: number; over?: string }>;

const TEXT = 4.5;
const NON_TEXT = 3;

const PAIRS: readonly Pair[] = [
  ...["background", "muted", "card", "accent"].map((bg) => ({ fg: "foreground", bg, min: TEXT })),
  // 提及的底色是半透明的，叠在 background 上再算。
  { fg: "foreground", bg: "mention", over: "background", min: TEXT },
  // 搜索命中词的底色也是半透明的，可能叠在背景或 embed 的卡片底色上。
  ...["background", "card"].map((over) => ({
    fg: "foreground",
    bg: "highlight",
    over,
    min: TEXT,
  })),
  ...["background", "muted", "card"].map((bg) => ({ fg: "muted-foreground", bg, min: TEXT })),
  { fg: "primary-foreground", bg: "primary", min: TEXT },
  { fg: "secondary-foreground", bg: "secondary", min: TEXT },
  { fg: "accent-foreground", bg: "accent", min: TEXT },
  { fg: "destructive", bg: "background", min: TEXT },
  { fg: "destructive-foreground", bg: "destructive", min: TEXT },
  { fg: "link", bg: "background", min: TEXT },
  { fg: "link", bg: "card", min: TEXT },
  { fg: "rail-foreground", bg: "rail", min: TEXT },
  ...[1, 2, 3, 4, 5, 6].map((n) => ({ fg: "avatar-foreground", bg: `avatar-${n}`, min: TEXT })),
  ...["background", "muted", "rail"].map((bg) => ({ fg: "ring", bg, min: NON_TEXT })),
];

function parseColor(value: string): Rgba {
  const hex = value.match(/^#([0-9a-f]{6})$/i)?.[1];
  if (hex) {
    const n = Number.parseInt(hex, 16);
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255, a: 1 };
  }
  const rgb = value.match(/^rgb\(\s*(\d+)\s+(\d+)\s+(\d+)\s*(?:\/\s*([\d.]+))?\s*\)$/);
  if (rgb) {
    return { r: Number(rgb[1]), g: Number(rgb[2]), b: Number(rgb[3]), a: Number(rgb[4] ?? 1) };
  }
  throw new Error(`不认识的颜色写法：${value}`);
}

function readTokens(block: string): Map<string, Rgba> {
  const tokens = new Map<string, Rgba>();
  for (const match of block.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6}|rgb\([^)]*\))\s*;/g)) {
    const [, name, value] = match;
    if (name && value) tokens.set(name, parseColor(value));
  }
  return tokens;
}

export function themesFromCss(css: string): Readonly<{ light: Tokens; dark: Tokens }> {
  const rootStart = css.indexOf(":root {");
  // 要找的是写深色值的那个媒体查询块，不是文件开头 @custom-variant 里的同名查询。
  const darkStart = css.indexOf("@media (prefers-color-scheme: dark) {");
  if (rootStart < 0 || darkStart < 0) throw new Error("globals.css 里找不到 :root 或深色媒体查询");
  const light = readTokens(css.slice(rootStart, darkStart));
  const darkBlockEnd = css.indexOf("@layer base", darkStart);
  const dark = new Map([...light, ...readTokens(css.slice(darkStart, darkBlockEnd))]);
  return { light, dark };
}

function blend(top: Rgba, bottom: Rgba): Rgba {
  const mix = (a: number, b: number) => a * top.a + b * (1 - top.a);
  return { r: mix(top.r, bottom.r), g: mix(top.g, bottom.g), b: mix(top.b, bottom.b), a: 1 };
}

function luminance({ r, g, b }: Rgba): number {
  const channel = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

export function contrastRatio(fg: Rgba, bg: Rgba): number {
  const [hi, lo] = [luminance(fg), luminance(bg)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

export type PairResult = Readonly<{ theme: string; pair: Pair; ratio: number; ok: boolean }>;

export function checkTheme(theme: string, tokens: Tokens): readonly PairResult[] {
  return PAIRS.map((pair) => {
    const fg = tokens.get(pair.fg);
    const rawBg = tokens.get(pair.bg);
    const under = pair.over ? tokens.get(pair.over) : undefined;
    if (!fg || !rawBg || (pair.over && !under)) {
      throw new Error(`${theme} 主题缺少 token：${pair.fg} / ${pair.bg}`);
    }
    const bg = under ? blend(rawBg, under) : rawBg;
    const ratio = contrastRatio(fg, bg);
    return { theme, pair, ratio, ok: ratio >= pair.min };
  });
}

function main() {
  const cssPath = fileURLToPath(new URL("../src/app/globals.css", import.meta.url));
  const { light, dark } = themesFromCss(readFileSync(cssPath, "utf8"));
  const results = [...checkTheme("浅色", light), ...checkTheme("深色", dark)];
  for (const { theme, pair, ratio, ok } of results) {
    const label = `${pair.fg} 对 ${pair.bg}${pair.over ? `（叠在 ${pair.over} 上）` : ""}`;
    console.log(
      `${ok ? "通过" : "不达标"}  ${theme}  ${label}  ${ratio.toFixed(2)}:1（要求 ${pair.min}:1）`,
    );
  }
  const failed = results.filter((result) => !result.ok);
  console.log(
    failed.length === 0 ? `\n全部 ${results.length} 组通过。` : `\n${failed.length} 组不达标。`,
  );
  if (failed.length > 0) process.exitCode = 1;
}

if (fileURLToPath(import.meta.url) === process.argv[1]) main();
