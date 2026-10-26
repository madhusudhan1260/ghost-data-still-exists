import csv
import json

from ghost_audit.models import Case, Decision, Step
from ghost_audit.output import validate_output, write_output
from ghost_audit.planner import sort_steps


def S(phase, sysn, rid, op, state, ev=""):
    return Step(phase, sysn, rid, op, state, ev)


def test_phase_then_operation_then_system_then_resource_order():
    steps = [
        S("VERIFICATION", "search_service", "search_2", "VERIFY_RECORD_ABSENT", "ABSENT"),
        S("REMEDIATION", "search_service", "search_2", "DELETE_RECORD", "ABSENT"),
        S("FINDING", "search_service", "search_2", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_01"),
        S("FINDING", "integration_service", "data_flow_1", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE", "evidence_01"),
        S("FINDING", "account_service", "account_9", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_01"),
        S("REMEDIATION", "integration_service", "data_flow_1", "DISABLE_DATA_FLOW", "DISABLED"),
        S("VERIFICATION", "account_service", "account_9", "VERIFY_RECORD_ABSENT", "ABSENT"),
    ]
    got = [(s.phase[:3], s.operation, s.resource_id) for s in sort_steps(steps)]
    assert got == [
        ("FIN", "REPORT_REMAINING_RECORD", "account_9"), ("FIN", "REPORT_REMAINING_RECORD", "search_2"),
        ("FIN", "REPORT_ACTIVE_DATA_FLOW", "data_flow_1"),
        ("REM", "DISABLE_DATA_FLOW", "data_flow_1"), ("REM", "DELETE_RECORD", "search_2"),
        ("VER", "VERIFY_RECORD_ABSENT", "account_9"), ("VER", "VERIFY_RECORD_ABSENT", "search_2"),
    ]


def test_duplicate_steps_are_collapsed():
    a = S("FINDING", "search_service", "search_2", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_01")
    assert len(sort_steps([a, a])) == 1


def _write(tmp_path, decisions):
    p = tmp_path / "output.csv"
    write_output(decisions, p)
    return p


def _case(cid):
    return Case(cid, "a@x.org", "t", "COMPLETE", "t", "2026-01-02T00:00:00Z", "x")


def test_writer_escapes_json_and_validator_accepts_valid_rows(tmp_path):
    d = [Decision(_case("case_2"), "NOT_APPLICABLE", []),
         Decision(_case("case_1"), "COMPLETE", [S("VERIFICATION", "account_service", 'account_"1"', "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_01")])]
    p = _write(tmp_path, d)
    rows = list(csv.reader(open(p)))
    assert rows[0] == ["case_id", "deletion_status", "steps_json"]
    assert [r[0] for r in rows[1:]] == ["case_1", "case_2"]  # sorted by case_id
    assert rows[2][2] == "[]"
    assert json.loads(rows[1][2])[0]["resource_id"] == 'account_"1"'  # quotes survive CSV + JSON escaping
    assert validate_output(p, ["case_1", "case_2"], {"evidence_01"}, {"evidence_01": "2026-01-01T00:00:00Z"},
                           {"case_1": "2026-01-02T00:00:00Z", "case_2": "2026-01-02T00:00:00Z"}) == []


def test_validator_flags_bad_state_unknown_evidence_late_evidence_and_gaps(tmp_path):
    bad = Decision(_case("case_1"), "COMPLETE", [S("VERIFICATION", "account_service", "account_1", "VERIFY_RECORD_ABSENT", "PRESENT", "evidence_01")])
    p = _write(tmp_path, [bad])
    probs = validate_output(p, ["case_1"], {"evidence_01"}, {"evidence_01": "2026-01-01T00:00:00Z"}, {"case_1": "2026-01-02T00:00:00Z"})
    assert any("invalid op/state" in x for x in probs)
    ghost = Decision(_case("case_1"), "COMPLETE", [S("VERIFICATION", "account_service", "account_1", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_77")])
    probs = validate_output(_write(tmp_path, [ghost]), ["case_1"], {"evidence_01"}, {}, {"case_1": "x"})
    assert any("unknown evidence_id" in x for x in probs)
    late = Decision(_case("case_1"), "COMPLETE", [S("VERIFICATION", "account_service", "account_1", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_01")])
    probs = validate_output(_write(tmp_path, [late]), ["case_1"], {"evidence_01"}, {"evidence_01": "2026-05-01T00:00:00Z"}, {"case_1": "2026-01-02T00:00:00Z"})
    assert any("captured after audit_at" in x for x in probs)
    probs = validate_output(_write(tmp_path, [late]), ["case_1", "case_2"], {"evidence_01"}, {}, {})
    assert any("case set mismatch" in x for x in probs)


def test_incomplete_future_steps_must_have_empty_evidence(tmp_path):
    d = Decision(_case("case_1"), "INCOMPLETE", [
        S("FINDING", "search_service", "search_1", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_01"),
        S("REMEDIATION", "search_service", "search_1", "DELETE_RECORD", "ABSENT", "evidence_01"),
    ])
    probs = validate_output(_write(tmp_path, [d]), ["case_1"], {"evidence_01"}, {}, {})
    assert any("future step must have empty evidence_id" in x for x in probs)
