import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type SyntheticEvent } from "react";
import { Navigate, useLocation, useNavigate, useSearchParams } from "react-router";

import { api } from "@/api/client";
import { isProblemError } from "@/api/problem";
import { queryKeys } from "@/api/queryKeys";
import type { components } from "@/api/schema";
import { useAuth } from "@/auth/RequireAuth";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { safeNext } from "./next";

type LoginRequest = components["schemas"]["LoginRequest"];
type Me = components["schemas"]["Me"];

export const GENERIC_ERROR = "Invalid username or password.";

function errorMessage(error: unknown): string {
  if (isProblemError(error) && error.status === 429) {
    const seconds = Number(error.extensions.retry_after_s);
    const minutes = Number.isFinite(seconds) && seconds > 0 ? Math.ceil(seconds / 60) : 1;
    return `Too many sign-in attempts. Try again in ${String(minutes)} minute${minutes === 1 ? "" : "s"}.`;
  }
  // One message for every other failure: nothing about why it failed.
  return GENERIC_ERROR;
}

export function LoginPage() {
  const auth = useAuth();
  const [params] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const next = safeNext(params.get("next"));
  const notice = (location.state as { notice?: string } | null)?.notice;

  const login = useMutation({
    mutationFn: (body: LoginRequest) => api.post<Me>("/auth/login", body, { redirectOn401: false }),
    onSuccess: (me) => {
      queryClient.setQueryData(queryKeys.auth.me(), me);
      void navigate(next, { replace: true });
    },
    onError: () => {
      setPassword("");
    },
  });

  if (auth.status === "authenticated" && !login.isPending) return <Navigate to={next} replace />;

  function onSubmit(event: SyntheticEvent) {
    event.preventDefault();
    login.mutate({ username, password });
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-muted/30 p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <h1 className="text-xl font-semibold">Sign in</h1>
          <CardDescription>invest-ai-llm</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={onSubmit} noValidate>
            {notice ? (
              <p role="status" className="rounded-md bg-muted p-3 text-sm">
                {notice}
              </p>
            ) : null}
            {login.isError ? (
              <p role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
                {errorMessage(login.error)}
              </p>
            ) : null}
            <div className="space-y-2">
              <Label htmlFor="username">Username</Label>
              <Input
                id="username"
                name="username"
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
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => {
                  setPassword(e.target.value);
                }}
              />
            </div>
            <Button type="submit" className="w-full" disabled={login.isPending}>
              {login.isPending ? "Signing in…" : "Sign in"}
            </Button>
            <p className="text-center text-sm text-muted-foreground">
              Forgot password? Contact your administrator.
            </p>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
