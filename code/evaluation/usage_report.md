# Token usage and cost - final full-dataset run

This is the run that produced the submitted `output.csv` (`python3 code/main.py`, recorded in `code/evaluation/run_stats.json`).

## Result

**No language model was used.** The solution is a deterministic Python pipeline, so for this run:

| Measure | Value |
|---|---|
| Model providers | none |
| Model names | none |
| Model calls | 0 |
| Input tokens | 0 |
| Output tokens | 0 |
| Total tokens | 0 |
| Average tokens per request | 0 (518 requests) |
| Estimated total cost | $0.00 |
| Estimated cost per request | $0.00 |
| Per-model totals | n/a (no models) |

The zero values are measured, not estimated: `main.py` contains no API client and reads no API keys.

## What did run

| Item | Value |
|---|---|
| Requests processed | 518 (`deletion_requests.csv`) |
| Statuses produced | COMPLETE 152, INCOMPLETE 356, NOT_APPLICABLE 10 |
| Steps emitted | 3281 |
| Evidence files parsed | 94 (failures: 0) |
| Observations extracted | 2605 |
| Wall-clock time of the run | 0.42 s (single process, OCR served from cache) |
| OCR | 11 screenshots; engine Apple Vision (on-device, bundled Swift helper `ghost_audit/ocr/vision_ocr.swift`), transcripts cached in `code/cache/ocr_cache.json`; no network, no tokens |

## Why no LLM, and where one could help

The task contract (closed set of operations/states, strict ordering, exact ids) is better served by deterministic rules: every id,
timestamp and citation in the output is copied from a parsed record, so nothing can be invented. An LLM would only help for
(1) free-text intent in languages beyond the six covered, (2) reading screenshots on machines without an OCR engine, and
(3) explaining findings to a human. If added, calls would be cached by content hash, constrained to a JSON schema, validated by
`ghost_audit/output.py::validate_output`, and their provider/model/tokens/cost logged in this file; keys would come from environment variables only.
