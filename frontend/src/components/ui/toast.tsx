import * as ToastPrimitive from "@radix-ui/react-toast";
import { cva, type VariantProps } from "class-variance-authority";
import { X } from "lucide-react";
import { createContext, use, useCallback, useState, type ComponentProps, type ReactNode } from "react";

import { cn } from "@/lib/utils";

const toastVariants = cva(
  "group pointer-events-auto relative flex w-full items-start justify-between gap-3 overflow-hidden rounded-md border p-4 pr-8 shadow-lg",
  {
    variants: {
      variant: {
        default: "border bg-background text-foreground",
        destructive: "border-destructive bg-destructive text-destructive-foreground",
        success: "border-success bg-success text-success-foreground",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export function Toast({
  className,
  variant,
  ...props
}: ComponentProps<typeof ToastPrimitive.Root> & VariantProps<typeof toastVariants>) {
  return <ToastPrimitive.Root className={cn(toastVariants({ variant }), className)} {...props} />;
}

export function ToastTitle({ className, ...props }: ComponentProps<typeof ToastPrimitive.Title>) {
  return <ToastPrimitive.Title className={cn("text-sm font-semibold", className)} {...props} />;
}

export function ToastDescription({ className, ...props }: ComponentProps<typeof ToastPrimitive.Description>) {
  return <ToastPrimitive.Description className={cn("text-sm", className)} {...props} />;
}

export function ToastClose({ className, ...props }: ComponentProps<typeof ToastPrimitive.Close>) {
  return (
    <ToastPrimitive.Close
      className={cn(
        "absolute top-2 right-2 rounded-sm p-1 opacity-80 hover:opacity-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
        className,
      )}
      aria-label="Dismiss"
      {...props}
    >
      <X className="size-4" aria-hidden="true" />
    </ToastPrimitive.Close>
  );
}

export function ToastViewport({ className, ...props }: ComponentProps<typeof ToastPrimitive.Viewport>) {
  return (
    <ToastPrimitive.Viewport
      className={cn(
        "fixed right-0 bottom-0 z-[100] flex max-h-screen w-full flex-col gap-2 p-4 sm:max-w-sm",
        className,
      )}
      {...props}
    />
  );
}

export interface ToastOptions {
  title: string;
  description?: string;
  variant?: "default" | "destructive" | "success";
  /** Milliseconds; Radix default (5 s) when omitted. */
  duration?: number;
}

interface ToastItem extends ToastOptions {
  id: number;
}

type ToastFn = (options: ToastOptions) => void;

const ToastContext = createContext<ToastFn | null>(null);

let nextId = 1;

/** Renders queued toasts; mount once near the root. `useToast()` adds one. */
export function Toaster({ children }: { children?: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const toast = useCallback<ToastFn>((options) => {
    const id = nextId++;
    setItems((current) => [...current, { ...options, id }]);
  }, []);

  const dismiss = useCallback((id: number) => {
    setItems((current) => current.filter((item) => item.id !== id));
  }, []);

  return (
    <ToastContext value={toast}>
      <ToastPrimitive.Provider swipeDirection="right">
        {children}
        {items.map(({ id, title, description, variant, duration }) => (
          <Toast
            key={id}
            variant={variant ?? "default"}
            {...(duration === undefined ? {} : { duration })}
            onOpenChange={(open) => {
              if (!open) dismiss(id);
            }}
          >
            <div className="grid gap-1">
              <ToastTitle>{title}</ToastTitle>
              {description ? <ToastDescription>{description}</ToastDescription> : null}
            </div>
            <ToastClose />
          </Toast>
        ))}
        <ToastViewport />
      </ToastPrimitive.Provider>
    </ToastContext>
  );
}

export function useToast(): ToastFn {
  const toast = use(ToastContext);
  if (!toast) throw new Error("useToast() needs a <Toaster> above it");
  return toast;
}
