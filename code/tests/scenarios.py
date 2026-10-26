"""SYNTHETIC end-to-end scenarios (fictional data) with their expected answers.

Used by pytest and by evaluation/main.py. Each scenario writes a tiny dataset that follows
the real participant-facing schemas, runs the production pipeline on it and compares the
result with the hand-derived expectation. These are team-made tests, not dataset evidence.
"""
from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ghost_audit.auditor import Auditor  # noqa: E402
from synth import Synth  # noqa: E402



def manual_event(act_id, when, etype, target_sys, rid, result="SUCCESS"):
    return [act_id, when, etype, "deletion_worker", target_sys, rid, result,
            f"Deletion worker reported the requested operation as completed. Target resource: {rid}."]


@dataclass
class Scenario:
    name: str
    purpose: str
    build: callable
    expect: dict  # case_id -> (status, [(phase, system, resource, operation, state, evidence_id)])
    notes: str = ""


def _s1(s: Synth):
    s.user("user_901", "Lena Voss", "lena.voss@example.org", "+49 151 1000 9001")
    s.request("syn_01", "lena.voss@example.org", "2026-03-08T10:00:00Z", "COMPLETE", "2026-03-09T12:00:00Z", "2026-03-10T12:00:00Z")
    s.activity.append(manual_event("activity_901", "2026-03-09T11:58:00Z", "DELETE_RECORD", "account_service", "account_9001"))
    s.activity.append(manual_event("activity_902", "2026-03-09T11:52:00Z", "ANONYMIZE_RECORD", "analytics_platform", "analytics_9002"))
    obs = "2026-03-10T11:00:00Z"
    s.readback_json("2026-03-10T11:30:00Z", {
        "account_service": {"profile_lookups": [{"observed_at": obs, "profile": None, "resource_id": "account_9001", "status_code": 404}]},
        "analytics_platform": {"profiles": [{"audience_cohort": "general_shopper", "customer_reference": None, "email": None,
                                             "email_hash": None, "observed_at": obs, "phone": None, "resource_id": "analytics_9002"}]},
        "security_service": {"audit_records": [{"event_code": "ACCOUNT_DELETION_RECORDED", "observed_at": obs, "resource_id": "audit_record_9003",
                                                "retention_until": "2031-12-31T23:59:59Z", "subject_reference": "user_901"}]},
        "support_platform": {"tickets": [{"customer_reference": "user_901", "message_excerpt": "Customer followed up about an order update.",
                                          "observed_at": obs, "requester_email": None, "requester_name": None, "requester_phone": None,
                                          "resource_id": "support_9004"}]},
        "integration_service": {"connectors": [{"connector_name": "support_profile_sync", "customer_email": None, "customer_reference": "user_901",
                                                "enabled": False, "last_delivery_status": "stopped", "observed_at": obs, "resource_id": "data_flow_9005"}]},
        "backup_service": {"restore_entries": [{"archive_key": "archive/customer-data/backup_9006.tar.zst", "customer_email": None,
                                                "customer_reference": "user_901", "observed_at": obs, "restore_allowed": False,
                                                "resource_id": "backup_9006"}]},
    })


