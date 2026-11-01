const PASSWORD_MIN = 12;
const PASSWORD_MAX = 128;

// 和后端同样的算法：NFKC 规范化之后按 Unicode 码点数（AC-4）。
export function passwordLength(password: string): number {
  return [...password.normalize("NFKC")].length;
}

export function passwordProblem(password: string, confirm: string): string | null {
  const length = passwordLength(password);
  if (length < PASSWORD_MIN || length > PASSWORD_MAX) {
    return `密码长度要在 ${PASSWORD_MIN} 到 ${PASSWORD_MAX} 个字符之间。`;
  }
  return password === confirm ? null : "两次输入的密码不一样。";
}
