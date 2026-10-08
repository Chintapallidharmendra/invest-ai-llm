/**
 * Server-sent events over a POST response (ADR-002/003/025).
 *
 * Chat turns stream from a POST, so `EventSource` can't be used. This follows the
 * WHATWG event-stream parsing rules: CRLF/LF/CR line ends, `:` comments, one optional
 * space after the colon, multi-line `data:` joined with "\n", a blank line dispatches,
 * and an event without data (or cut off by the end of the stream) is dropped.
 *
 * Each `data` is a JSON object in this API; non-JSON data is yielded as the raw string.
 * Event names: meta, status, delta, table, sources, replace, plan, job, done, error.
 */

export interface SSEEvent {
  event: string;
  data: unknown;
  id?: string;
}

export interface ReadSSEOptions {
  /** Stops iteration and cancels the body. Pass the same signal given to fetch. */
  signal?: AbortSignal;
}

function parseData(raw: string): unknown {
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return raw;
  }
}

class EventBuilder {
  private event = "";
  private data: string[] = [];
  private id: string | undefined;

  /** Feed one line; returns an event when the line is blank and one is pending. */
  line(line: string): SSEEvent | undefined {
    if (line === "") return this.dispatch();
    if (line.startsWith(":")) return undefined;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    switch (field) {
      case "event":
        this.event = value;
        break;
      case "data":
        this.data.push(value);
        break;
      case "id":
        if (!value.includes("\0")) this.id = value;
        break;
      default:
        // `retry` and unknown fields are ignored.
        break;
    }
    return undefined;
  }

  private dispatch(): SSEEvent | undefined {
    const { event, data, id } = this;
    this.event = "";
    this.data = [];
    if (data.length === 0) return undefined;
    const out: SSEEvent = { event: event || "message", data: parseData(data.join("\n")) };
    if (id !== undefined) out.id = id;
    return out;
  }
}

function isAbort(error: unknown, signal: AbortSignal | undefined): boolean {
  return signal?.aborted === true || (error as { name?: unknown } | null)?.name === "AbortError";
}

export async function* readSSE(
  response: Response,
  options: ReadSSEOptions = {},
): AsyncGenerator<SSEEvent, void, undefined> {
  const { signal } = options;
  if (!response.body) return;
  if (signal?.aborted) {
    await response.body.cancel().catch(() => undefined);
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  const builder = new EventBuilder();
  const onAbort = () => {
    reader.cancel().catch(() => undefined);
  };
  signal?.addEventListener("abort", onAbort, { once: true });

  let buffer = "";
  let first = true;
  try {
    for (;;) {
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await reader.read();
      } catch (error) {
        if (isAbort(error, signal)) return;
        throw error;
      }
      if (signal?.aborted) return;
      const done = chunk.done;
      buffer += done ? decoder.decode() : decoder.decode(chunk.value, { stream: true });
      if (first && buffer.length > 0) {
        if (buffer.startsWith("﻿")) buffer = buffer.slice(1);
        first = false;
      }

      let start = 0;
      for (let i = 0; i < buffer.length; i++) {
        const ch = buffer[i];
        if (ch !== "\n" && ch !== "\r") continue;
        // A trailing CR may be the first half of a CRLF split across chunks.
        if (ch === "\r" && i === buffer.length - 1 && !done) break;
        const event = builder.line(buffer.slice(start, i));
        if (ch === "\r" && buffer[i + 1] === "\n") i++;
        start = i + 1;
        if (event) {
          yield event;
          if (signal?.aborted) return;
        }
      }
      buffer = buffer.slice(start);
      // Whatever is left at the end of the stream is an incomplete event: dropped.
      if (done) return;
    }
  } finally {
    signal?.removeEventListener("abort", onAbort);
    reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
