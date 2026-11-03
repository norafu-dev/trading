import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// 没开 globals，Testing Library 不会自动清理，这里每个测试后手动卸载。
afterEach(() => {
  cleanup();
});
