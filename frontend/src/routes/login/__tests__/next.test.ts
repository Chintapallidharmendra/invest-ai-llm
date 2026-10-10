import { describe, expect, it } from "vitest";

import { safeNext } from "../next";

const ORIGIN = "https://invest-ai.example";

describe("safeNext", () => {
  it.each([
    ["/spaces/1?tab=docs#top", "/spaces/1?tab=docs#top"],
    ["/", "/"],
  ])("keeps the same-origin path %s", (next, expected) => {
    expect(safeNext(next, ORIGIN)).toBe(expected);
  });

  it.each([
    null,
    "",
    "//evil.com",
    "//evil.com/path",
    "/\\evil.com",
    "https://evil.com",
    "http://invest-ai.example/x",
    "javascript:alert(1)",
    "spaces",
    "/login",
    "/login?next=/x",
  ])("rejects %s and goes home", (next) => {
    expect(safeNext(next, ORIGIN)).toBe("/");
  });
});
