import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

export function Input({ className, type = "text", ...props }: ComponentProps<"input">) {
  return (
    <input
      type={type}
      className={cn(
        "flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-xs transition-colors placeholder:text-muted-foreground file:border-0 file:bg-transparent file:text-sm file:font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-60 aria-invalid:border-destructive",
        className,
      )}
      {...props}
    />
  );
}
