import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, mockFetch, problemResponse, requestOf, setCookies } from "@/__tests__/fixtures";
import { api, apiFetch, apiUrl, CSRF_HEADER, readCookie, setUnauthorizedHandler } from "@/api/client";
import { ProblemError } from "@/api/problem";

let onUnauthorized: ReturnType<typeof vi.fn<() => void>>;
let restore: () => void;

beforeEach(() => {
  onUnauthorized = vi.fn<() => void>();
  restore = setUnauthorizedHandler(onUnauthorized);
  setCookies();
});

afterEach(() => {
  setUnauthorizedHandler(restore);
});

describe("apiUrl", () => {
  it("prefixes the API base and encodes query values", () => {
    expect(apiUrl("/spaces")).toBe("/api/v1/spaces");
    expect(apiUrl("spaces")).toBe("/api/v1/spaces");
    expect(apiUrl("/api/v1/spaces")).toBe("/api/v1/spaces");
    expect(apiUrl("/spaces", { q: "a b", page: 2, skip: undefined, tag: ["x", "y"] })).toBe(
      "/api/v1/spaces?q=a+b&page=2&tag=x&tag=y",
    );
  });
});

describe("cookies and CSRF", () => {
  it("reads the CSRF cookie", () => {
    setCookies("other=1", "__Host-csrf=tok%2Fen");
    expect(readCookie("__Host-csrf")).toBe("tok/en");
    expect(readCookie("missing")).toBeUndefined();
  });

  it("adds X-CSRF-Token on POST and always sends credentials", async () => {
    setCookies("__Host-csrf=abc123");
    const fetchMock = mockFetch(jsonResponse({ id: "1" }, { status: 201 }));

    await expect(api.post("/spaces", { name: "Deal A" })).resolves.toEqual({ id: "1" });

    const { url, init, headers } = requestOf(fetchMock);
    expect(url).toBe("/api/v1/spaces");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(headers.get(CSRF_HEADER)).toBe("abc123");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(init.body).toBe(JSON.stringify({ name: "Deal A" }));
  });

  it.each(["PUT", "PATCH", "DELETE"])("adds the CSRF header on %s", async (method) => {
    setCookies("__Host-csrf=abc123");
    const fetchMock = mockFetch(new Response(null, { status: 204 }));
    await apiFetch("/x", { method });
    expect(requestOf(fetchMock).headers.get(CSRF_HEADER)).toBe("abc123");
  });

  it("does not add the CSRF header on GET", async () => {
    setCookies("__Host-csrf=abc123");
    const fetchMock = mockFetch(jsonResponse({ ok: true }));
    await api.get("/auth/me");
    const { init, headers } = requestOf(fetchMock);
    expect(headers.has(CSRF_HEADER)).toBe(false);
    expect(init.credentials).toBe("include");
  });

  it("proceeds without the header when the cookie is missing (the server rejects it)", async () => {
    const fetchMock = mockFetch(problemResponse(403, { code: "csrf_failed", title: "CSRF check failed" }));
    const error = await api.post("/spaces", {}).catch((e: unknown) => e);
    expect(requestOf(fetchMock).headers.has(CSRF_HEADER)).toBe(false);
    expect(error).toBeInstanceOf(ProblemError);
    expect((error as ProblemError).code).toBe("csrf_failed");
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("passes FormData through without a JSON content type", async () => {
    const fetchMock = mockFetch(jsonResponse({}));
    const form = new FormData();
    form.append("file", new Blob(["x"]), "a.pdf");
    await api.post("/uploads", form);
    const { init, headers } = requestOf(fetchMock);
    expect(init.body).toBe(form);
    expect(headers.has("Content-Type")).toBe(false);
  });
});

describe("problem+json", () => {
  it("parses a 422 into a ProblemError with code, correlation id and extensions", async () => {
    mockFetch(
      problemResponse(422, {
        type: "urn:invest-ai-llm:problem:validation_error",
        title: "Validation failed",
        code: "validation_error",
        detail: "name is required",
        correlation_id: "0192-abc",
        errors: [{ loc: ["body", "name"], msg: "required" }],
      }),
    );
    const error = await api.post("/spaces", {}).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ProblemError);
    const problem = error as ProblemError;
    expect(problem.status).toBe(422);
    expect(problem.code).toBe("validation_error");
    expect(problem.title).toBe("Validation failed");
    expect(problem.detail).toBe("name is required");
    expect(problem.correlationId).toBe("0192-abc");
    expect(problem.extensions).toEqual({ errors: [{ loc: ["body", "name"], msg: "required" }] });
    expect(problem.isClientError).toBe(true);
  });

  it("falls back to the correlation header and a generic problem for non-problem bodies", async () => {
    mockFetch(
      new Response("<html>Bad gateway</html>", {
        status: 502,
        headers: { "Content-Type": "text/html", "X-Correlation-ID": "corr-1" },
      }),
    );
    const problem = (await api.get("/x").catch((e: unknown) => e)) as ProblemError;
    expect(problem).toBeInstanceOf(ProblemError);
    expect(problem.status).toBe(502);
    expect(problem.code).toBe("http_502");
    expect(problem.correlationId).toBe("corr-1");
  });

  it("maps a network failure to status 0", async () => {
    mockFetch(new TypeError("Failed to fetch"));
    const problem = (await api.get("/x").catch((e: unknown) => e)) as ProblemError;
    expect(problem).toBeInstanceOf(ProblemError);
    expect(problem.status).toBe(0);
    expect(problem.code).toBe("network_error");
  });

  it("rethrows aborts unchanged", async () => {
    mockFetch(new DOMException("aborted", "AbortError"));
    await expect(api.get("/x")).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("401", () => {
  it("triggers the login redirect and still throws", async () => {
    mockFetch(problemResponse(401, { code: "auth_required", title: "Sign in required" }));
    const problem = (await api.get("/auth/me").catch((e: unknown) => e)) as ProblemError;
    expect(problem.code).toBe("auth_required");
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("can be opted out of (login form)", async () => {
    mockFetch(problemResponse(401, { code: "invalid_credentials", title: "Wrong username or password" }));
    await expect(api.post("/auth/login", {}, { redirectOn401: false })).rejects.toBeInstanceOf(ProblemError);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("the default handler sends the browser to /login with the current path", async () => {
    setUnauthorizedHandler(restore);
    const assign = vi.fn();
    vi.stubGlobal("location", { pathname: "/spaces/1", search: "?t=2", hash: "", assign });
    mockFetch(problemResponse(401, { code: "auth_required", title: "Sign in required" }));
    await api.get("/x").catch(() => undefined);
    expect(assign).toHaveBeenCalledWith("/login?next=%2Fspaces%2F1%3Ft%3D2");
  });
});

describe("apiJson", () => {
  it("returns undefined for 204", async () => {
    mockFetch(new Response(null, { status: 204 }));
    await expect(api.delete("/spaces/1")).resolves.toBeUndefined();
  });

  it("raises invalid_response on a malformed JSON body", async () => {
    mockFetch(new Response("{not json", { status: 200, headers: { "Content-Type": "application/json" } }));
    await expect(api.get("/x")).rejects.toMatchObject({ code: "invalid_response" });
  });
});
