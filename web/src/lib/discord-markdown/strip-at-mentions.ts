// @用户、@身份组、@everyone、@here 在 dashboard 里不显示（spec 0015），连同后面的空格一起去掉。
// 规则和后端 `harvester.db.messages.strip_at_mentions` 一致，改一边要同步改另一边。
const AT_MENTION = /(?:<@[!&]?\d+>|(?<!\w)@(?:everyone|here)\b)[ \t]*/g;

/** 去掉 @ 提及；改过的行去掉行尾空格，只剩提及的行整行去掉。没有提及时原样返回。 */
export function stripAtMentions(text: string): string {
  return text
    .split("\n")
    .flatMap((line) => {
      const stripped = line.replace(AT_MENTION, "");
      if (stripped === line) return [line];
      return stripped.trim() ? [stripped.trimEnd()] : [];
    })
    .join("\n");
}
