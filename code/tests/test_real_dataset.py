"""Integration test on the real public sample (skipped when dataset/ is not present)."""
import csv
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "dataset"
pytestmark = pytest.mark.skipif(not (DATASET / "sample_output.csv").exists(), reason="dataset/ not available")


@pytest.fixture(scope="module")
def run():
    from ghost_audit.auditor import Auditor
    aud = Auditor(DATASET)
    cases = aud.load_cases(DATASET / "sample_deletion_requests.csv")
    ctx = aud.load_cases(DATASET / "deletion_requests.csv")
    return aud, {d.case.case_id: d for d in aud.audit_all(cases, ctx)}


def test_every_evidence_file_is_parsed_or_reported(run):
    aud, _ = run
    assert not aud.inv.report.failures
    assert set(aud.inv.report.parsed) | set(aud.inv.report.failures) == set(aud.inv.files)


def test_sample_status_accuracy(run):
    _, decisions = run
    exp = {r["case_id"]: r for r in csv.DictReader(open(DATASET / "sample_output.csv"))}
    ok = sum(decisions[c].status == r["deletion_status"] for c, r in exp.items())
    assert ok / len(exp) >= 0.95


def test_sample_findings_and_evidence_references(run):
    _, decisions = run
    exp = {r["case_id"]: json.loads(r["steps_json"]) for r in csv.DictReader(open(DATASET / "sample_output.csv"))}
    # generic (non hand-built) sample cases: every finding must match resource, operation and cited file
    for cid in [f"case_{i}" for i in range(11, 33)]:
        want = {(s["system_id"], s["resource_id"], s["operation"], s["evidence_id"]) for s in exp[cid] if s["phase"] == "FINDING"}
        got = {(s.system_id, s.resource_id, s.operation, s.evidence_id) for s in decisions[cid].steps if s.phase == "FINDING"}
        assert want == got, cid


def test_pipeline_is_deterministic(run):
    from ghost_audit.output import steps_json
    aud, first = run
    cases = aud.load_cases(DATASET / "sample_deletion_requests.csv")
    ctx = aud.load_cases(DATASET / "deletion_requests.csv")
    second = {d.case.case_id: d for d in aud.audit_all(cases, ctx)}
    assert {c: steps_json(d) for c, d in first.items()} == {c: steps_json(d) for c, d in second.items()}
