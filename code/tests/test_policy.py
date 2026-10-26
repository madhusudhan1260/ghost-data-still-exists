"""Policy engine reads deletion_rules.csv; each category maps to the required treatment."""
from policy_fixture import engine


def ev(kind, **state):
    return engine().evaluate(kind, state, "2026-01-01T00:00:00Z")


def test_delete_categories():
    for kind in ("account", "search", "export", "support_attachment", "marketing", "secondary_identifier"):
        bad = ev(kind, present=True)
        assert not bad.compliant and bad.remediation[0] == "DELETE_RECORD" and bad.finding == "REPORT_REMAINING_RECORD"
        ok = ev(kind, present=False)
        assert ok.compliant and ok.verification[0] == "VERIFY_RECORD_ABSENT"


def test_support_message_is_redacted_not_deleted():
    bad = ev("support_message", exposed=True)
    assert bad.finding == "REPORT_EXPOSED_PII" and bad.remediation == ("REDACT_RECORD", "REDACTED")
    assert ev("support_message", exposed=False).verification == ("VERIFY_RECORD_REDACTED", "REDACTED")


def test_analytics_is_anonymized():
    assert ev("analytics", attributable=True).remediation == ("ANONYMIZE_RECORD", "ANONYMIZED")
    assert ev("analytics", attributable=False).verification == ("VERIFY_RECORD_ANONYMIZED", "ANONYMIZED")


def test_tombstone_keeps_stable_reference_but_redacts_direct_identifiers():
    bad = ev("tombstone", exposed=True)
    assert bad.remediation[0] == "REDACT_RECORD"  # never DELETE_RECORD: the reference must stay
    assert ev("tombstone", exposed=False).verification[0] == "VERIFY_RECORD_REDACTED"


def test_retained_records_are_verified_present_never_deleted():
    for kind in ("audit", "legal_hold"):
        t = ev(kind, present=True)
        assert t.compliant and t.verification == ("VERIFY_RECORD_PRESENT", "PRESENT") and t.remediation is None


def test_backup_is_retained_but_restore_must_be_blocked():
    assert ev("backup", restore_allowed=True).remediation == ("BLOCK_RESTORE", "BLOCKED")
    assert ev("backup", restore_allowed=False).verification == ("VERIFY_RESTORE_BLOCKED", "BLOCKED")


def test_active_data_flow_is_a_violation():
    assert ev("data_flow", enabled=True).finding == "REPORT_ACTIVE_DATA_FLOW"
    assert ev("data_flow", enabled=False).compliant
