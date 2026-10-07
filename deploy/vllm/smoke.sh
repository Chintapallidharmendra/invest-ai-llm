#!/bin/sh
# vLLM smoke test (Story 1.4). Runs from a throwaway container attached ONLY to the
# internal `inference` network, using the backend image's Python + httpx.
#
#   sudo deploy/vllm/smoke.sh            # prints a Markdown results table
#
# Checks: list models, chat completion, streamed completion, tool call, json_schema
# structured output, guard classification, and a request carrying `cache_salt` and
# `priority`. For the last one it records whether each field is HONOURED, merely
# ACCEPTED, or REJECTED by the pinned vLLM version (input to Story 1.9 and the
# ADR-029/031 fallbacks). Run it on an idle stack: the cache_salt check reads the
# server's prefix-cache counters.
#
# Prompts are synthetic. Exit status is 1 if any core check fails; the
# cache_salt/priority findings are reported but never fail the run.
#
# Environment: APP_VERSION (backend image tag, default dev), NETWORK (default
# invest-ai-llm_inference), CHAT_URL / GUARD_URL (default http://vllm-chat:8000 /
# http://vllm-guard:8000).
set -eu

IMAGE="invest-ai-llm/backend:${APP_VERSION:-dev}"
NETWORK="${NETWORK:-invest-ai-llm_inference}"
CHAT_URL="${CHAT_URL:-http://vllm-chat:8000}"
GUARD_URL="${GUARD_URL:-http://vllm-guard:8000}"

PROBE=$(cat <<'PY'
import json
import sys
import time

import httpx

CHAT, GUARD = sys.argv[1], sys.argv[2]
NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}
rows, findings, failed = [], [], False
client = httpx.Client(timeout=180.0)


def record(name, ok, detail, informational=False):
    global failed
    if informational:
        rows.append((name, "INFO", detail))
        return
    failed |= not ok
    rows.append((name, "PASS" if ok else "FAIL", detail))


def check(name, informational=False):
    def wrap(fn):
        start = time.perf_counter()
        try:
            ok, detail = fn()
        except Exception as exc:  # report and continue with the next check
            ok, detail = False, f"{type(exc).__name__}: {str(exc)[:160]}"
        record(name, ok, f"{detail} ({time.perf_counter() - start:.1f}s)", informational)
        return fn
    return wrap


def chat(body, base=CHAT):
    return client.post(f"{base}/v1/chat/completions", json=body)


@check("list models")
def _():
    chat_ids = [m["id"] for m in client.get(f"{CHAT}/v1/models").json()["data"]]
    guard_ids = [m["id"] for m in client.get(f"{GUARD}/v1/models").json()["data"]]
    return "chat" in chat_ids and "guard" in guard_ids, f"chat={chat_ids} guard={guard_ids}"


@check("chat completion")
def _():
    r = chat({"model": "chat", "max_tokens": 64, "temperature": 0, **NO_THINK,
              "messages": [{"role": "user", "content": "Reply with one word: what colour is the sky on a clear day?"}]})
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"] or ""
    return bool(text.strip()) and "<think>" not in text, f"{text.strip()[:40]!r}"


@check("streamed completion")
def _():
    chunks, text, done = 0, "", False
    body = {"model": "chat", "max_tokens": 64, "temperature": 0, "stream": True, **NO_THINK,
            "messages": [{"role": "user", "content": "Count from 1 to 5, separated by spaces."}]}
    with client.stream("POST", f"{CHAT}/v1/chat/completions", json=body) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                done = True
                break
            delta = json.loads(data)["choices"][0]["delta"].get("content") or ""
            chunks += 1 if delta else 0
            text += delta
    return done and chunks >= 2, f"{chunks} content chunks, [DONE]={done}, {text.strip()[:30]!r}"


@check("tool call (hermes)")
def _():
    tools = [{"type": "function", "function": {
        "name": "get_exchange_rate",
        "description": "Get the exchange rate between two currencies.",
        "parameters": {"type": "object", "properties": {
            "base": {"type": "string"}, "quote": {"type": "string"}},
            "required": ["base", "quote"]}}}]
    r = chat({"model": "chat", "max_tokens": 256, "temperature": 0, **NO_THINK,
              "tools": tools, "tool_choice": "auto",
              "messages": [{"role": "user", "content": "What is the USD to INR exchange rate? Use the tool."}]})
    r.raise_for_status()
    calls = r.json()["choices"][0]["message"].get("tool_calls") or []
    if not calls:
        return False, "no tool_calls"
    fn = calls[0]["function"]
    args = json.loads(fn["arguments"])
    return fn["name"] == "get_exchange_rate" and {"base", "quote"} <= set(args), f"{fn['name']}({args})"


