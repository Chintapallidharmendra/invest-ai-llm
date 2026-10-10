import { useMutation } from "@tanstack/react-query";
import { useEffect, useState, type SyntheticEvent } from "react";
import { Link, useNavigate } from "react-router";

import { api } from "@/api/client";
import { isProblemError } from "@/api/problem";
import type { components } from "@/api/schema";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { POLICY_HINT, reasonMessages } from "./passwordPolicy";

type SetPasswordRequest = components["schemas"]["SetPasswordRequest"];

export const INVALID_LINK =
  "This link is invalid or expired. Ask your administrator for a new one.";

/** The `t` parameter of the URL fragment (`#t=<token>`); the query string is ignored. */
export function tokenFromHash(hash: string): string | null {
  const value = new URLSearchParams(hash.replace(/^#/, "")).get("t");
  return value?.trim() ? value.trim() : null;
}

function errorMessages(error: unknown): string[] {
  if (isProblemError(error) && error.code === "password_rejected") {
    return reasonMessages(error.extensions.reasons);
  }
  if (isProblemError(error) && error.status === 400) return [INVALID_LINK];
  return ["The password could not be set. Try again."];
}

export function SetPasswordPage() {
  const navigate = useNavigate();
  // Read once; the fragment is removed from the address bar and history right after.
  const [token] = useState(() => tokenFromHash(window.location.hash));
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [mismatch, setMismatch] = useState(false);

  useEffect(() => {
    if (window.location.hash) {
      const { pathname, search } = window.location;
      window.history.replaceState(window.history.state, "", `${pathname}${search}`);
    }
  }, []);

  const setPasswordMutation = useMutation({
    mutationFn: (body: SetPasswordRequest) => api.post("/auth/set-password", body, { redirectOn401: false }),
    onSuccess: () => {
      void navigate("/login", {
        replace: true,
        state: { notice: "Your password is set. Sign in with your new password." },
      });
    },
  });

  function onSubmit(event: SyntheticEvent) {
    event.preventDefault();
    if (password !== confirm) {
      setMismatch(true);
      return;
    }
    setMismatch(false);
    if (token) setPasswordMutation.mutate({ token, username, new_password: password });
  }

  const errors = setPasswordMutation.isError ? errorMessages(setPasswordMutation.error) : [];

  return (
    <main className="flex min-h-screen items-center justify-center bg-muted/30 p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <h1 className="text-xl font-semibold">Set your password</h1>
          <CardDescription>Choose the password you'll sign in with.</CardDescription>
        </CardHeader>
        <CardContent>
          {token ? (
            <form className="space-y-4" onSubmit={onSubmit} noValidate>
              {errors.length > 0 ? (
                <div role="alert" className="space-y-1 rounded-md bg-destructive/10 p-3 text-sm text-destructive">
                  {errors.map((message) => (
                    <p key={message}>{message}</p>
                  ))}
                </div>
              ) : null}
              <div className="space-y-2">
                <Label htmlFor="username">Username</Label>
                <Input
                  id="username"
                  autoComplete="username"
                  autoCapitalize="none"
                  spellCheck={false}
                  required
                  value={username}
                  onChange={(e) => {
                    setUsername(e.target.value);
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
              <Button type="submit" className="w-full" disabled={setPasswordMutation.isPending}>
                {setPasswordMutation.isPending ? "Setting password…" : "Set password"}
              </Button>
            </form>
          ) : (
            <div className="space-y-4">
              <p role="alert" className="text-sm">
                {INVALID_LINK}
              </p>
              <Link to="/login" className="text-sm underline underline-offset-4">
                Go to sign in
              </Link>
            </div>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
