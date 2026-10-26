"""Deletion auditor: resolve identity, reconstruct resource state at audit_at,
apply policy, detect data-flow / restore risk and plan ordered steps."""
from __future__ import annotations

import csv
import datetime as dt
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .chain import ActivityIndex, Activity, PhaseModel, STATUS_RESULT, load_activities
from .identity import IdentityIndex, Resolution
from .intent import authorizes_deletion
from .inventory import Inventory, load_inventory
from .models import Case, Decision, Obs
from .planner import plan_steps
from .policy import PolicyEngine
from .timeline import ResState, reduce_states, _fnum
from .assign import hungarian_max
from .util import parse_ts

log = logging.getLogger("ghost_audit")

LOOKBACK_DAYS = 21           # evidence older than this before audit_at is not considered current
ACCOUNT_WINDOW = (dt.timedelta(minutes=100), dt.timedelta(minutes=20))  # account delete: R-100m .. R-20m
PROX = (dt.timedelta(minutes=12), dt.timedelta(minutes=6))
MANUAL_MARK = "Target resource:"  # activity template of the manually operated deletion workflow  # delete logged within minutes of the report
POLICY_KINDS = {"account", "search", "support_message", "support_attachment", "analytics", "marketing",
                "export", "data_flow", "backup", "audit", "legal_hold", "tombstone", "cache",
                "email_delivery", "secondary_identifier"}


@dataclass
class Work:
    case: Case
    deletion: bool
    intent_reason: str
    res: Optional[Resolution] = None
    states: dict = field(default_factory=dict)  # (system, rid, kind) -> ResState
    account_rid: Optional[str] = None
    account_event: Optional[Activity] = None
    account_source: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def reported(self) -> dt.datetime:
        return parse_ts(self.case.reported_at)


