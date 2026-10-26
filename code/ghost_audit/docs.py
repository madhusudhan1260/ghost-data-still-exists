"""Document evidence (PDF text and OCR'd screenshots) -> observations.

Business artifacts (customer statements, support attachments, admin-console
screenshots, retention journals) rarely contain a verdict; they show that a record
*exists* and which identifiers it carries. A resource mentioned in a document is bound to a
user in two grounded ways:
  1. the data catalog (`LINK_RESOURCE_IDENTITY` activity) links the resource id - or an
     external label printed on the document, e.g. a statement number or file name - to a user;
  2. the document mentions exactly one resource of the primary kind together with one of the
     user's own identifiers (e-mail, user id, any historical phone).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from .models import Obs
from .parsers import DocText
from .util import RID_RE, USER_RE, extract_ids, prefix_of, system_of

CATALOG_RE = re.compile(
    r"(?:linked|confirmed)\s+(?P<rid>[a-z_]+_\w+)\s+\((?P<label>[^)]*)\)\s+(?:to|belongs only to)\s+(?P<email>\S+@\S+?)\s+and\s+(?P<user>user_\d+)",
    re.I)


TOMBSTONE_WORDS = ("retained reference", "deleted-account reference", "deleted account", "customer references",
                   "tombstone")


@dataclass
class CatalogLink:
    rid: str
    label: str
    ids: set[str]
    t: str
    activity_id: str
    exclusive: bool


def catalog_from_activities(acts) -> list[CatalogLink]:
    out = []
    for a in acts:
        if a.event_type != "LINK_RESOURCE_IDENTITY":
            continue
        m = CATALOG_RE.search(a.details)
        if not m:
            continue
        ids = extract_ids([m.group("email"), m.group("user")])
        out.append(CatalogLink(m.group("rid"), m.group("label"), ids, a.t, a.activity_id, "only" in a.details))
    return out


def _normalize(text: str) -> str:
    """OCR/PDF text -> searchable text: 'audit record 003' -> 'audit_record_003'."""
    t = re.sub(r"\b(account|search|support|analytics|export|backup|tombstone|marketing|object)[ ·]+(\d{1,6})\b",
               lambda m: f"{m.group(1)}_{m.group(2)}", text, flags=re.I)
    t = re.sub(r"\b(audit)[ _]record[ _](\d+)\b", r"audit_record_\2", t, flags=re.I)
    t = re.sub(r"\bdata[ _]flow[ _](\d+)\b", r"data_flow_\1", t, flags=re.I)
    return t


def _kind_for(rid: str, text: str) -> Optional[str]:
    p = prefix_of(rid)
    low = text.lower()
    if p == "account":
        return "account"
    if p == "search":
        return "search"
    if p == "analytics":
        return "analytics"
    if p == "export":
        return "export"
    if p == "data_flow":
        return "data_flow"
    if p == "backup":
        return "backup"
    if p == "audit_record":
        return "audit"
    if p == "tombstone":
        return "tombstone"
    if p == "marketing":
        return "marketing"
    if p == "object":
        return "support_attachment"
    if p == "legal_hold":
        return "legal_hold"
    if p == "support":
        if "attachment" in low or "uploader" in low or "file delivery_" in low or ".pdf" in low:
            return "support_attachment"
        return "support_message"
    return None


def _state_for(kind: str, text: str, ids_present: bool) -> dict:
    low = text.lower()
    if kind == "data_flow":
        off = any(w in low for w in ("disabled", "paused", "stopped", "inactive"))
        return {"enabled": not off}
    if kind == "backup":
        blocked = any(w in low for w in ("restore blocked", "restore denied", "restore_allowed=false", "blocked"))
        return {"restore_allowed": not blocked}
    if kind == "analytics":
        return {"attributable": ids_present}
    if kind in ("tombstone", "support_message"):
        return {"present": True, "exposed": ids_present, "redacted": not ids_present}
    return {"present": True}


def docs_to_obs(docs: Iterable[DocText], catalog: list[CatalogLink]) -> list[Obs]:
    by_rid: dict[str, list[CatalogLink]] = {}
    by_label: dict[str, list[CatalogLink]] = {}
    for c in catalog:
        by_rid.setdefault(c.rid, []).append(c)
        if c.label and c.label != c.rid:
            by_label.setdefault(c.label, []).append(c)
    out: list[Obs] = []
    for d in docs:
        text = _normalize(d.text)
        rids = list(dict.fromkeys(RID_RE.findall(text)))
        # catalog binding through printed labels (statement numbers, file names)
        low_all = text.lower()
        label_hits = []
        for lbl, links in by_label.items():
            if lbl.lower() not in low_all:
                continue
            if USER_RE.fullmatch(lbl):
                # a bare user id is only a tombstone reference when the page says it is one
                links = [c for c in links if prefix_of(c.rid) == "tombstone"
                         and any(k in low_all for k in TOMBSTONE_WORDS)]
            if links:
                label_hits.append((lbl, links))
        bound: dict[str, set[str]] = {}
        for rid in rids:
            if rid in by_rid:
                bound[rid] = set().union(*[c.ids for c in by_rid[rid]])
        for lbl, links in label_hits:
            for c in links:
                bound.setdefault(c.rid, set()).update(c.ids)
        doc_ids = extract_ids([text])
        if not bound:
            primary = [r for r in rids if prefix_of(r) in ("support", "account", "search", "export", "backup", "tombstone",
                                                              "analytics", "data_flow", "marketing", "object")]
            if len(primary) == 1:
                bound[primary[0]] = doc_ids
        for rid, ids in bound.items():
            kind = _kind_for(rid, text)
            if not kind:
                continue
            low = text.lower()
            # exposure = one of the subject's direct identifiers is visible on the document
            visible = any((i in low) for i in ids if "@" in i or len(i) == 64)
            if kind in ("analytics",):
                visible = visible or any(i in low for i in ids if i.startswith("user_"))
            state = _state_for(kind, text, visible)
            out.append(Obs(d.file_id, d.captured_at, system_of(rid) or "unknown", rid, kind, set(ids), set(), state,
                           f"{d.kind}:doc"))
    return out
