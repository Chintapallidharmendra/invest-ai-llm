import { describe, expect, it } from "vitest";

import { appRoutes, collectRoutes, navEntries, type AppRoute } from "@/routes";

const r = (path: string, extra: Partial<AppRoute> = {}): AppRoute => ({
  path,
  element: null,
  requiresAuth: true,
  ...extra,
});

describe("collectRoutes", () => {
  it("registers every module's route and puts the catch-all last", () => {
    const routes = collectRoutes({
      "./not-found/route.tsx": { route: r("*", { requiresAuth: false }) },
      "./home/route.tsx": { route: r("/") },
      "./demo/route.tsx": { route: r("/demo") },
    });
    expect(routes.map((x) => x.path)).toEqual(["/demo", "/", "*"]);
  });

  it("rejects a module without a route export", () => {
    expect(() => collectRoutes({ "./broken/route.tsx": {} })).toThrow(/broken\/route.tsx must export/);
  });

  it("rejects duplicate paths", () => {
    expect(() =>
      collectRoutes({ "./a/route.tsx": { route: r("/x") }, "./b/route.tsx": { route: r("/x") } }),
    ).toThrow(/declared by both/);
  });
});

describe("navEntries", () => {
  const routes = [
    r("/", { nav: { label: "Home", order: 0 } }),
    r("/audit", { nav: { label: "Audit", order: 5 }, roles: ["compliance"] }),
    r("/spaces", { nav: { label: "Spaces", order: 1 } }),
    r("/spaces/:id", { nav: { label: "Space" } }),
    r("/hidden"),
    r("*", { nav: { label: "404" } }),
  ];

  it("orders entries and skips parameterised, unlisted and catch-all routes", () => {
    expect(navEntries(routes).map((x) => x.path)).toEqual(["/", "/spaces"]);
  });

  it("shows role-restricted entries only to those roles", () => {
    expect(navEntries(routes, ["compliance"]).map((x) => x.path)).toEqual(["/", "/spaces", "/audit"]);
    expect(navEntries(routes, ["admin"]).map((x) => x.path)).toEqual(["/", "/spaces"]);
  });
});

describe("discovered routes", () => {
  it("includes the home page and the 404 page from src/routes/**/route.tsx", () => {
    const paths = appRoutes.map((x) => x.path);
    expect(paths).toContain("/");
    expect(paths.at(-1)).toBe("*");
  });
});
