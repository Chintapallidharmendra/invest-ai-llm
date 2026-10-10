import { useMutation } from "@tanstack/react-query";
import { useState, type SyntheticEvent } from "react";

import { api } from "@/api/client";
import { isProblemError } from "@/api/problem";
import type { components } from "@/api/schema";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { POLICY_HINT, reasonMessages } from "@/routes/set-password/passwordPolicy";

type ChangePasswordRequest = components["schemas"]["ChangePasswordRequest"];

export const CHANGED_NOTICE = "Your password was changed. Other devices were signed out.";

function errorMessages(error: unknown): string[] {
  if (!isProblemError(error)) return ["The password could not be changed. Try again."];
  if (error.code === "current_password_invalid") return ["Your current password is incorrect."];
  if (error.code === "password_unchanged") return ["Choose a password different from your current one."];
  if (error.code === "password_rejected") return reasonMessages(error.extensions.reasons);
  return ["The password could not be changed. Try again."];
}

export function ChangePasswordPage() {
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [mismatch, setMismatch] = useState(false);

  const change = useMutation({
    mutationFn: (body: ChangePasswordRequest) => api.post("/auth/change-password", body),
    onSuccess: () => {
      setCurrent("");
      setPassword("");
      setConfirm("");
    },
  });

  function onSubmit(event: SyntheticEvent) {
    event.preventDefault();
    if (password !== confirm) {
      setMismatch(true);
      return;
    }
    setMismatch(false);
    change.mutate({ current_password: current, new_password: password });
  }

  const errors = change.isError ? errorMessages(change.error) : [];

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Change password</h1>
      <Card className="max-w-md">
        <CardHeader>
          <CardDescription>Other devices signed in to your account will be signed out.</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={onSubmit} noValidate>
            {change.isSuccess ? (
              <p role="status" className="rounded-md bg-muted p-3 text-sm">
                {CHANGED_NOTICE}
              </p>
            ) : null}
            {errors.length > 0 ? (
              <div role="alert" className="space-y-1 rounded-md bg-destructive/10 p-3 text-sm text-destructive">
                {errors.map((message) => (
                  <p key={message}>{message}</p>
                ))}
              </div>
            ) : null}
            <div className="space-y-2">
              <Label htmlFor="current-password">Current password</Label>
              <Input
                id="current-password"
                type="password"
                autoComplete="current-password"
                required
                value={current}
                onChange={(e) => {
                  setCurrent(e.target.value);
                }}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="new-password">New password</Label>
              <Input
                id="new-password"
                type="password"
                autoComplete="new-password"
                aria-describedby="policy"
                required
                value={password}
                onChange={(e) => {
                  setPassword(e.target.value);
                }}
              />
              <p id="policy" className="text-xs text-muted-foreground">
                {POLICY_HINT}
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="confirm-password">Confirm new password</Label>
              <Input
                id="confirm-password"
                type="password"
                autoComplete="new-password"
                required
                aria-invalid={mismatch}
                aria-describedby={mismatch ? "mismatch" : undefined}
                value={confirm}
                onChange={(e) => {
                  setConfirm(e.target.value);
                }}
              />
              {mismatch ? (
                <p id="mismatch" role="alert" className="text-xs text-destructive">
                  The passwords don't match.
                </p>
              ) : null}
            </div>
            <Button type="submit" disabled={change.isPending}>
              {change.isPending ? "Changing…" : "Change password"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
