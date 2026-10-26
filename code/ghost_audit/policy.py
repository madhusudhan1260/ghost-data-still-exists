"""Policy engine: `deletion_rules.csv` drives the required treatment per data category.

The CSV is the single source of truth for *what* must happen to each category
(DELETE / REDACT / ANONYMIZE / RETAIN). This module only maps an observed record
kind onto a data category and decides whether the record's observed state satisfies
that treatment. Data flows have no row in the rules table: the problem statement
requires that no active process can copy or recreate deleted data, so an enabled
flow is always a violation.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


KIND_TO_CATEGORY = {
    "account": "USER_PROFILE",
    "secondary_identifier": "SECONDARY_IDENTIFIER",
    "search": "SEARCH_INDEX_ENTRY",
    "cache": "CACHE_ENTRY",
    "support_message": "SUPPORT_MESSAGE",
    "support_attachment": "SUPPORT_ATTACHMENT",
    "analytics": "ANALYTICS_PROFILE",
    "telemetry": "PRODUCT_TELEMETRY",
    "export": "USER_EXPORT",
    "marketing": "MARKETING_PROFILE",
    "email_delivery": "EMAIL_DELIVERY_LOG",
    "audit": "SECURITY_AUDIT_LOG",
    "legal_hold": "LEGAL_HOLD_RECORD",
    "backup": "BACKUP_ARCHIVE",
    "tombstone": "ACCOUNT_TOMBSTONE",
}

# Finding / remediation / verification vocabulary (problem_statement.md "Allowed Operations")
FIND_REMAINING, FIND_PII, FIND_FLOW, FIND_RESTORE = (
    "REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK")


@dataclass
class Rule:
    rule_id: str
    category: str
    condition: str
    action: str  # DELETE | REDACT | ANONYMIZE | RETAIN


@dataclass
class Treatment:
    """Outcome of checking one resource against policy."""
    compliant: bool
    finding: Optional[str]  # operation name or None
    remediation: Optional[tuple[str, str]]  # (operation, state)
    verification: Optional[tuple[str, str]]  # (operation, state)
    reason: str


class PolicyEngine:
    def __init__(self, rules_csv: Path):
        self.rules: dict[str, Rule] = {}
        with open(rules_csv, newline="") as f:
            for r in csv.DictReader(f):
                self.rules[r["data_category"]] = Rule(r["rule_id"], r["data_category"], r["condition"], r["required_action"].strip().upper())

    def action_for(self, kind: str) -> Optional[Rule]:
        return self.rules.get(KIND_TO_CATEGORY.get(kind, ""))

    # ------------------------------------------------------------------ evaluation
    def evaluate(self, kind: str, state: dict, audit_at: str) -> Optional[Treatment]:
        if kind == "data_flow":
            if state.get("enabled"):
                return Treatment(False, FIND_FLOW, ("DISABLE_DATA_FLOW", "DISABLED"),
                                 ("VERIFY_DATA_FLOW_DISABLED", "DISABLED"), "active data flow")
            return Treatment(True, None, None, ("VERIFY_DATA_FLOW_DISABLED", "DISABLED"), "flow disabled")
        rule = self.action_for(kind)
        if rule is None:
            return None
        act = rule.action
        if act == "DELETE":
            present = bool(state.get("present", True))
            if present:
                return Treatment(False, FIND_REMAINING, ("DELETE_RECORD", "ABSENT"),
                                 ("VERIFY_RECORD_ABSENT", "ABSENT"), f"{rule.category} must be deleted ({rule.rule_id})")
            return Treatment(True, None, None, ("VERIFY_RECORD_ABSENT", "ABSENT"), "record absent")
        if act == "REDACT":
            exposed = bool(state.get("exposed", False))
            if exposed:
                return Treatment(False, FIND_PII, ("REDACT_RECORD", "REDACTED"),
                                 ("VERIFY_RECORD_REDACTED", "REDACTED"), f"{rule.category} still exposes identifiers ({rule.rule_id})")
            return Treatment(True, None, None, ("VERIFY_RECORD_REDACTED", "REDACTED"), "identifiers redacted")
        if act == "ANONYMIZE":
            if state.get("attributable", False):
                return Treatment(False, FIND_PII, ("ANONYMIZE_RECORD", "ANONYMIZED"),
                                 ("VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"), f"{rule.category} still attributable ({rule.rule_id})")
            return Treatment(True, None, None, ("VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"), "record anonymized")
        if act == "RETAIN":
            if kind == "backup":
                if state.get("restore_allowed", False):
                    return Treatment(False, FIND_RESTORE, ("BLOCK_RESTORE", "BLOCKED"),
                                     ("VERIFY_RESTORE_BLOCKED", "BLOCKED"), f"backup restorable ({rule.rule_id})")
                return Treatment(True, None, None, ("VERIFY_RESTORE_BLOCKED", "BLOCKED"), "restore blocked")
            if state.get("present", True):
                return Treatment(True, None, None, ("VERIFY_RECORD_PRESENT", "PRESENT"), "retained as required")
            return Treatment(True, None, None, None, "retained record absent")
        return None
