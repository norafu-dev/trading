import { Suspense } from "react";

import { FullScreenSpinner } from "@/components/auth/AuthGate";
import { LoginForm } from "@/components/auth/login-form";

// 静态导出时，useSearchParams 必须在 Suspense 里面。
export default function LoginPage() {
  return (
    <Suspense fallback={<FullScreenSpinner />}>
      <LoginForm />
    </Suspense>
  );
}
