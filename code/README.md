# Ghost Data Still Exists - deletion audit agent

A deterministic, evidence-driven audit agent. For every request in `dataset/deletion_requests.csv` it decides whether the
personal data was **really** deleted at `audit_at`, and writes `output.csv` (`case_id,deletion_status,steps_json`) with ordered
findings, remediation and verification steps. Reported statuses (`reported_status`, `reported_result`) are treated as claims;
only parsed evidence establishes state. **No LLM or network call is used**; the run is reproducible and free
(see `evaluation/usage_report.md`).

## Run

Requires Python 3.10+ (developed on 3.14). From the repository root:

```bash
pip install -r code/requirements.txt          # pdfplumber (+pypdf fallback), pytest
python3 code/main.py                          # reads dataset/, writes ./output.csv, validates it
python3 -m pytest code/tests -q               # unit + synthetic end-to-end tests
python3 code/evaluation/main.py               # scores the public sample, regenerates the two reports
```

`python3 code/main.py --help` shows `--dataset`, `--requests`, `--output`, `--verbose`. The run finishes in about a second and
prints the status counts plus `VALIDATION OK` (header, 518 rows sorted by case_id, allowed operation/state pairs, consecutive
`step_no`, phase and within-phase order, evidence ids that exist and are not captured after `audit_at`, empty `evidence_id` for future steps).

**Screenshots (11 PNG files)** are read by OCR. Transcripts are cached in `code/cache/ocr_cache.json` (keyed by file hash), so the run needs
no OCR engine. A cache miss falls back to Apple Vision (macOS, `code/ghost_audit/ocr/vision_ocr.swift`) and then `pytesseract`; if none is
available the file is reported in `evidence_parse_failures` instead of being skipped silently.

## Pipeline

| Module (`code/ghost_audit/`) | Role |
|---|---|
| `inventory.py`, `parsers.py`, `docs.py`, `ocr/` | Read **every** file in `evidence_files.csv` once and normalize it to `Obs` records (resource, kind, identifiers, state, time, source file). SQL snapshots are executed with sqlite3 (primary + regional/read/reporting/failover replicas kept as separate copies); JSON bundles, read-back exports and ledgers; six log dialects joined by `trace_id`; handover text tables; PDF text; screenshot OCR. Documents are bound to users through the data catalog (`LINK_RESOURCE_IDENTITY`) or a single-resource + own-identifier fallback. |
| `identity.py` | Request e-mail -> stable `user_id`. Current and historical addresses (`user_audit_log.csv`), temporal ownership so a recycled address is never merged into two users, every historical e-mail, SHA-256 hash and phone number is an identifier. Names are never used (collisions). |
| `timeline.py` | State **as of `audit_at`**: observations after `audit_at` are ignored, the latest earlier observation wins (so recreation by a sync is seen, post-audit recreation is not), replica copies at the same time merge pessimistically (replication drift is a violation). Citations must be files captured at or before `audit_at`. |
| `policy.py` | Reads `deletion_rules.csv` and maps record kinds to categories: DELETE / REDACT / ANONYMIZE / RETAIN. Backups are retained but must have restore blocked; tombstones keep the stable `user_id` but lose name/e-mail/phone/hash; audit records and legal holds must stay present; an enabled connector is always an active data flow. |
| `chain.py`, `assign.py`, `auditor.py` | Accounts that are already deleted (404) and anonymized analytics profiles carry no identifier. They are linked through `system_activity.csv`: time window before `reported_at`, `reported_result` mirrors `reported_status`, and a **phase model learned from identity-anchored cases** (second-of-minute offsets of the worker chain). A global Hungarian assignment prevents one case stealing another's account. Activity results are used for linking only, never as proof of state. |
| `intent.py` | Does `request_text` authorize deletion? Multilingual (en/es/pt/fr/ja/ko) with explicit negations -> `NOT_APPLICABLE` (`steps_json` = `[]`). |
| `planner.py`, `output.py` | Findings -> remediation -> verification, within phase by the operation order of the spec, then `system_id`, `resource_id`; JSON written with `csv`/`json` writers; validator described above. |

Status rule: `COMPLETE` only when every relevant copy is compliant (verification steps only, each citing evidence);
`INCOMPLETE` when any record, data flow or restore path violates policy (findings + full remediation + post-remediation verification with `evidence_id` = `""`);
`NOT_APPLICABLE` when the text does not ask for deletion.

## Layout

```
code/main.py                  entry point
code/ghost_audit/             the package described above
code/tests/                   pytest suite + synthetic scenario builder (fictional data, labelled as such)
code/evaluation/main.py       scoring harness -> evaluation_report.md, usage_report.md (run_stats.json written by main.py)
code/cache/ocr_cache.json     OCR transcripts of the 11 screenshots
code/requirements.txt
```

## Notes and limits

* No secrets are used or stored. If an LLM is ever added, read keys from environment variables and log usage in `evaluation/usage_report.md`.
* Ids that are not present anywhere in the participant-visible data cannot be emitted (e.g. some audit-record ids of INCOMPLETE cases); see
  `evaluation/evaluation_report.md` section 7 for the measured limits.
* Predictions never use anything outside `dataset/`; no case ids, e-mails or resource ids are hard-coded (the only constants are the
  policy vocabulary from the specification, the intent lexicon, and tolerances of the phase model).
