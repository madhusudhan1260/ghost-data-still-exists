"""State at audit_at: later evidence is ignored, latest earlier observation wins, copies merge pessimistically."""
from ghost_audit.models import Obs
from ghost_audit.timeline import reduce_states


def o(t, present, file_id="evidence_01", rid="account_1", kind="account", source="x"):
    return Obs(file_id, t, "account_service", rid, kind, {"u@x.org"}, set(), {"present": present}, source)


def test_latest_observation_before_audit_wins():
    s = reduce_states([o("2026-01-01T00:00:00Z", True), o("2026-01-02T00:00:00Z", False)], "2026-01-03T00:00:00Z")
    assert s[("account_service", "account_1", "account")].state["present"] is False


def test_recreation_is_reported_when_it_happens_before_audit():
    s = reduce_states([o("2026-01-01T00:00:00Z", False), o("2026-01-02T00:00:00Z", True)], "2026-01-03T00:00:00Z")
    assert s[("account_service", "account_1", "account")].state["present"] is True


def test_evidence_after_audit_is_ignored():
    s = reduce_states([o("2026-01-01T00:00:00Z", False), o("2026-02-01T00:00:00Z", True)], "2026-01-03T00:00:00Z")
    assert s[("account_service", "account_1", "account")].state["present"] is False


def test_replica_drift_is_pessimistic():
    a = o("2026-01-01T00:00:00Z", False, source="sql:primary")
    b = o("2026-01-01T00:00:00Z", True, source="sql:replica")
    s = reduce_states([a, b], "2026-01-03T00:00:00Z")
    assert s[("account_service", "account_1", "account")].state["present"] is True


def test_citation_prefers_a_file_captured_before_audit():
    early = o("2026-01-01T00:00:00Z", False, file_id="evidence_02")
    late = o("2026-01-02T00:00:00Z", False, file_id="evidence_01")
    ft = {"evidence_01": "2026-01-09T00:00:00Z", "evidence_02": "2026-01-01T00:00:00Z"}
    s = reduce_states([early, late], "2026-01-03T00:00:00Z", file_time=ft)
    assert s[("account_service", "account_1", "account")].evidence_id == "evidence_02"
