# Model selection spike (Story 1.9)

Measures the shortlisted chat models on the UAT A10 against our own tasks and load, so
the model choice and the † NFR thresholds rest on evidence. **UAT host only, synthetic
corpus only, no egress.** Nothing here touches production data.

| Candidate | Manifest entry | Notes |
|---|---|---|
| Qwen3-14B-AWQ (primary) | `Qwen3-14B-AWQ` | hermes tool parser; thinking off via the profile |
| Qwen3-8B-AWQ (fallback) | `Qwen3-8B-AWQ` | same flags |
| Qwen2.5-14B-Instruct INT4 | `Qwen2.5-14B-Instruct-AWQ` | hermes tool parser; no thinking mode (`chat_template_kwargs` is ignored) |
| gpt-oss-20b (if it starts) | `gpt-oss-20b` | MXFP4 weights; needs the `openai` tool parser, not hermes. Record a start failure on Ampere as the result. |

## Procedure (per candidate)

1. **Stage** from approved media (checksums verified): `sudo deploy/models/stage-models.sh /media/transfer <name>`.
2. **Point vllm-chat at it**: in `deploy/vllm/chat.env` set `VLLM_MODEL=/models/<name>`
   (keep `--gpu-memory-utilization 0.78`, the guard stays co-resident at 0.12), then
   `docker compose -f deploy/compose.yml up -d vllm-chat` and wait for `/health`.
   Record the result, inside a container on the `inference` network:

   ```sh
   R=evals/spike/results.json; C=<name>; T=http://vllm-chat:8000/v1
   P="uv run --project backend python -m evals.spike"
   $P start --results $R --candidate $C --quant AWQ-INT4            # or --failed "<error>"
   ```

3. **Quality** (AC #2): run the eval set in model mode, then merge its summary:

   ```sh
   uv run --project backend python -m evals.runner run --mode model --target $T --model chat --no-gates
   $P quality --results $R --candidate $C --eval-results evals/reports/<timestamp>/results.json
   $P tool-calls --results $R --candidate $C --target $T     # 35 finance prompts, ADR-026 tools
   ```

4. **Performance** (AC #3):

   ```sh
   $P load      --results $R --candidate $C --target $T --levels 1,5,10
   $P alongside --results $R --candidate $C --target $T --chats 10 --summaries 2
   $P soak      --results $R --candidate $C --target $T --minutes 30   # 10 chats + 2 summary loops
   ```

   The soak samples `nvidia-smi` (memory, SM clock vs max for vGPU throttling,
   temperature drift) and vLLM `/metrics` (KV usage, preemptions). An OOM or crash part
   way shows up as errors / OOM signs: record it, don't retry silently.

5. **Platform features** (AC #4): `$P features --results $R --candidate $C --target $T`
   (cache_salt isolation, priority scheduling, thinking off, hermes tool parsing).

## Report and decision (AC #5)

```sh
$P report --results $R --out evals/spike/REPORT.md --host uat-a10 --gpu "A10-24Q" \
    --date $(date +%F) --vllm-image <pinned tag> --git-commit $(git rev-parse --short HEAD) \
    --guard Qwen3Guard-Gen-0.6B --gpu-split "0.78 / 0.12"
```

Commit `REPORT.md` and `results.json` (summaries only; per-case transcripts stay in the
git-ignored `evals/reports/`). Then, by hand:

- a dated `bmad-output/decision-log.md` entry: model, quantisation, vLLM tag, context
  length, max sequences, and the † thresholds confirmed or proposed for change;
- `deploy/vllm/chat.env` and `backend/app/llm/profiles.yaml` set to the choice
  (`supports_cache_salt` / `supports_priority` from the feature probes).

Switching models must stay configuration only (NFR-015): a code change the spike seems
to need is a finding for the report, not a fix to make here.

## Tests

`uv run --project backend pytest evals/spike` checks the scorer (wrong tool, wrong
arguments, malformed JSON, no call, unparsed hermes output), the timing maths against
synthetic distributions, the probes' decisions on fake replies, the CLI and the report
rendering from `tests/sample_results.json` (illustrative numbers, not measurements).