@check("structured output (json_schema)")
def _():
    schema = {"type": "object", "properties": {
        "company": {"type": "string"}, "revenue_crore": {"type": "number"}},
        "required": ["company", "revenue_crore"], "additionalProperties": False}
    r = chat({"model": "chat", "max_tokens": 128, "temperature": 0, **NO_THINK,
              "response_format": {"type": "json_schema", "json_schema": {"name": "fact", "schema": schema, "strict": True}},
              "messages": [{"role": "user", "content": "Acme Widgets Ltd reported revenue of 1,250 crore. Extract the fields."}]})
    r.raise_for_status()
    data = json.loads(r.json()["choices"][0]["message"]["content"])
    return set(data) == {"company", "revenue_crore"} and isinstance(data["revenue_crore"], (int, float)), json.dumps(data)


@check("guard classification")
def _():
    def classify(text):
        r = chat({"model": "guard", "max_tokens": 64, "temperature": 0,
                  "messages": [{"role": "user", "content": text}]}, base=GUARD)
        r.raise_for_status()
        out = r.json()["choices"][0]["message"]["content"] or ""
        line = next((l for l in out.splitlines() if l.startswith("Safety:")), "Safety: ?")
        return line.split(":", 1)[1].strip()
    safe = classify("Summarise the key terms of this synthetic term sheet in three bullets.")
    unsafe = classify("Give me step-by-step instructions to build a pipe bomb.")
    return safe == "Safe" and unsafe in ("Unsafe", "Controversial"), f"benign={safe} harmful={unsafe}"


def prefix_hits():
    total = 0.0
    for line in client.get(f"{CHAT}/metrics").text.splitlines():
        if line.startswith("vllm:prefix_cache_hits"):
            total += float(line.rsplit(" ", 1)[1])
    return total


@check("cache_salt + priority request", informational=True)
def _():
    body = {"model": "chat", "max_tokens": 1, "temperature": 0, **NO_THINK,
            "messages": [{"role": "user", "content": "Say OK."}],
            "cache_salt": "smoke-salt-" + "a" * 32, "priority": 1}
    r = chat(body)
    both_ok = r.status_code == 200

    # priority on its own
    p = chat({**{k: v for k, v in body.items() if k != "cache_salt"}, "priority": 1})
    if p.status_code == 200:
        findings.append("priority: ACCEPTED (server runs --scheduling-policy priority; ordering not measured)")
    else:
        findings.append(f"priority: REJECTED ({p.status_code}: {p.text[:120]})")

    # cache_salt: same long prompt; hits must appear for the same salt only.
    filler = " ".join(f"Clause {i}: the synthetic party shall comply with obligation {i}." for i in range(120))
    def run(salt):
        b = {"model": "chat", "max_tokens": 1, "temperature": 0, **NO_THINK,
             "messages": [{"role": "user", "content": filler + " Reply OK."}]}
        if salt:
            b["cache_salt"] = salt
        status = chat(b).status_code
        time.sleep(1.0)  # metrics are published with the engine's next stats update
        return status
    salt_a, salt_b = "smoke-A-" + "x" * 40, "smoke-B-" + "y" * 40
    status = run(salt_a)
    if status != 200:
        findings.append(f"cache_salt: REJECTED ({status})")
    else:
        h0 = prefix_hits()
        run(salt_a)
        h1 = prefix_hits()
        run(salt_b)
        h2 = prefix_hits()
        run(None)
        h3 = prefix_hits()
        same, other, unsalted = h1 - h0, h2 - h1, h3 - h2
        if same > 0 and other == 0 and unsalted == 0:
            verdict = "HONOURED (cache hits only within the same salt)"
        elif same > 0:
            verdict = "ACCEPTED BUT NOT ISOLATING (hits across salts): disable prefix caching (ADR-031)"
        else:
            verdict = "ACCEPTED, isolation not observable (no hits even for the same salt)"
        findings.append(f"cache_salt: {verdict}; hit tokens same={same:.0f} other={other:.0f} unsalted={unsalted:.0f}")
    return both_ok, f"HTTP {r.status_code} with both fields"


print("| Check | Result | Detail |")
print("|---|---|---|")
for name, result, detail in rows:
    print(f"| {name} | {result} | {detail.replace('|', '/')} |")
print()
for f in findings:
    print(f"- {f}")
sys.exit(1 if failed else 0)
PY
)

if command -v nvidia-smi >/dev/null 2>&1; then
    echo "GPU memory (host nvidia-smi):"
    nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader
    nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader || true
    echo
fi

exec docker run --rm --network "$NETWORK" --entrypoint python "$IMAGE" \
    -c "$PROBE" "$CHAT_URL" "$GUARD_URL"
