"use client";

import { LogOutIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api } from "@/lib/api/client";
import { toApiError, toUiError } from "@/lib/api/errors";
import { hardNavigate } from "@/lib/auth/navigate";

export function LogoutButton() {
  const [pending, setPending] = useState(false);

  async function logout() {
    setPending(true);
    try {
      const { error, response } = await api.POST("/api/auth/logout");
      if (!response.ok) throw toApiError(response.status, error);
      hardNavigate("/login");
    } catch (error) {
      toast.error(toUiError(error).title);
      setPending(false);
    }
  }

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <button
            type="button"
            aria-label="登出"
            disabled={pending}
            onClick={() => void logout()}
            className="flex size-10 items-center justify-center rounded-lg transition-colors hover:bg-rail-foreground/10 disabled:opacity-50"
          />
        }
      >
        <LogOutIcon aria-hidden className="size-5" strokeWidth={1.75} />
      </TooltipTrigger>
      <TooltipContent side="right">登出</TooltipContent>
    </Tooltip>
  );
}
