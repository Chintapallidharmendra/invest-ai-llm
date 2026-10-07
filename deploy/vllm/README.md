# vLLM serving (Story 1.4)

Two vLLM OpenAI-compatible servers share the A10-24Q on `nbfcuatyolomlapp01`. Both are
attached **only** to the internal `inference` network, publish no ports and run
offline from read-only weights in `/data/models` (ADR-009, ADR-034).

| Service | Model (default) | Served name | GPU share | Max len | Notes |
|---|---|---|---|---|---|
| `vllm-chat` | `Qwen3-14B-AWQ` (INT4, Marlin on Ampere) | `chat` | 0.78 | 16384 | fp8 KV cache, prefix caching, 16 seqs, hermes tool parser, priority scheduling |
| `vllm-guard` | `Qwen3Guard-Gen-0.6B` | `guard` | 0.12 | 4096 | 16 seqs; starts after chat is healthy |

Fallback chat model: `Qwen3-8B-AWQ` (in the manifest). Switch by editing
`VLLM_MODEL` in `chat.env` only.

## Configuration

- `compose.inference.yml` holds image, GPU, network, mounts and health only.
- `chat.env` / `guard.env` hold the model path (`VLLM_MODEL`) and all launch flags
  (`VLLM_ARGS`). The entrypoint runs `vllm serve "$VLLM_MODEL" $VLLM_ARGS`. **Changing
  the model or limits never needs a compose edit:**
  ```sh
  vi deploy/vllm/chat.env          # e.g. VLLM_MODEL=/models/Qwen3-8B-AWQ
  docker compose -f deploy/compose.yml up -d vllm-chat
  ```
- Offline flags: `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`, plus `VLLM_NO_USAGE_STATS=1`
  and `DO_NOT_TRACK=1`, because vLLM's usage stats would otherwise try to phone home.
- vLLM doesn't log request prompts by default (`--enable-log-requests` is off). Keep it
  off (ADR-033).
- **Start order:** `vllm-guard` waits for `vllm-chat` to be healthy, so the two memory
  profiles never overlap. Neither claims more than its `--gpu-memory-utilization`
  share.
- Compile and kernel caches persist in `/data/vllm-cache/{chat,guard}`, which speeds up
  restarts.

**Machines without a GPU (laptops):** `docker compose up -d` now also starts the vLLM
services, which need the vLLM image and an NVIDIA GPU. To run the rest of the stack
locally, start only the app side (nothing depends on vLLM at start-up):
`docker compose -f deploy/compose.yml up -d caddy`.

## Image

Pinned: **`vllm/vllm-openai:v0.31.0-cu129`** (vLLM 0.31.0, released 2026-10-05).

- The default `v0.31.0` tag is built for **CUDA 13.0**, which needs driver ≥ 580. The VM
  has **driver 570 (CUDA 12.8)**, so the default tag will not start.
- The `-cu129` tag uses CUDA 12.9. It runs on a 12.8 driver through CUDA minor-version
  compatibility, and A10 (sm_86) kernels ship prebuilt. **Confirm on UAT** (bring-up
  log below).
- If `-cu129` fails on driver 570 (e.g. `CUDA driver version is insufficient` or a
  PTX JIT error), ask IT for a driver ≥ 575 vGPU build. Failing that, pin the newest
  vLLM release with a CUDA 12.8 image and re-run the smoke test. Record the outcome
  here.
- Override with `VLLM_VERSION=<tag>` in `deploy/.env`.

Offline load: as in `deploy/runbook.md` §3. Add the image to the `docker save` list on
the build machine (about 11.8 GB compressed):

```sh
docker pull vllm/vllm-openai:v0.31.0-cu129
docker save -o vllm-openai-v0.31.0-cu129.tar vllm/vllm-openai:v0.31.0-cu129
sha256sum vllm-openai-v0.31.0-cu129.tar > vllm-openai-v0.31.0-cu129.tar.sha256
```

## Model weights: one-time controlled download (outside the VM)

The VM never downloads anything. The weights are fetched once on an approved, connected
machine, at the exact revision pinned in `deploy/models/manifest.yaml`:

```sh
# On the connected machine (any OS with Python 3.10+):
python3 -m venv hfdl && . hfdl/bin/activate && pip install -U huggingface_hub
for spec in "Qwen/Qwen3-14B-AWQ 31c69efc29464b6bb0aee1398b5a7b50a99340c3" \
            "Qwen/Qwen3Guard-Gen-0.6B fada3b2f655b89601929198343c94cd2f64d93cc" \
            "Qwen/Qwen3-8B-AWQ 4da05a8edb55c6046cce958586c33b61da07bb79"; do
    set -- $spec
    hf download "$1" --revision "$2" --local-dir "transfer/${1#*/}"
done
# Optional pre-check on the connected machine:
MODELS_DIR=$PWD/precheck deploy/models/stage-models.sh transfer
tar -cf models-2026-10-07.tar -C transfer . && sha256sum models-2026-10-07.tar > models-2026-10-07.tar.sha256
```

