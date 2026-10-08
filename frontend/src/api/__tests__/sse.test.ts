import { describe, expect, it } from "vitest";

import { hangingStream, streamOf } from "@/__tests__/fixtures";
import { readSSE, type SSEEvent } from "@/api/sse";

async function collect(chunks: readonly (string | Uint8Array)[]): Promise<SSEEvent[]> {
  const out: SSEEvent[] = [];
  for await (const ev of readSSE(new Response(streamOf(chunks)))) out.push(ev);
  return out;
}

describe("readSSE", () => {
  it("parses events split across chunks", async () => {
    const events = await collect([
      'event: meta\ndata: {"conversation_id":"c1"}\n\nev',
      'ent: delta\ndata: {"te',
      'xt":"Hel"}\n',
      "\nevent: delta\ndata: ",
      '{"text":"lo"}\n\n',
      'event: done\ndata: {"finish_reason":"stop","latency_ms":12}\n\n',
    ]);
    expect(events).toEqual([
      { event: "meta", data: { conversation_id: "c1" } },
      { event: "delta", data: { text: "Hel" } },
      { event: "delta", data: { text: "lo" } },
      { event: "done", data: { finish_reason: "stop", latency_ms: 12 } },
    ]);
  });

  it("joins multi-line data with newlines", async () => {
    const events = await collect(['event: table\ndata: {"rows":\ndata: [1,2]}\n\n', "data: line one\ndata: line two\n\n"]);
    expect(events).toEqual([
      { event: "table", data: { rows: [1, 2] } },
      { event: "message", data: "line one\nline two" },
    ]);
  });

  it("handles CRLF (also split mid-pair), CR, comments, BOM, ids and no-space values", async () => {
    const events = await collect([
      "﻿: keep-alive\r",
      "\nid: 7\r\nevent:status\r\ndata:{\"stage\":\"checking\"}\r\n\r\n",
      "event: delta\rdata: {\"text\":\"x\"}\r\r",
    ]);
    expect(events).toEqual([
      { event: "status", data: { stage: "checking" }, id: "7" },
      { event: "delta", data: { text: "x" }, id: "7" },
    ]);
  });

  it("decodes multi-byte characters split across chunks", async () => {
    const bytes = new TextEncoder().encode('data: {"text":"₹4,215.5 cr"}\n\n');
    const cut = bytes.indexOf(0xe2) + 1; // inside the 3-byte ₹
    const events = await collect([bytes.slice(0, cut), bytes.slice(cut)]);
    expect(events).toEqual([{ event: "message", data: { text: "₹4,215.5 cr" } }]);
  });

  it("drops events without data and an incomplete trailing event", async () => {
    const events = await collect(["event: ping\n\n", 'event: delta\ndata: {"text":"a"}\n\n', 'event: delta\ndata: {"text":"b"}']);
    expect(events).toEqual([{ event: "delta", data: { text: "a" } }]);
  });

  it("stops iterating and cancels the body on abort", async () => {
    const { stream, state } = hangingStream('event: delta\ndata: {"text":"a"}\n\n');
    const controller = new AbortController();
    const seen: SSEEvent[] = [];
    for await (const ev of readSSE(new Response(stream), { signal: controller.signal })) {
      seen.push(ev);
      controller.abort();
    }
    expect(seen).toHaveLength(1);
    expect(state.cancelled).toBe(true);
  });

  it("ends a pending read when aborted mid-wait", async () => {
    const { stream, state } = hangingStream("");
    const controller = new AbortController();
    const iteration = (async () => {
      const seen: SSEEvent[] = [];
      for await (const ev of readSSE(new Response(stream), { signal: controller.signal })) seen.push(ev);
      return seen;
    })();
    setTimeout(() => {
      controller.abort();
    }, 10);
    await expect(iteration).resolves.toEqual([]);
    expect(state.cancelled).toBe(true);
  });

  it("returns immediately when already aborted, and on a bodyless response", async () => {
    const controller = new AbortController();
    controller.abort();
    const seen: SSEEvent[] = [];
    for await (const ev of readSSE(new Response(streamOf(["data: 1\n\n"])), { signal: controller.signal })) seen.push(ev);
    for await (const ev of readSSE(new Response(null))) seen.push(ev);
    expect(seen).toEqual([]);
  });

  it("cancels the body when the consumer breaks out early", async () => {
    const { stream, state } = hangingStream("data: 1\n\ndata: 2\n\n");
    for await (const ev of readSSE(new Response(stream))) {
      expect(ev.data).toBe(1);
      break;
    }
    expect(state.cancelled).toBe(true);
  });
});
