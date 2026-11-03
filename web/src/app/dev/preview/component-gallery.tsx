"use client";

import { PlusIcon, RotateCwIcon, Trash2Icon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

export function ButtonGallery() {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <Button>
          <PlusIcon data-icon="inline-start" aria-hidden />
          添加账号
        </Button>
        <Button variant="secondary">
          <RotateCwIcon data-icon="inline-start" aria-hidden />
          重试
        </Button>
        <Button variant="outline">取消</Button>
        <Button variant="ghost">更多</Button>
        <Button variant="destructive">
          <Trash2Icon data-icon="inline-start" aria-hidden />
          删除
        </Button>
        <Button variant="link">查看详情</Button>
        <Button disabled>
          <Spinner data-icon="inline-start" />
          保存中
        </Button>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button size="lg">大按钮</Button>
        <Button size="sm">小按钮</Button>
        <Button size="xs">更小</Button>
        <Button size="icon" variant="secondary" aria-label="添加">
          <PlusIcon aria-hidden />
        </Button>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Badge>在线</Badge>
        <Badge variant="secondary">未知</Badge>
        <Badge variant="outline">线程</Badge>
        <Badge variant="destructive">已删除</Badge>
      </div>
    </div>
  );
}
