import { describe, expect, it } from "vitest";

import { ProblemError } from "@/api/problem";
import { createQueryClient, shouldRetry, STALE_TIME_MS } from "@/api/queryClient";
import { keyScope, queryKeys } from "@/api/queryKeys";

const problem = (status: number) => new ProblemError({ status, code: "x", title: "x" });

describe("query client defaults", () => {
  it("never retries 4xx, retries 5xx and network errors twice", () => {
    expect(shouldRetry(0, problem(404))).toBe(false);
    expect(shouldRetry(0, problem(422))).toBe(false);
    expect(shouldRetry(0, problem(503))).toBe(true);
    expect(shouldRetry(1, problem(0))).toBe(true);
    expect(shouldRetry(2, problem(503))).toBe(false);
    expect(shouldRetry(0, new Error("boom"))).toBe(true);
  });

  it("uses a 30 s stale time and no mutation retries", () => {
    const defaults = createQueryClient().getDefaultOptions();
    expect(defaults.queries?.staleTime).toBe(STALE_TIME_MS);
    expect(STALE_TIME_MS).toBe(30_000);
    expect(defaults.mutations?.retry).toBe(false);
  });
});

describe("query keys", () => {
  it("builds scoped keys that share the scope prefix", () => {
    const spaces = keyScope("spaces");
    expect(spaces.all).toEqual(["spaces"]);
    expect(spaces.key("detail", "s1")).toEqual(["spaces", "detail", "s1"]);
    expect(queryKeys.auth.me()).toEqual(["auth", "me"]);
    expect(queryKeys.auth.me().slice(0, 1)).toEqual(queryKeys.auth.all);
  });
});
