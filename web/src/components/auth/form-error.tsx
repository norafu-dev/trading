import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import type { UiError } from "@/lib/api/errors";

export function FormError({ error }: Readonly<{ error: UiError }>) {
  return (
    <Alert variant="destructive">
      <AlertTitle>{error.title}</AlertTitle>
      <AlertDescription>{error.detail}</AlertDescription>
    </Alert>
  );
}
