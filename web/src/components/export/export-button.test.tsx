import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ExportButton, isExportable } from "./export-button";

describe("ExportButton（AC-1）", () => {
  it.each([0, 5, 10, 11, 12])("类型 %i 显示导出按钮", (type) => {
    render(<ExportButton channelType={type} active={false} onClick={() => {}} />);
    expect(screen.getByRole("button", { name: "导出" })).toBeInTheDocument();
  });

  it.each([2, 4, 13, 15, 16])("类型 %i（语音、分类、论坛、媒体等）不显示", (type) => {
    render(<ExportButton channelType={type} active={false} onClick={() => {}} />);
    expect(screen.queryByRole("button", { name: "导出" })).not.toBeInTheDocument();
    expect(isExportable(type)).toBe(false);
  });

  it("频道详情还没加载时不显示", () => {
    render(<ExportButton channelType={undefined} active={false} onClick={() => {}} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
