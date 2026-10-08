/**
 * TanStack Query defaults (ADR-008): server state lives only here.
 *
 * 4xx answers are final (validation, not found, auth), so they are never retried;
 * network errors and 5xx get two more tries. Data is fresh for 30 s.
 */

import { QueryClient } from "@tanstack/react-query";

import { isProblemError } from "./problem";

export const STALE_TIME_MS = 30_000;
const MAX_RETRIES = 2;

export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (isProblemError(error) && error.isClientError) return false;
  return failureCount < MAX_RETRIES;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: STALE_TIME_MS,
        retry: shouldRetry,
        refetchOnWindowFocus: false,
      },
      mutations: {
        // Mutations are not idempotent in general; retrying is the caller's call.
        retry: false,
      },
    },
  });
}

export const queryClient = createQueryClient();
