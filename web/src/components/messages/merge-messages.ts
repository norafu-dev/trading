// 按 ID 去重，新的覆盖旧的，按 ID 从旧到新排。ID 超过 JS 安全整数，比较用 BigInt。
export function compareIds(a: string, b: string): number {
  const left = BigInt(a);
  const right = BigInt(b);
  return left < right ? -1 : left > right ? 1 : 0;
}

export function mergeMessages<T extends Readonly<{ id: string }>>(
  existing: readonly T[],
  incoming: readonly T[],
): readonly T[] {
  const byId = new Map(existing.map((message) => [message.id, message]));
  for (const message of incoming) byId.set(message.id, message);
  return [...byId.values()].sort((a, b) => compareIds(a.id, b.id));
}