class Auditor:
    def __init__(self, dataset_dir: Path):
        self.dir = dataset_dir
        self.inv: Inventory = load_inventory(dataset_dir)
        self.idx = IdentityIndex(dataset_dir)
        self.policy = PolicyEngine(dataset_dir / "deletion_rules.csv")
        self.act = ActivityIndex(load_activities(dataset_dir / "system_activity.csv"))
        self.phase = PhaseModel()
        self.obs = self.inv.obs
        self.file_scope: dict[str, set[str]] = defaultdict(set)
        for o in self.obs:
            self.file_scope[o.file_id].add(o.system)
        self.obs_by_key: dict[tuple[str, str, str], list[Obs]] = defaultdict(list)
        for o in self.obs:
            self.obs_by_key[(o.system, o.rid, o.kind)].append(o)

    # ------------------------------------------------------------------ cases
    @staticmethod
    def load_cases(path: Path) -> list[Case]:
        with open(path, newline="") as f:
            return [Case(r["case_id"], r["email"], r["requested_at"], r["reported_status"], r["reported_at"],
                         r["audit_at"], r["request_text"]) for r in csv.DictReader(f)]

    # ------------------------------------------------------------------ linking
    def _link(self, w: Work) -> None:
        u = w.res.user
        ids = u.identifiers()
        lo = (parse_ts(w.case.audit_at) - dt.timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        mine = [o for o in self.obs if (o.ids & ids) or (o.phones & u.phones)]
        w.states = self._reduce(w, mine, lo)
        accounts = [s for s in w.states.values() if s.kind == "account"]
        if accounts:
            best = max(accounts, key=lambda s: (s.t, -_fnum(s.evidence_id)))
            for s in accounts:
                if s is not best:
                    del w.states[(s.system, s.rid, s.kind)]
            w.account_rid = best.rid
            w.account_source = "evidence"

    def _reduce(self, w: Work, mine: list[Obs], lo: str) -> dict:
        """Reduce identity-linked observations, then join every other observation of the
        same resource (a 404 or an anonymized copy carries no identifier but is still
        the later truth about a resource we already know belongs to this user)."""
        ftime = {k: v.captured_at for k, v in self.inv.files.items()}
        first = reduce_states(mine, w.case.audit_at, not_before=lo, file_time=ftime)
        rids = {(s.system, s.rid, s.kind) for s in first.values()}
        extra = [o for o in self.obs_by_key_get(rids)]
        return reduce_states(list(mine) + extra, w.case.audit_at, not_before=lo, file_time=ftime)

    def obs_by_key_get(self, keys):
        for k in keys:
            yield from self.obs_by_key.get(k, [])

    def _anchors(self, w: Work) -> list[Activity]:
        evs = []
        for (sysn, rid, kind), st in w.states.items():
            if kind == "account":
                continue
            evs.extend(self.act.worker_events_for(rid))
        return evs

    def _learn_phase(self, works: list[Work]) -> None:
        samples = []
        for w in works:
            if not (w.deletion and w.account_rid):
                continue
            acc = [a for a in self.act.worker_events_for(w.account_rid) if a.event_type == "DELETE_RECORD"]
            if not acc:
                continue
            for ev in self._anchors(w):
                side = self.phase.side(ev, w.reported)
                samples.append((side, ev.event_type, (ev.dtv - acc[0].dtv).total_seconds()))
        self.phase.learn(samples)
        log.info("phase model learned: %s", self.phase.learned)

    def _assign_accounts(self, works: list[Work]) -> None:
        """Assign each deletion case the account-delete event that belongs to it.

        Evidence-linked accounts are taken as given. Proximity matches (worker event logged within
        minutes of the report) come next. The rest is a global assignment problem: edge weight =
        phase log-likelihood of the case's anchor events against the candidate account delete (+ the
        best anonymized-analytics phase match), solved optimally per connected component so a
        coincidental phase match cannot steal an account from the case it truly belongs to."""
        claimed = {w.account_rid for w in works if w.account_rid}
        free = [a for a in self.act.account_deletes if a.rid not in claimed]
        pending = [w for w in works if w.deletion and w.res and w.res.user and not w.account_rid]
        for w in list(pending):
            R = w.reported
            near = sorted((a for a in free if R - PROX[0] <= a.dtv <= R + PROX[1] and MANUAL_MARK in a.details),
                          key=lambda a: abs((a.dtv - R).total_seconds()))
            if near:
                ev = near[0]
                w.account_rid, w.account_event, w.account_source = ev.rid, ev, "activity-proximity"
                free.remove(ev)
                pending.remove(w)
        lo, hi = ACCOUNT_WINDOW
        an_cands = self._anonymized_analytics_candidates(works)
        edges: dict[tuple[int, int], float] = {}
        for i, w in enumerate(pending):
            R = w.reported
            want = STATUS_RESULT.get(w.case.reported_status)
            anchors = self._anchors(w)
            files = {o.file_id for st in w.states.values() for o in st.obs if o.t <= w.case.audit_at}
            for j, a in enumerate(free):
                if not (R - lo <= a.dtv <= R - hi):
                    continue
                if want and a.result != want:
                    continue
                score = sum(self.phase.llr(a, e, R) for e in anchors)
                if not anchors and an_cands:
                    best = [self.phase.llr(a, e, R) for (o, e) in an_cands if o.file_id in files and e.dtv < R
                            and R - e.dtv <= dt.timedelta(minutes=60)]
                    score += max([x for x in best if x > 1.0], default=0.0)
                edges[(i, j)] = score
        # connected components of the bipartite candidate graph
        parent: dict = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for (i, j) in edges:
            parent[find(("c", i))] = find(("e", j))
        comps: dict = defaultdict(lambda: ([], []))
        for (i, j) in edges:
            r = find(("c", i))
            if i not in comps[r][0]:
                comps[r][0].append(i)
            if j not in comps[r][1]:
                comps[r][1].append(j)
        assigned: set[int] = set()
        for rows, cols in comps.values():
            sol = hungarian_max([[edges.get((i, j)) for j in cols] for i in rows])
            for ri, cj in sol:
                i, j = rows[ri], cols[cj]
                w, ev = pending[i], free[j]
                w.account_rid, w.account_event = ev.rid, ev
                anchored = bool(self._anchors(w))
                w.account_source = "activity-chain" if anchored else "activity-window"
                cand_scores = sorted((edges[(i, jj)] for jj in cols if (i, jj) in edges), reverse=True)
                if len(cand_scores) > 1 and cand_scores[0] - cand_scores[1] < 1.0:
                    w.notes.append(f"account {ev.rid} chosen among {len(cand_scores)} equally plausible deletion events")
                assigned.add(i)
        for i, w in enumerate(pending):
            if i not in assigned:
                w.notes.append("no account delete event matched this case")

    def _anonymized_analytics_candidates(self, works: list[Work]) -> list[tuple[Obs, Activity]]:
        linked = set()
        for w in works:
            linked.update(rid for (_, rid, k) in w.states if k == "analytics")
        out = []
        seen = set()
        for o in self.obs:
            if (o.kind == "analytics" and not o.ids and not o.phones and not o.state.get("attributable")
                    and o.rid not in linked and o.rid not in seen):
                seen.add(o.rid)
                for e in self.act.worker_events_for(o.rid):
                    if e.event_type == "ANONYMIZE_RECORD":
                        out.append((o, e))
        return out

    def _pair_anonymized_analytics(self, works: list[Work]) -> None:
        """Anonymized analytics profiles carry no identifier; attach them by activity chain
        (phase-consistent with the account delete) or by proximity to the report time."""
        ftime = {k: v.captured_at for k, v in self.inv.files.items()}
        linked_rids: set[str] = set()
        for w in works:
            linked_rids.update(rid for (_, rid, k) in w.states if k == "analytics")
        cand: list[Obs] = [o for o in self.obs if o.kind == "analytics" and not o.ids and not o.phones
                           and not o.state.get("attributable") and o.rid not in linked_rids]
        claimed: set[str] = set()
        for w in sorted(works, key=lambda x: x.case.case_id):
            if not w.deletion or any(k == "analytics" for (_, _, k) in w.states):
                continue
            acc = self._account_event(w) if w.account_rid else None
            R = w.reported
            lo = (parse_ts(w.case.audit_at) - dt.timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
            files = {o.file_id for st in w.states.values() for o in st.obs if o.t <= w.case.audit_at}
            best = None
            for o in cand:
                if o.rid in claimed or o.t > w.case.audit_at or o.t < lo or ftime.get(o.file_id, "") > w.case.audit_at:
                    continue
                evs = [e for e in self.act.worker_events_for(o.rid)
                       if e.event_type == "ANONYMIZE_RECORD" and e.dtv < R and R - e.dtv <= dt.timedelta(minutes=60)]
                for e in evs:
                    if R - e.dtv <= PROX[0] and MANUAL_MARK in e.details:  # logged within minutes of the report (manual workflow)
                        d = -50.0
                    elif acc is not None and (not files or o.file_id in files) and self.phase.ok(acc, e, R):
                        d = -self.phase.llr(acc, e, R)
                    else:
                        continue
                    if best is None or d < best[0]:
                        best = (d, o)
            if best:
                o = best[1]
                claimed.add(o.rid)
                key = (o.system, o.rid, o.kind)
                w.states[key] = reduce_states(self.obs_by_key[key], w.case.audit_at, file_time=ftime)[key]
                w.notes.append(f"anonymized analytics {o.rid} linked via activity chain")

    def _account_event(self, w: Work) -> Optional[Activity]:
        if w.account_event:
            return w.account_event
        if not w.account_rid:
            return None
        evs = [a for a in self.act.worker_events_for(w.account_rid) if a.event_type == "DELETE_RECORD"]
        w.account_event = evs[0] if evs else None
        return w.account_event

    # ------------------------------------------------------------------ main
    def audit_all(self, cases: list[Case], context_cases: Optional[list[Case]] = None) -> list[Decision]:
        """Audit `cases`. `context_cases` (e.g. the public sample requests) share the same
        activity stream; including them in the global linking step stops their account
        events from being mistaken for a target case's, but they are not returned."""
        works: list[Work] = []
        have = {c.case_id for c in cases}
        for c in list(cases) + [x for x in (context_cases or []) if x.case_id not in have]:
            ok, why = authorizes_deletion(c.request_text)
            w = Work(c, ok, why)
            if ok:
                w.res = self.idx.resolve(c.email, c.requested_at)
                if w.res.user:
                    self._link(w)
            works.append(w)
        self._learn_phase(works)
        self._assign_accounts(works)
        self._pair_anonymized_analytics(works)
        return [self._decide(w) for w in works if w.case.case_id in have]

    # ------------------------------------------------------------------ decision
    def _decide(self, w: Work) -> Decision:
        c = w.case
        if not w.deletion:
            return Decision(c, "NOT_APPLICABLE", [], [w.intent_reason])
        if not w.res or not w.res.user:
            return Decision(c, "INCOMPLETE", [], [f"identity not resolved ({w.res.via if w.res else 'n/a'})"])
        user = w.res.user
        audit_at = c.audit_at
        items: list[tuple[ResState, object]] = []
        for st in w.states.values():
            if st.kind not in POLICY_KINDS:
                continue
            t = self.policy.evaluate(st.kind, st.state, audit_at)
            if t is None:
                continue
            items.append((st, t))
        # case evidence file = most common file across cited resources (for absent-record verification)
        ftime = {k: v.captured_at for k, v in self.inv.files.items()}
        files = Counter()
        for st, _ in items:
            for o in st.obs:
                if o.t <= audit_at and ftime.get(o.file_id, "9") <= audit_at:
                    files[o.file_id] += 1
        case_file = ""
        if files:
            case_file = max(files, key=lambda f: (files[f], ftime[f]))
        if not case_file and w.account_rid:
            # nothing else to anchor on: cite the closest earlier snapshot that covers the account system
            scoped = [f for f, ef in self.inv.files.items() if ef.captured_at <= audit_at
                      and "account_service" in self.file_scope.get(f, set())]
            if scoped:
                case_file = max(scoped, key=lambda f: (ftime[f], _fnum(f)))
        for st, _ in items:  # a citation captured after audit_at is not admissible: fall back to a valid file
            if ftime.get(st.evidence_id, "") > audit_at and case_file:
                st.evidence_id = case_file
        steps = plan_steps(w, items, case_file)
        status = "INCOMPLETE" if any(not t.compliant for _, t in items) else "COMPLETE"
        return Decision(c, status, steps, w.notes, user.user_id)