S1_EXPECT = {"syn_01": ("COMPLETE", [
    ("VERIFICATION", "account_service", "account_9001", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_01"),
    ("VERIFICATION", "security_service", "audit_record_9003", "VERIFY_RECORD_PRESENT", "PRESENT", "evidence_01"),
    ("VERIFICATION", "support_platform", "support_9004", "VERIFY_RECORD_REDACTED", "REDACTED", "evidence_01"),
    ("VERIFICATION", "analytics_platform", "analytics_9002", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED", "evidence_01"),
    ("VERIFICATION", "integration_service", "data_flow_9005", "VERIFY_DATA_FLOW_DISABLED", "DISABLED", "evidence_01"),
    ("VERIFICATION", "backup_service", "backup_9006", "VERIFY_RESTORE_BLOCKED", "BLOCKED", "evidence_01"),
])}


def _s2(s: Synth):
    s.user("user_902", "Tomas Brandt", "tomas.brandt@example.org", "+49 151 1000 9002")
    s.request("syn_02", "tomas.brandt@example.org", "2026-04-01T09:00:00Z", "NOT_STARTED", "2026-04-02T12:00:00Z", "2026-04-10T12:00:00Z")
    s.activity.append(manual_event("activity_911", "2026-04-02T11:56:00Z", "DELETE_RECORD", "account_service", "account_9101", "FAILED"))
    t = "2026-04-10T09:00:00Z"
    s.readback_json("2026-04-10T09:30:00Z", {
        "account_service": {"profile_lookups": [{"observed_at": t, "profile": {"customer_reference": None, "email": "tomas.brandt@example.org",
                                                                               "email_hash": None, "full_name": None, "phone": None},
                                                 "resource_id": "account_9101", "status_code": 200}]},
        "search_service": {"index_queries": [{"hits": {"documents": [{"customer_reference": None, "display_name": None,
                                                                      "email": "tomas.brandt@example.org", "email_hash": None, "phone": None,
                                                                      "resource_id": "search_9102"}], "total": 1},
                                              "observed_at": t, "query": "tomas.brandt@example.org", "resource_id": "search_9102"}]},
        "integration_service": {"connectors": [{"connector_name": "customer_events_connector", "customer_email": "tomas.brandt@example.org",
                                                "customer_reference": None, "enabled": True, "last_delivery_status": "delivered",
                                                "observed_at": t, "resource_id": "data_flow_9103"}]},
        "backup_service": {"restore_entries": [{"archive_key": "archive/customer-data/backup_9104.tar.zst", "customer_email": "tomas.brandt@example.org",
                                                "customer_reference": None, "observed_at": t, "restore_allowed": True, "resource_id": "backup_9104"}]},
        "export_service": {"exports": [{"customer_reference": None, "object_key": "exports/export_9105/customer_data.zip", "observed_at": t,
                                        "owner_email": "tomas.brandt@example.org", "owner_phone": None, "resource_id": "export_9105"}]},
        "support_platform": {"tickets": [{"customer_reference": None, "message_excerpt": "Customer supplied contact details.", "observed_at": t,
                                          "requester_email": "tomas.brandt@example.org", "requester_name": None, "requester_phone": None,
                                          "resource_id": "support_9106"}]},
    })


S2_EXPECT = {"syn_02": ("INCOMPLETE", [
    ("FINDING", "account_service", "account_9101", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_01"),
    ("FINDING", "export_service", "export_9105", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_01"),
    ("FINDING", "search_service", "search_9102", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_01"),
    ("FINDING", "support_platform", "support_9106", "REPORT_EXPOSED_PII", "PRESENT", "evidence_01"),
    ("FINDING", "integration_service", "data_flow_9103", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE", "evidence_01"),
    ("FINDING", "backup_service", "backup_9104", "REPORT_RESTORE_RISK", "ACTIVE", "evidence_01"),
    ("REMEDIATION", "integration_service", "data_flow_9103", "DISABLE_DATA_FLOW", "DISABLED", ""),
    ("REMEDIATION", "backup_service", "backup_9104", "BLOCK_RESTORE", "BLOCKED", ""),
    ("REMEDIATION", "account_service", "account_9101", "DELETE_RECORD", "ABSENT", ""),
    ("REMEDIATION", "export_service", "export_9105", "DELETE_RECORD", "ABSENT", ""),
    ("REMEDIATION", "search_service", "search_9102", "DELETE_RECORD", "ABSENT", ""),
    ("REMEDIATION", "support_platform", "support_9106", "REDACT_RECORD", "REDACTED", ""),
    ("VERIFICATION", "account_service", "account_9101", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
    ("VERIFICATION", "export_service", "export_9105", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
    ("VERIFICATION", "search_service", "search_9102", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
    ("VERIFICATION", "support_platform", "support_9106", "VERIFY_RECORD_REDACTED", "REDACTED", ""),
    ("VERIFICATION", "integration_service", "data_flow_9103", "VERIFY_DATA_FLOW_DISABLED", "DISABLED", ""),
    ("VERIFICATION", "backup_service", "backup_9104", "VERIFY_RESTORE_BLOCKED", "BLOCKED", ""),
])}


def _s3(s: Synth):
    """Non-deletion request + tombstone/legal-hold handling."""
    s.user("user_903", "Mira Kolar", "mira.kolar@example.org", "+386 31 000 903")
    s.user("user_904", "Jan Kolar", "jan.kolar@example.org", "+386 31 000 904")
    s.request("syn_03a", "mira.kolar@example.org", "2026-05-01T09:00:00Z", "NOT_STARTED", "2026-05-02T09:00:00Z", "2026-05-05T09:00:00Z",
              "Please send me a copy of the information held on my account. This is an information request, not a request to erase it.")
    s.request("syn_03b", "jan.kolar@example.org", "2026-05-01T09:00:00Z", "COMPLETE", "2026-05-02T12:00:00Z", "2026-05-05T09:00:00Z")
    s.activity.append(manual_event("activity_921", "2026-05-02T11:57:00Z", "DELETE_RECORD", "account_service", "account_9201"))
    t = "2026-05-05T08:00:00Z"
    s.sql("2026-05-05T08:30:00Z", f"""BEGIN TRANSACTION;
CREATE TABLE customer_data_primary (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL, collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO "customer_data_primary" VALUES('tombstone_9202','identity_service','deleted_customer_references','{{"resource_id":"tombstone_9202","subject_reference":"user_904","name":"Jan Kolar","email":"jan.kolar@example.org","phone":null,"email_hash":null,"account_status":"DELETED"}}','{t}');
INSERT INTO "customer_data_primary" VALUES('legal_hold_9203','legal_service','retained_order_records','{{"resource_id":"legal_hold_9203","subject_reference":"user_904","order_reference":"ORD-20269203","retention_reason":"payment_dispute_and_tax_record","retained_until":"2033-12-31T23:59:59Z"}}','{t}');
INSERT INTO "customer_data_primary" VALUES('audit_record_9204','security_service','security_audit_events','{{"resource_id":"audit_record_9204","subject_reference":"user_904","event_name":"account_deletion_reviewed","actor_type":"automated_privacy_workflow","integrity_seal":"sha256:00"}}','{t}');
COMMIT;
""")


S3_EXPECT = {
    "syn_03a": ("NOT_APPLICABLE", []),
    "syn_03b": ("INCOMPLETE", [
        ("FINDING", "identity_service", "tombstone_9202", "REPORT_EXPOSED_PII", "PRESENT", "evidence_01"),
        ("REMEDIATION", "identity_service", "tombstone_9202", "REDACT_RECORD", "REDACTED", ""),
        ("VERIFICATION", "account_service", "account_9201", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
        ("VERIFICATION", "legal_service", "legal_hold_9203", "VERIFY_RECORD_PRESENT", "PRESENT", ""),
        ("VERIFICATION", "security_service", "audit_record_9204", "VERIFY_RECORD_PRESENT", "PRESENT", ""),
        ("VERIFICATION", "identity_service", "tombstone_9202", "VERIFY_RECORD_REDACTED", "REDACTED", ""),
    ]),
}


def _s4(s: Synth):
    """Request made with a historical address; evidence only knows the old address."""
    s.user("user_905", "Ada Lund", "ada.lund@example.org", "+46 70 000 905",
           history=[("ada.l.old@example.org", "2026-02-01T10:00:00Z")])
    s.request("syn_04", "ada.l.old@example.org", "2026-06-01T09:00:00Z", "PARTIALLY_COMPLETE", "2026-06-02T12:00:00Z", "2026-06-06T12:00:00Z")
    s.activity.append(manual_event("activity_931", "2026-06-02T11:57:00Z", "DELETE_RECORD", "account_service", "account_9301", "PARTIAL"))
    s.log("2026-06-06T11:00:00Z", [
        '2026-06-06T10:00:00Z level=DEBUG service=search_service trace_id=tr-1 event=index_query_started resource_id=search_9302 lookup_key=ada.l.old@example.org',
        '2026-06-06T10:00:01Z level=INFO service=search_service trace_id=tr-1 event=index_query_completed resource_id=search_9302 result=hit total_hits=1 index=customer_directory_v3 email=ada.l.old@example.org customer_reference=null',
        '2026-06-06T10:01:00Z level=INFO service=account_service trace_id=tr-2 resource_id=account_9301 event=profile_lookup_completed rows=0 http_status=404 duration_ms=12 correlation_key="user_905"',
    ])


S4_EXPECT = {"syn_04": ("INCOMPLETE", [
    ("FINDING", "search_service", "search_9302", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_01"),
    ("REMEDIATION", "search_service", "search_9302", "DELETE_RECORD", "ABSENT", ""),
    ("VERIFICATION", "account_service", "account_9301", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
    ("VERIFICATION", "search_service", "search_9302", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
])}


def _s5(s: Synth):
    """The account is gone at audit_at but a later lookup (after audit_at) shows it recreated:
    later evidence must not change the answer."""
    s.user("user_906", "Noor Aziz", "noor.aziz@example.org", "+971 50 000 906")
    s.request("syn_05", "noor.aziz@example.org", "2026-07-01T09:00:00Z", "COMPLETE", "2026-07-02T12:00:00Z", "2026-07-03T08:00:00Z")
    s.activity.append(manual_event("activity_941", "2026-07-02T11:57:00Z", "DELETE_RECORD", "account_service", "account_9401"))
    s.log("2026-07-03T07:30:00Z", [
        '2026-07-03T07:10:00Z level=DEBUG service=account_service trace_id=tr-1 event=profile_lookup_started lookup_key=noor.aziz@example.org',
        '2026-07-03T07:10:01Z level=INFO service=account_service trace_id=tr-1 event=profile_lookup_completed resource_id=account_9401 rows=0 http_status=404',
    ], name="before_audit.log")
    s.log("2026-07-06T07:30:00Z", [
        '2026-07-03T07:10:00Z level=DEBUG service=account_service trace_id=tr-1 event=profile_lookup_started lookup_key=noor.aziz@example.org',
        '2026-07-03T07:10:01Z level=INFO service=account_service trace_id=tr-1 event=profile_lookup_completed resource_id=account_9401 rows=0 http_status=404',
        '2026-07-05T07:10:00Z level=DEBUG service=account_service trace_id=tr-9 event=profile_lookup_started lookup_key=noor.aziz@example.org',
        '2026-07-05T07:10:01Z level=INFO service=account_service trace_id=tr-9 event=profile_lookup_completed resource_id=account_9401 rows=1 http_status=200',
    ], name="after_audit.log")


S5_EXPECT = {"syn_05": ("COMPLETE", [
    ("VERIFICATION", "account_service", "account_9401", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_01"),
])}

SCENARIOS = [
    Scenario("complete_verifiable", "Completed deletion: absent account, retained audit record, redacted support, anonymized analytics, disabled flow, blocked restore - every check cites evidence", _s1, S1_EXPECT),
    Scenario("incomplete_flow_restore", "Remaining account/search/export, exposed support message, active data flow and restorable backup - ordered findings, remediation and verification", _s2, S2_EXPECT),
    Scenario("non_deletion_and_retained", "Access request is NOT_APPLICABLE ([]); exposed tombstone is redacted not deleted; legal hold and audit record are verified present", _s3, S3_EXPECT),
    Scenario("historical_email", "Request filed with a historical address resolves to the stable user via user_audit_log", _s4, S4_EXPECT),
    Scenario("later_evidence_ignored", "A lookup after audit_at that shows the account recreated must not change the audit_at answer", _s5, S5_EXPECT),
]


def run_scenario(sc: Scenario, workdir: Path | None = None):
    """Build the synthetic dataset, run the production pipeline, return (decisions_by_case, mismatches)."""
    tmp = None
    if workdir is None:
        tmp = tempfile.TemporaryDirectory()
        workdir = Path(tmp.name)
    s = Synth(workdir)
    sc.build(s)
    s.write()
    aud = Auditor(workdir)
    cases = aud.load_cases(workdir / "deletion_requests.csv")
    decisions = {d.case.case_id: d for d in aud.audit_all(cases)}
    problems = []
    for cid, (status, steps) in sc.expect.items():
        d = decisions[cid]
        got = [(x.phase, x.system_id, x.resource_id, x.operation, x.state, x.evidence_id) for x in d.steps]
        if d.status != status:
            problems.append(f"{cid}: status {d.status} != {status}")
        if got != steps:
            problems.append(f"{cid}: steps differ\n  got {got}\n  exp {steps}")
    if tmp:
        tmp.cleanup()
    return decisions, problems


if __name__ == "__main__":
    for sc in SCENARIOS:
        _, probs = run_scenario(sc)
        print(sc.name, "OK" if not probs else probs)