Move the tarball over the approved channel, sending the `.sha256` separately. Then, on
the VM:

```sh
sha256sum -c models-2026-10-07.tar.sha256
mkdir -p /data/transfer/models && tar -xf models-2026-10-07.tar -C /data/transfer/models
sudo deploy/models/stage-models.sh /data/transfer/models      # verifies every file, then installs
rm -rf /data/transfer/models                                  # optional, after a successful install
```

`stage-models.sh` checks every manifest file (size + SHA-256) for **all** selected models
before installing anything. Any missing file (e.g. no `tokenizer.json`), extra
unapproved model name or mismatch aborts with nothing changed. Copies are re-verified
in a staging directory and moved into place atomically. Installed files are read-only
(`0444`). It refuses a `MODELS_DIR` on `/mnt`. To approve a new model or revision, add
a reviewed manifest entry; never edit a hash to make a transfer pass.

Total size: chat 10.0 GB, guard 1.5 GB, fallback 6.1 GB.

## Memory budget (expected)

A10-24Q: 24 GB vGPU (usable ≈ 23 GiB after vGPU and CUDA reservations).

| | chat (0.78 ≈ 18.7 GB) | guard (0.12 ≈ 2.9 GB) |
|---|---|---|
| Weights | ≈ 9.3 GiB (INT4 AWQ) | ≈ 1.1 GiB (BF16) |
| KV cache | ≈ 8 GiB fp8, ~80 KiB/token (40 layers × 8 KV heads × 128 × 2 × 1 B) ≈ 100k tokens ≈ 6 full 16k sequences | ≈ 1 GiB BF16, ~112 KiB/token ≈ 9k tokens |
| Rest | activations, CUDA graphs | activations, CUDA graphs |

The remaining 0.10 covers the two CUDA contexts. If chat fails to start with
`No available memory for the cache blocks`, lower `--max-num-seqs` or `--max-model-len`
first. Change the split only within ADR-009 and record it here.

## UAT bring-up results

> **To fill in on the VM.** Development had no GPU; everything below was verified
> without one (compose config, offline flags, staging, smoke logic against a mock
> server).

```sh
sudo deploy/models/stage-models.sh /data/transfer/models
docker compose -f deploy/compose.yml up -d vllm-chat vllm-guard
docker compose -f deploy/compose.yml ps vllm-chat vllm-guard     # both (healthy)
nvidia-smi                                                       # after warm-up
sudo deploy/vllm/smoke.sh | tee /data/vllm-smoke-$(date +%F).md
# Soak: leave idle 10 min, rerun smoke.sh, then check:
docker compose -f deploy/compose.yml logs vllm-chat vllm-guard | grep -iE 'out of memory|OOM|CUDA error' || echo "no OOM"
```

| Item | Result |
|---|---|
| Date / operator | |
| Docker / Compose / driver | / / 570.211 |
| Image works on driver 570 | |
| Time to healthy (chat / guard) | |
| `nvidia-smi` used / total after warm-up | |
| Per-process memory (chat / guard) | |
| KV cache tokens reported by chat at start-up (`GPU KV cache size`) | |
| OOM / CUDA errors over 10 min idle + smoke | |
| vGPU licensing throttling under sustained load (`nvidia-smi -q \| grep -i licen`) | |

### Smoke test results (`deploy/vllm/smoke.sh`)

| Check | Result | Detail |
|---|---|---|
| list models | | |
| chat completion | | |
| streamed completion | | |
| tool call (hermes) | | |
| structured output (json_schema) | | |
| guard classification | | |
| cache_salt + priority request | INFO | |

Findings (input to Story 1.9 and ADR-029/031):

- `cache_salt`: _HONOURED / ACCEPTED BUT NOT ISOLATING / REJECTED_. If it is not
  honoured, **disable prefix caching** (remove `--enable-prefix-caching`) rather than
  share the cache across users (ADR-031).
- `priority`: _ACCEPTED / REJECTED_. If rejected, drop `--scheduling-policy priority`
  and use the queue-depth pause in summary jobs (ADR-029).

The v0.31.0 source has a `cache_salt` field (1–1024 chars, salts the prefix-cache hash)
and a `priority` field (non-zero values are rejected unless
`--scheduling-policy priority` is set). The smoke test confirms the runtime behaviour.
