"""Output writing and structural validation of `output.csv`."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import Decision
from .planner import PHASE_ORDER, PHASE_RANK

ALLOWED_STATUS = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}
ALLOWED_OPS = {
    "FINDING": {"REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT",
                "REPORT_ACTIVE_DATA_FLOW": "ACTIVE", "REPORT_RESTORE_RISK": "ACTIVE"},
    "REMEDIATION": {"DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED", "DELETE_RECORD": "ABSENT",
                    "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED"},
    "VERIFICATION": {"VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT",
                     "VERIFY_RECORD_REDACTED": "REDACTED", "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
                     "VERIFY_DATA_FLOW_DISABLED": "DISABLED", "VERIFY_RESTORE_BLOCKED": "BLOCKED"},
}
FIELDS = ["step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"]


def steps_json(decision: Decision) -> str:
    arr = [s.as_dict(i) for i, s in enumerate(decision.steps, 1)]
    return json.dumps(arr, ensure_ascii=False, separators=(",", ":"))


def write_output(decisions: list[Decision], path: Path) -> None:
    rows = sorted(decisions, key=lambda d: d.case.case_id)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for d in rows:
            w.writerow([d.case.case_id, d.status, steps_json(d)])


def validate_output(path: Path, request_ids: list[str], valid_evidence: set[str], evidence_time: dict[str, str],
                    audit_at: dict[str, str]) -> list[str]:
    """Return a list of human-readable problems (empty list = valid)."""
    problems: list[str] = []
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.reader(f)
        header = next(rd)
        if header != ["case_id", "deletion_status", "steps_json"]:
            problems.append(f"bad header {header}")
        rows = list(rd)
    ids = [r[0] for r in rows]
    if ids != sorted(ids):
        problems.append("rows not sorted by case_id")
    if len(set(ids)) != len(ids):
        problems.append("duplicate case_id rows")
    if set(ids) != set(request_ids) or len(ids) != len(request_ids):
        problems.append(f"case set mismatch: {len(ids)} rows vs {len(request_ids)} requests")
    for r in rows:
        cid, status, sj = r
        if status not in ALLOWED_STATUS:
            problems.append(f"{cid}: bad status {status}")
        try:
            arr = json.loads(sj)
        except json.JSONDecodeError as e:
            problems.append(f"{cid}: invalid JSON ({e})")
            continue
        if not isinstance(arr, list):
            problems.append(f"{cid}: steps_json is not an array")
            continue
        if status == "NOT_APPLICABLE" and arr:
            problems.append(f"{cid}: NOT_APPLICABLE must have []")
        last = (-1, -1, "", "")
        for i, s in enumerate(arr, 1):
            if list(s.keys()) != FIELDS and set(s.keys()) != set(FIELDS):
                problems.append(f"{cid}: step {i} fields {sorted(s)}")
                continue
            if s["step_no"] != i:
                problems.append(f"{cid}: step_no {s['step_no']} != {i}")
            ph, op, st = s["phase"], s["operation"], s["state"]
            if ph not in ALLOWED_OPS or ALLOWED_OPS[ph].get(op) != st:
                problems.append(f"{cid}: step {i} invalid op/state {ph}/{op}/{st}")
                continue
            key = (PHASE_RANK[ph], PHASE_ORDER[ph].index(op), s["system_id"], s["resource_id"])
            if key < last:
                problems.append(f"{cid}: step {i} out of order")
            last = key
            ev = s["evidence_id"]
            if status == "COMPLETE" and ph != "VERIFICATION":
                problems.append(f"{cid}: COMPLETE contains {ph}")
            future = ph in ("REMEDIATION",) or (ph == "VERIFICATION" and status == "INCOMPLETE")
            if future and ev != "":
                problems.append(f"{cid}: step {i} future step must have empty evidence_id")
            if not future:
                if ev not in valid_evidence:
                    problems.append(f"{cid}: step {i} unknown evidence_id {ev!r}")
                elif ev in evidence_time and evidence_time[ev] > audit_at.get(cid, "9999") and ev.startswith("evidence_"):
                    problems.append(f"{cid}: step {i} cites {ev} captured after audit_at")
    return problems
