"""Step planner: turns policy treatments into the ordered findings / remediation /
verification list required by the specification."""
from __future__ import annotations

from .models import Step

FINDING_ORDER = ["REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK"]
REMEDIATION_ORDER = ["DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"]
VERIFICATION_ORDER = ["VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED",
                      "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED"]
PHASE_ORDER = {"FINDING": FINDING_ORDER, "REMEDIATION": REMEDIATION_ORDER, "VERIFICATION": VERIFICATION_ORDER}
PHASE_RANK = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}

FINDING_STATE = {"REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT",
                 "REPORT_ACTIVE_DATA_FLOW": "ACTIVE", "REPORT_RESTORE_RISK": "ACTIVE"}

# Whether compliant non-core records (support/analytics/flow/backup/tombstone) are
# verified in an INCOMPLETE case. The specification asks verification to "cover the
# required final state for each remediated record or process", so they are not.
INCLUDE_COMPLIANT_IN_INCOMPLETE = False


def sort_steps(steps: list[Step]) -> list[Step]:
    def key(s: Step):
        return (PHASE_RANK[s.phase], PHASE_ORDER[s.phase].index(s.operation), s.system_id, s.resource_id)
    seen, out = set(), []
    for s in sorted(steps, key=key):
        k = (s.phase, s.operation, s.system_id, s.resource_id)
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out


def plan_steps(w, items, case_file: str) -> list[Step]:
    """`items` = [(ResState, Treatment)]; `w` is the per-case work object."""
    steps: list[Step] = []
    violations = [(st, t) for st, t in items if not t.compliant]
    incomplete = bool(violations)
    account_seen = any(st.kind == "account" for st, _ in items)

    for st, t in violations:
        steps.append(Step("FINDING", st.system, st.rid, t.finding, FINDING_STATE[t.finding], st.evidence_id))
        op, state = t.remediation
        steps.append(Step("REMEDIATION", st.system, st.rid, op, state, ""))
        vop, vstate = t.verification
        steps.append(Step("VERIFICATION", st.system, st.rid, vop, vstate, ""))

    # the account is always verified absent once an account id is known
    if w.account_rid and not account_seen:
        steps.append(Step("VERIFICATION", "account_service", w.account_rid, "VERIFY_RECORD_ABSENT", "ABSENT",
                          "" if incomplete else case_file))

    for st, t in items:
        if t.compliant and t.verification:
            core = st.kind in ("account", "audit", "legal_hold")
            if incomplete and not (core or INCLUDE_COMPLIANT_IN_INCOMPLETE):
                continue
            if st.kind in ("search", "export", "support_attachment", "marketing", "cache", "email_delivery",
                           "secondary_identifier"):
                continue  # an already-absent non-account copy is not a retained or remediated record
            steps.append(Step("VERIFICATION", st.system, st.rid, t.verification[0], t.verification[1],
                              "" if incomplete else st.evidence_id))
    return sort_steps(steps)
