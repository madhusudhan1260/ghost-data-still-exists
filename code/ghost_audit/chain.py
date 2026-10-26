"""Link resources that carry no identifier to the case they belong to, via system_activity.

Why this exists: an account that has been deleted (HTTP 404 / NOT_FOUND) and an
analytics profile that has been anonymized no longer contain any identifier, so
the evidence files cannot say whose they are. The deletion worker, however, wrote
one `DELETE_RECORD` for the case's account and a short chain of policy actions
around the time the workflow reported its status. We therefore join on activity:

  * time window  - the account delete lands shortly *before* `reported_at`;
  * report claim - the activity `reported_result` mirrors the workflow's
                   `reported_status` (SUCCESS/PARTIAL/FAILED);
  * chain phase  - actions of one case are emitted by the same worker run, so their
                   second-of-minute offsets relative to the account delete are
                   constant (mod 30 s). The offsets are *learned from cases whose
                   resources are identity-linked*, not hard-coded.

Activity results are treated as claims only; they are used for linking, never as
proof of a resource's state.
"""
from __future__ import annotations

import csv
import datetime as dt
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .util import parse_ts

STATUS_RESULT = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}
MOD = 30.0


@dataclass
class Activity:
    activity_id: str
    t: str
    dtv: dt.datetime
    event_type: str
    source: str
    target: str
    rid: str
    result: str
    details: str


def load_activities(path: Path) -> list[Activity]:
    out = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            out.append(Activity(r["activity_id"], r["event_time"], parse_ts(r["event_time"]), r["event_type"],
                                r["source_system"], r["target_system"], r["resource_id"], r["reported_result"], r["details"]))
    return out


class ActivityIndex:
    def __init__(self, acts: list[Activity]):
        self.acts = acts
        self.by_rid: dict[str, list[Activity]] = defaultdict(list)
        for a in acts:
            self.by_rid[a.rid].append(a)
        self.account_deletes = [a for a in acts if a.event_type == "DELETE_RECORD" and a.source == "deletion_worker"
                                and a.target == "account_service"]
        self.worker_events = [a for a in acts if a.source == "deletion_worker"]

    def worker_events_for(self, rid: str) -> list[Activity]:
        return [a for a in self.by_rid.get(rid, []) if a.source == "deletion_worker"]


def _circ(d: float) -> float:
    d = d % MOD
    return min(d, MOD - d)


class PhaseModel:
    """Likelihood model of the second-of-minute offset between a case's account delete
    and its other worker actions. Learned from identity-anchored cases:
      * pooled per side (before/after the report) modulo 30 s (robust, ample data);
      * per (side, event type) modulo 60 s (the worker alternates a 30 s shift by slot,
        so the exact cluster depends on the action type) when enough samples exist."""

    def __init__(self):
        self.tol = 2.0
        self.pool30: dict[str, list[float]] = {}
        self.typed60: dict[tuple[str, str], list[float]] = {}
        self.learned: dict = {}

    @staticmethod
    def side(ev: Activity, reported: dt.datetime) -> str:
        return "post" if ev.dtv >= reported else "pre"

    def learn(self, samples: list[tuple[str, str, float]]) -> None:
        for side, etype, diff in samples:
            self.pool30.setdefault(side, []).append(diff % MOD)
            self.typed60.setdefault((side, etype), []).append(diff % 60.0)
        self.learned = {"pooled": {k: len(v) for k, v in self.pool30.items()},
                        "typed": {f"{k[0]}:{k[1]}": len(v) for k, v in self.typed60.items() if len(v) >= 10}}

    @staticmethod
    def _density(vals: list[float], x: float, modulus: float, width: float = 1.5) -> float:
        n = len(vals)
        hit = sum(1 for v in vals if min((v - x) % modulus, (x - v) % modulus) <= width)
        return (hit + 0.5) / (n + 1.0) / (2 * width)  # per-second density, lightly smoothed

    def llr(self, account_ev: Activity, ev: Activity, reported: dt.datetime) -> float:
        """log-likelihood ratio that `ev` belongs to the same worker run as `account_ev`
        versus an unrelated event (uniform phase)."""
        import math
        side = self.side(ev, reported)
        diff = (ev.dtv - account_ev.dtv).total_seconds()
        typed = self.typed60.get((side, ev.event_type), [])
        if len(typed) >= 10:
            return math.log(self._density(typed, diff % 60.0, 60.0) * 60.0)
        pooled = self.pool30.get(side, [])
        if len(pooled) >= 10:
            return math.log(self._density(pooled, diff % MOD, MOD) * MOD)
        return 0.0

    def ok(self, account_ev: Activity, ev: Activity, reported: dt.datetime) -> bool:
        return self.llr(account_ev, ev, reported) > 1.0
