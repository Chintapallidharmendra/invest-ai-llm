/** Shared test fixtures: a stream builder and a mock fetch. */

import { vi } from "vitest";

/** A ReadableStream that emits `chunks` (UTF-8 encoded) one read at a time. */
export function streamOf(chunks: readonly (string | Uint8Array)[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let i = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      const chunk = chunks[i++];
      if (chunk === undefined) {
        controller.close();
        return;
      }
      controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk);
    },
  });
}

/** A stream that emits `first`, then never ends until cancelled. Reports cancellation. */
export function hangingStream(first: string) {
  const state = { cancelled: false };
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(first));
    },
    cancel() {
      state.cancelled = true;
    },
  });
  return { stream, state };
}

export function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers as Record<string, string> | undefined) },
  });
}

export function problemResponse(status: number, body: Record<string, unknown>, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify({ status, ...body }), {
    status,
    headers: { "Content-Type": "application/problem+json", ...headers },
  });
}

/** Stub global fetch; returns the mock so tests can inspect calls. */
export function mockFetch(...responses: (Response | Error | DOMException)[]) {
  const fn = vi.fn<typeof fetch>();
  for (const r of responses) {
    if (r instanceof Response) fn.mockResolvedValueOnce(r);
    else fn.mockRejectedValueOnce(r);
  }
  vi.stubGlobal("fetch", fn);
  return fn;
}

export function requestOf(fn: ReturnType<typeof mockFetch>, call = 0) {
  const args = fn.mock.calls[call];
  if (!args) throw new Error(`fetch call ${String(call)} not made`);
  const [input, init] = args;
  const url = input instanceof Request ? input.url : input instanceof URL ? input.href : input;
  return { url, init: init ?? {}, headers: new Headers(init?.headers) };
}

/**
 * Stub `document.cookie` reads. jsdom (plain http) refuses to store `__Host-` cookies,
 * which need Secure, so tests stub the getter instead of writing real cookies.
 */
export function setCookies(...cookies: string[]) {
  vi.spyOn(document, "cookie", "get").mockReturnValue(cookies.join("; "));
}
