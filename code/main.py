"""Entry point: python3 code/main.py [--dataset dataset] [--requests deletion_requests.csv] [--output output.csv]

Runs the whole deterministic pipeline (no model API calls) and writes output.csv at the
repository root, then validates it structurally.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ghost_audit.auditor import Auditor  # noqa: E402
from ghost_audit.output import validate_output, write_output  # noqa: E402


def write_run_stats(decisions, aud, elapsed: float, requests: Path, output: Path) -> None:
    """Record measured facts about this run; evaluation/usage_report.md is generated from it."""
    ocr = {fid: note for fid, note in aud.inv.report.notes.items() if note.startswith("ocr")}
    stats = {
        "requests_file": requests.name,
        "requests": len(decisions),
        "status_counts": dict(Counter(d.status for d in decisions)),
        "evidence_files": len(aud.inv.files),
        "evidence_parse_failures": aud.inv.report.failures,
        "observations": len(aud.inv.obs),
        "ocr_files": ocr,
        "elapsed_seconds": round(elapsed, 2),
        "model_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_usd": 0.0,
        "steps_total": sum(len(d.steps) for d in decisions),
        "ambiguous_account_links": sum(any("equally plausible" in n for n in d.notes) for d in decisions),
        "unmatched_account_links": sum(any("no account delete event" in n for n in d.notes) for d in decisions),
        "no_audit_record_rows": sum(d.status != "NOT_APPLICABLE" and not any(x.system_id == "security_service" for x in d.steps)
                                    for d in decisions),
    }
    (ROOT / "code" / "evaluation" / "run_stats.json").write_text(json.dumps(stats, indent=1, sort_keys=True))


def run(dataset: Path, requests: Path, output: Path, quiet: bool = False) -> tuple[list, Auditor]:
    t0 = time.time()
    aud = Auditor(dataset)
    cases = aud.load_cases(requests)
    sample = dataset / "sample_deletion_requests.csv"
    context = aud.load_cases(sample) if sample.exists() and sample.resolve() != requests.resolve() else []
    decisions = aud.audit_all(cases, context)
    write_output(decisions, output)
    write_run_stats(decisions, aud, time.time() - t0, requests, output)
    if not quiet:
        c = Counter(d.status for d in decisions)
        print(f"processed {len(decisions)} requests in {time.time()-t0:.1f}s -> {dict(c)}")
        print(f"evidence files parsed: {len(aud.inv.report.parsed)}; failures: {aud.inv.report.failures or 'none'}")
    return decisions, aud


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(ROOT / "dataset"))
    ap.add_argument("--requests", default=None, help="requests CSV (default: <dataset>/deletion_requests.csv)")
    ap.add_argument("--output", default=str(ROOT / "output.csv"))
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(message)s")
    ds = Path(a.dataset)
    req = Path(a.requests) if a.requests else ds / "deletion_requests.csv"
    out = Path(a.output)
    decisions, aud = run(ds, req, out)
    with open(req, newline="") as f:
        ids = [r["case_id"] for r in csv.DictReader(f)]
    audit_at = {d.case.case_id: d.case.audit_at for d in decisions}
    ev_ids = set(aud.inv.files)
    ev_time = {k: v.captured_at for k, v in aud.inv.files.items()}
    problems = validate_output(out, ids, ev_ids, ev_time, audit_at)
    if problems:
        print(f"VALIDATION: {len(problems)} problem(s)")
        for p in problems[:30]:
            print("  -", p)
        return 1
    print(f"VALIDATION OK: {len(ids)} rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
