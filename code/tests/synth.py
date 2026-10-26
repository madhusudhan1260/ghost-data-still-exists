"""Builder for tiny SYNTHETIC datasets used by the tests and the evaluation report.

Everything produced here is invented (fictional people, ids and timestamps). It follows the
real participant-facing schemas so the production pipeline can run on it unchanged. It is
never mixed into the real dataset or its evaluation numbers.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

RULES = [
    ("rule_01", "USER_PROFILE", "Always", "DELETE"),
    ("rule_02", "SECONDARY_IDENTIFIER", "Always", "DELETE"),
    ("rule_03", "SEARCH_INDEX_ENTRY", "Always", "DELETE"),
    ("rule_04", "CACHE_ENTRY", "Always", "DELETE"),
    ("rule_05", "SUPPORT_MESSAGE", "Conversation contains shared customer, agent, or order context that must be retained", "REDACT"),
    ("rule_06", "SUPPORT_ATTACHMENT", "Attachment belongs only to the deletion subject", "DELETE"),
    ("rule_07", "ANALYTICS_PROFILE", "Record is still attributable to a user", "ANONYMIZE"),
    ("rule_08", "PRODUCT_TELEMETRY", "Record is still attributable to a user", "ANONYMIZE"),
    ("rule_09", "USER_EXPORT", "Always", "DELETE"),
    ("rule_10", "MARKETING_PROFILE", "Always", "DELETE"),
    ("rule_11", "EMAIL_DELIVERY_LOG", "Log contains a user email address", "REDACT"),
    ("rule_12", "SECURITY_AUDIT_LOG", "Record is within the supplied retention period", "RETAIN"),
    ("rule_13", "LEGAL_HOLD_RECORD", "A supplied legal hold is active", "RETAIN"),
    ("rule_14", "BACKUP_ARCHIVE", "Restore access can be blocked", "RETAIN"),
    ("rule_15", "ACCOUNT_TOMBSTONE", "Account is deleted and only a stable internal user_id is needed to preserve references to retained records", "REDACT"),
]

DELETE_TEXT = "Please erase the account linked to this email address and process the associated personal-data deletion."


class Synth:
    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "evidence").mkdir(parents=True, exist_ok=True)
        self.users: list[list[str]] = []
        self.audit: list[list[str]] = []
        self.activity: list[list[str]] = []
        self.files: list[list[str]] = []
        self.requests: list[list[str]] = []
        self._n_file = 0

    # ---- people
    def user(self, uid: str, name: str, email: str, phone: str, history: list[tuple[str, str]] = ()):
        """`history` = [(old_email, change_time)] oldest first; `email` is the current address."""
        h = hashlib.sha256(email.lower().encode()).hexdigest()
        self.users.append([uid, name, email, phone, h, "2026-01-01T00:00:00Z"])
        chain = [e for e, _ in history] + [email]
        for i, (old, when) in enumerate(history):
            new = chain[i + 1]
            self.audit.append([f"audit_{uid}_{i}", uid, when, "email", old, new])
            self.audit.append([f"audit_{uid}_{i}h", uid, when, "email_hash", hashlib.sha256(old.encode()).hexdigest(),
                               hashlib.sha256(new.encode()).hexdigest()])
        return h

    def account_delete_event(self, act_id: str, when: str, rid: str, result: str = "SUCCESS"):
        self.activity.append([act_id, when, "DELETE_RECORD", "deletion_worker", "account_service", rid, result,
                              f"Deletion worker submitted primary profile {rid} to account_service."])

    # ---- evidence files
    def _register(self, rel: str, origin: str, captured: str) -> str:
        self._n_file += 1
        fid = f"evidence_{self._n_file:02d}"
        self.files.append([fid, f"evidence/{rel}", origin, captured])
        return fid

    def readback_json(self, captured: str, sections: dict, name: str = "service_resource_export_syn.json") -> str:
        fid = self._register(f"records/{name}", "service_resource_export", captured)
        (self.root / "evidence" / "records").mkdir(parents=True, exist_ok=True)
        (self.root / "evidence" / "records" / name).write_text(json.dumps(
            {"exported_at": captured, "region": "global", "source": "synthetic", "service_exports": sections}, indent=1))
        return fid

    def log(self, captured: str, lines: list[str], name: str = "service_events_syn.log") -> str:
        fid = self._register(f"logs/{name}", "central_observability_export", captured)
        (self.root / "evidence" / "logs").mkdir(parents=True, exist_ok=True)
        (self.root / "evidence" / "logs" / name).write_text("\n".join(lines) + "\n")
        return fid

    def sql(self, captured: str, ddl_and_rows: str, name: str = "customer_data_syn.sql") -> str:
        fid = self._register(f"databases/{name}", "customer_data_read_replica_capture", captured)
        (self.root / "evidence" / "databases").mkdir(parents=True, exist_ok=True)
        (self.root / "evidence" / "databases" / name).write_text(ddl_and_rows)
        return fid

    # ---- requests
    def request(self, case_id: str, email: str, requested: str, status: str, reported: str, audit: str, text: str = DELETE_TEXT):
        self.requests.append([case_id, email, requested, status, reported, audit, text])

    # ---- write everything
    def write(self) -> Path:
        def w(name, header, rows):
            with open(self.root / name, "w", newline="") as f:
                cw = csv.writer(f)
                cw.writerow(header)
                cw.writerows(rows)
        w("user_identities.csv", ["user_id", "name", "email", "phone", "email_hash", "updated_at"], self.users)
        w("user_audit_log.csv", ["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"], self.audit)
        w("system_activity.csv", ["activity_id", "event_time", "event_type", "source_system", "target_system", "resource_id",
                                  "reported_result", "details"], self.activity)
        w("deletion_rules.csv", ["rule_id", "data_category", "condition", "required_action"], RULES)
        w("evidence_files.csv", ["file_id", "file_path", "origin", "captured_at"], self.files)
        w("deletion_requests.csv", ["case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at",
                                    "request_text"], self.requests)
        return self.root
