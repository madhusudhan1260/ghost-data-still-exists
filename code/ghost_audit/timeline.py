"""Timeline reconstruction: the state of every resource *as of audit_at*.

Rules
  * Observations after `audit_at` are ignored entirely (later evidence must not change the answer).
  * For each resource the latest observation at or before `audit_at` wins, so a record
    that was deleted and later recreated (e.g. by a sync job) is reported as present,
    and a record that was present and later observed gone is reported as absent.
  * Observations that share the latest timestamp come from different copies of the same
    resource (primary vs. replicas, several services). The copies are merged
    pessimistically - if any copy still exposes the record, the resource is not compliant -
    because replication drift is a real violation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .models import Obs

# state keys that merge with OR (violation if any copy shows it)
OR_KEYS = ("present", "exposed", "attributable", "enabled", "restore_allowed")


@dataclass
class ResState:
    system: str
    rid: str
    kind: str
    t: str
    state: dict
    evidence_id: str
    obs: list[Obs] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return (self.system, self.rid)


def _fnum(fid: str) -> int:
    try:
        return int(fid.split("_")[1])
    except (IndexError, ValueError):
        return 10**9


def violating(kind: str, state: dict) -> bool:
    if kind in ("account", "search", "export", "marketing", "support_attachment", "cache", "email_delivery",
                "secondary_identifier"):
        return bool(state.get("present", True))
    if kind == "support_message" or kind == "tombstone":
        return bool(state.get("exposed"))
    if kind in ("analytics", "telemetry"):
        return bool(state.get("attributable"))
    if kind == "data_flow":
        return bool(state.get("enabled"))
    if kind == "backup":
        return bool(state.get("restore_allowed"))
    return False


def reduce_states(observations: Iterable[Obs], audit_at: str, not_before: str = "",
                  file_time: dict[str, str] | None = None) -> dict[tuple[str, str, str], ResState]:
    groups: dict[tuple[str, str, str], list[Obs]] = {}
    for o in observations:
        if o.t > audit_at or (not_before and o.t < not_before):
            continue
        groups.setdefault((o.system, o.rid, o.kind), []).append(o)
    out: dict[tuple[str, str, str], ResState] = {}
    for key, lst in groups.items():
        tmax = max(o.t for o in lst)
        latest = [o for o in lst if o.t == tmax]
        merged: dict = {}
        for o in latest:
            for k, v in o.state.items():
                if k in OR_KEYS:
                    merged[k] = bool(merged.get(k, False) or v)
                else:
                    merged.setdefault(k, v)
        if "redacted" in merged:
            merged["redacted"] = all(o.state.get("redacted", False) for o in latest if "redacted" in o.state) and not merged.get("exposed")
        viol = violating(key[2], merged)
        # cite an observation that actually proves the reduced state
        proving = [o for o in latest if violating(key[2], o.state) == viol] or latest
        # a citation must be a file captured at or before audit_at; prefer such files
        def late(o: Obs) -> int:
            return int(bool(file_time) and file_time.get(o.file_id, "") > audit_at)
        proving.sort(key=lambda o: (late(o), _fnum(o.file_id), o.source))
        cite = proving[0].file_id
        if file_time and file_time.get(cite, "") > audit_at:
            alt = [o for o in lst if not late(o) and violating(key[2], o.state) == viol]
            if alt:
                alt.sort(key=lambda o: (-len(o.t), o.t))
                cite = max(alt, key=lambda o: o.t).file_id
        out[key] = ResState(key[0], key[1], key[2], tmax, merged, cite, lst,
                            sorted({o.source for o in latest}))
    return out
