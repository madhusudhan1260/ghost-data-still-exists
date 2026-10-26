"""Core data structures shared across the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Obs:
    """One normalized observation of a resource, extracted from a piece of evidence.

    `kind` is the semantic record type (account, search, support_message,
    support_attachment, analytics, marketing, export, data_flow, backup, audit,
    tombstone, legal_hold, ...). `state` carries kind-specific facts that the
    policy engine reads (present, enabled, restore_allowed, exposed, ...).
    """

    file_id: str
    t: str  # effective observation time (ISO, Z)
    system: str
    rid: str
    kind: str
    ids: set[str] = field(default_factory=set)  # emails, user refs, hashes found on the record
    phones: set[str] = field(default_factory=set)
    state: dict = field(default_factory=dict)
    source: str = ""  # parser/table/event the observation came from

    def key(self) -> tuple[str, str]:
        return (self.system, self.rid)


@dataclass
class Case:
    case_id: str
    email: str
    requested_at: str
    reported_status: str
    reported_at: str
    audit_at: str
    request_text: str


@dataclass
class Step:
    phase: str
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str = ""

    def as_dict(self, n: int) -> dict:
        return {
            "step_no": n,
            "phase": self.phase,
            "system_id": self.system_id,
            "resource_id": self.resource_id,
            "operation": self.operation,
            "state": self.state,
            "evidence_id": self.evidence_id,
        }


@dataclass
class Decision:
    case: Case
    status: str
    steps: list[Step]
    notes: list[str] = field(default_factory=list)
    user_id: Optional[str] = None
