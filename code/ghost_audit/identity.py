"""Identity resolution: request e-mail -> stable user_id plus every identifier ever held.

The request e-mail may be a current or a historical address. History comes from
`user_audit_log.csv` (field changes with timestamps). Ownership of an address is
temporal: an address belongs to the user who held it at the time of the request,
so two users are never merged because one inherited a recycled address.
"""
from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .util import norm_email, norm_phone


START = "0000-00-00T00:00:00Z"


@dataclass
class User:
    user_id: str
    name: str
    email: str
    phone: str
    email_hash: str
    updated_at: str
    # (email, valid_from, valid_to, audit_id that ended it or None)
    email_history: list[tuple[str, str, str, Optional[str]]] = field(default_factory=list)
    phones: set[str] = field(default_factory=set)
    hashes: set[str] = field(default_factory=set)
    audit_ids: list[str] = field(default_factory=list)

    @property
    def emails(self) -> set[str]:
        return {e for e, *_ in self.email_history}

    def identifiers(self) -> set[str]:
        return {self.user_id} | self.emails | self.hashes


@dataclass
class Resolution:
    user: Optional[User]
    via: str  # current | historical | none | ambiguous
    matched_email: str
    audit_ids: list[str] = field(default_factory=list)  # audit events that prove a historical match
    candidates: list[str] = field(default_factory=list)


class IdentityIndex:
    def __init__(self, dataset_dir: Path):
        self.users: dict[str, User] = {}
        self.owners: dict[str, list[tuple[str, str, str]]] = {}  # email -> [(user, from, to)]
        self.phone_owners: dict[str, set[str]] = {}
        with open(dataset_dir / "user_identities.csv", newline="") as f:
            for r in csv.DictReader(f):
                u = User(r["user_id"], r["name"], norm_email(r["email"]), r["phone"], r["email_hash"].lower(), r["updated_at"])
                self.users[u.user_id] = u
        changes: dict[str, list[dict]] = {}
        with open(dataset_dir / "user_audit_log.csv", newline="") as f:
            for r in csv.DictReader(f):
                changes.setdefault(r["user_id"], []).append(r)
        for uid, u in self.users.items():
            rows = sorted(changes.get(uid, []), key=lambda r: r["event_time"])
            emails = [r for r in rows if r["field_name"] == "email"]
            u.phones.add(norm_phone(u.phone))
            u.hashes.add(u.email_hash)
            for r in rows:
                u.audit_ids.append(r["audit_id"])
                if r["field_name"] == "phone":
                    u.phones.update({norm_phone(r["old_value"]), norm_phone(r["new_value"])})
                elif r["field_name"] == "email_hash":
                    u.hashes.update({r["old_value"].lower(), r["new_value"].lower()})
            start = START
            if emails:
                prev = start
                for r in emails:
                    u.email_history.append((norm_email(r["old_value"]), prev, r["event_time"], r["audit_id"]))
                    prev = r["event_time"]
                u.email_history.append((u.email, prev, "9999-12-31T23:59:59Z", None))
            else:
                u.email_history.append((u.email, start, "9999-12-31T23:59:59Z", None))
            for e, a, b, _ in u.email_history:
                self.owners.setdefault(e, []).append((uid, a, b))
                u.hashes.add(hashlib.sha256(e.encode()).hexdigest())
            for p in u.phones:
                self.phone_owners.setdefault(p, set()).add(uid)

        # A recycled address cannot belong to its new holder before its previous holder gave it up.
        for e, lst in self.owners.items():
            if len({o[0] for o in lst}) < 2:
                continue
            ends = [b for (_, a, b) in lst if b != "9999-12-31T23:59:59Z"]
            if not ends:
                continue
            latest = max(ends)
            self.owners[e] = [(uid, (latest if (a == START and b == "9999-12-31T23:59:59Z") else a), b) for uid, a, b in lst]

    def resolve(self, email: str, requested_at: str) -> Resolution:
        e = norm_email(email)
        owners = self.owners.get(e, [])
        if not owners:
            return Resolution(None, "none", e)
        users = sorted({o[0] for o in owners})
        if len(users) > 1:
            # recycled address: pick the holder at request time if that is unique
            holders = sorted({uid for uid, a, b in owners if a <= requested_at <= b})
            if len(holders) != 1:
                return Resolution(None, "ambiguous", e, candidates=users)
            users = holders
        u = self.users[users[0]]
        via = "current" if u.email == e else "historical"
        proof = [aid for (em, _, _, aid) in u.email_history if em == e and aid]
        return Resolution(u, via, e, proof)
