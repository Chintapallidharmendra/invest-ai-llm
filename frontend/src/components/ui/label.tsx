import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

// A native <label>: it already gives click-to-focus and the accessible name.
export function Label({ className, ...props }: ComponentProps<"label">) {
  return (
    <label
      className={cn(
        "text-sm leading-none font-medium peer-disabled:cursor-not-allowed peer-disabled:opacity-60",
        className,
      )}
      {...props}
    />
  );
}
