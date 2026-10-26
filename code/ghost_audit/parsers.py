"""Evidence parsers: turn every supplied evidence file into normalized `Obs` records.

Supported formats: SQLite-compatible SQL snapshots (executed with sqlite3), JSON
(snapshot bundles, service read-back exports, record ledgers), text/log files in
six logging dialects, plain-text handover tables, PDFs (pdfplumber/pypdf) and
images (OCR via ghost_audit.ocr). Parse failures are recorded, never silently
swallowed (see `ParseReport`).
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .models import Obs
from .util import EMAIL_RE, PHONE_RE, extract_ids, extract_phones, prefix_of, system_of

NULLS = {None, "", "null", "None", "NULL", "none"}


def _nn(v: Any) -> Any:
    return None if (v is None or (isinstance(v, str) and v.strip() in NULLS)) else v


def _truthy(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    s = str(v).strip().strip('"').lower()
    if s in ("true", "1", "yes", "y"):
        return True
    if s in ("false", "0", "no", "n"):
        return False
    return None


@dataclass
class ParseReport:
    parsed: dict[str, int] = field(default_factory=dict)  # file_id -> observation count
    failures: dict[str, str] = field(default_factory=dict)  # file_id -> reason
    notes: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- records
def _mk(file_id: str, t: str, rid: str, kind: str, ids=(), phones=(), state=None,
        source: str = "", system: Optional[str] = None) -> Obs:
    return Obs(file_id=file_id, t=t, system=system or system_of(rid) or "unknown", rid=rid,
               kind=kind, ids=set(ids), phones=set(phones), state=state or {}, source=source)


def record_to_obs(file_id: str, t: str, collection: str, rec: dict, source: str) -> Optional[Obs]:
    """Convert a structured record (SQL record_json / bundle record) to an Obs."""
    rid = rec.get("resource_id")
    if not rid:
        return None
    ph = extract_phones([rec.get("phone"), rec.get("requester_phone"), rec.get("owner_phone")])
    if collection == "customer_profiles":
        ids = extract_ids([rec.get("email"), rec.get("customer_reference"), rec.get("email_hash")])
        return _mk(file_id, t, rid, "account", ids, ph,
                   {"present": True, "status": rec.get("account_status")}, source)
    if collection == "customer_search_documents":
        doc = rec.get("document") or {}
        ids = extract_ids([doc.get("email"), doc.get("customer_reference"), doc.get("email_hash")])
        return _mk(file_id, t, rid, "search", ids, extract_phones([doc.get("phone")]),
                   {"present": True}, source)
    if collection == "customer_analytics_reports":
        vals = [rec.get("user_id"), rec.get("email"), rec.get("email_hash")]
        attrib = any(_nn(v) for v in vals + [rec.get("phone")])
        return _mk(file_id, t, rid, "analytics", extract_ids(vals), ph,
                   {"attributable": bool(attrib)}, source)
    if collection == "backup_catalog":
        return _mk(file_id, t, rid, "backup", extract_ids([rec.get("subject_reference")]), (),
                   {"restore_allowed": bool(_truthy(rec.get("restore_allowed")))}, source)
    if collection == "customer_export_jobs":
        return _mk(file_id, t, rid, "export", extract_ids([rec.get("owner_email")]), (),
                   {"present": True, "available": _truthy(rec.get("download_available")),
                    "expires_at": rec.get("expires_at")}, source)
    if collection == "deleted_customer_references":
        fields = [k for k in ("name", "email", "phone", "email_hash") if _nn(rec.get(k))]
        ids = extract_ids([rec.get("subject_reference"), rec.get("email"), rec.get("email_hash")])
        return _mk(file_id, t, rid, "tombstone", ids, ph,
                   {"present": True, "pii_fields": fields, "exposed": bool(fields)}, source)
    if collection == "connector_configurations":
        return _mk(file_id, t, rid, "data_flow", extract_ids([rec.get("subject_selector")]), (),
                   {"enabled": bool(_truthy(rec.get("enabled"))), "mode": rec.get("mode")}, source)
    if collection == "retained_order_records":
        return _mk(file_id, t, rid, "legal_hold", extract_ids([rec.get("subject_reference")]), (),
                   {"present": True, "retained_until": rec.get("retained_until")}, source)
    if collection == "security_audit_events":
        return _mk(file_id, t, rid, "audit", extract_ids([rec.get("subject_reference")]), (),
                   {"present": True, "event": rec.get("event_name"),
                    "retention_until": rec.get("retention_until")}, source)
    if collection == "support_conversations":
        text = rec.get("message_text") or ""
        ids = extract_ids([rec.get("subject_reference"), text])
        phones = extract_phones([text])
        exposed = bool(EMAIL_RE.search(text) or PHONE_RE.search(text))
        return _mk(file_id, t, rid, "support_message", ids, phones,
                   {"present": True, "exposed": exposed, "redacted": "[REDACTED]" in text and not exposed},
                   source)
    if collection == "ticket_attachments":
        return _mk(file_id, t, rid, "support_attachment", extract_ids([rec.get("uploaded_by")]), (),
                   {"present": True, "file": rec.get("file_name"), "ticket": rec.get("ticket_id")}, source)
    return None


LEDGER_KIND = {
    "USER_PROFILE": "account", "SEARCH_INDEX_ENTRY": "search", "ANALYTICS_PROFILE": "analytics",
    "BACKUP_ARCHIVE": "backup", "USER_EXPORT": "export", "DATA_FLOW": "data_flow",
    "LEGAL_HOLD_RECORD": "legal_hold", "SECURITY_AUDIT_LOG": "audit",
    "SUPPORT_MESSAGE": "support_message", "SUPPORT_ATTACHMENT": "support_attachment",
    "ACCOUNT_TOMBSTONE": "tombstone", "CACHE_ENTRY": "cache", "MARKETING_PROFILE": "marketing",
    "EMAIL_DELIVERY_LOG": "email_delivery", "PRODUCT_TELEMETRY": "analytics",
    "SECONDARY_IDENTIFIER": "secondary_identifier",
}


def ledger_to_obs(file_id: str, t: str, system: str, rid: str, rtype: str, result: str,
                  subject: str, source: str) -> Optional[Obs]:
    kind = LEDGER_KIND.get(rtype)
    if not kind:
        return None
    res = (result or "").upper()
    ids = extract_ids([subject])
    state: dict = {"result": res}
    if kind in ("account", "search", "export", "support_attachment", "marketing", "cache",
                "email_delivery", "secondary_identifier"):
        state["present"] = res not in ("NOT_FOUND", "ABSENT", "DELETED", "MISSING")
    elif kind == "analytics":
        state["attributable"] = res in ("IDENTIFIABLE", "ATTRIBUTABLE", "FOUND", "ACTIVE")
    elif kind == "backup":
        state["restore_allowed"] = res in ("ACTIVE", "ALLOWED", "FOUND", "RESTORABLE")
    elif kind == "data_flow":
        state["enabled"] = res in ("ACTIVE", "ENABLED", "FOUND")
    elif kind == "support_message":
        state["exposed"] = res in ("IDENTIFIABLE", "FOUND", "EXPOSED")
        state["redacted"] = res == "REDACTED"
        state["present"] = True
    elif kind == "tombstone":
        state["exposed"] = res in ("IDENTIFIABLE", "FOUND", "EXPOSED")
        state["present"] = True
    else:  # audit, legal_hold
        state["present"] = res not in ("NOT_FOUND", "ABSENT")
    return _mk(file_id, t, rid, kind, ids, (), state, source, system=system or None)


# --------------------------------------------------------------------------- SQL
INVENTORY_TABLES = {
    "customer_profiles": "account",
    "customer_profiles_primary": "account",
    "customer_profiles_failover_replica": "account",
    "customer_search_documents": "search",
    "search_documents_primary": "search",
    "customer_analytics_profiles": "analytics",
    "support_records": "support_message",
    "customer_exports": "export",
    "connector_subscriptions": "data_flow",
    "backup_restore_entries": "backup",
    "security_audit_records": "audit",
}


def _inventory_row(file_id: str, t: str, table: str, r: dict, source: str) -> Optional[Obs]:
    kind = INVENTORY_TABLES[table]
    rid = r.get("resource_id")
    if not rid:
        return None
    ph = extract_phones([r.get("phone"), r.get("requester_phone"), r.get("owner_phone")])
    if kind == "account":
        ids = extract_ids([r.get("email"), r.get("customer_reference"), r.get("user_id"), r.get("email_hash")])
        return _mk(file_id, t, rid, kind, ids, ph, {"present": True, "status": r.get("account_status")}, source)
    if kind == "search":
        ids = extract_ids([r.get("email"), r.get("customer_reference"), r.get("email_hash"), r.get("source_resource_id")])
        return _mk(file_id, t, rid, kind, ids, ph, {"present": True}, source)
    if kind == "analytics":
        vals = [r.get("customer_reference"), r.get("email"), r.get("email_hash")]
        return _mk(file_id, t, rid, kind, extract_ids(vals), ph,
                   {"attributable": any(_nn(v) for v in vals + [r.get("phone")])}, source)
    if kind == "support_message":
        vals = [r.get("customer_reference"), r.get("requester_email")]
        exposed = any(_nn(r.get(k)) for k in ("requester_name", "requester_email", "requester_phone"))
        return _mk(file_id, t, rid, kind, extract_ids(vals), ph,
                   {"present": True, "exposed": exposed, "redacted": not exposed}, source)
    if kind == "export":
        return _mk(file_id, t, rid, kind, extract_ids([r.get("owner_email"), r.get("customer_reference")]), ph,
                   {"present": True, "available": None}, source)
    if kind == "data_flow":
        return _mk(file_id, t, rid, kind, extract_ids([r.get("customer_email"), r.get("customer_reference")]), (),
                   {"enabled": bool(_truthy(r.get("enabled"))), "delivery": r.get("last_delivery_status")}, source)
    if kind == "backup":
        return _mk(file_id, t, rid, kind, extract_ids([r.get("customer_email"), r.get("customer_reference")]), (),
                   {"restore_allowed": bool(_truthy(r.get("restore_allowed")))}, source)
    if kind == "audit":
        return _mk(file_id, t, rid, kind, extract_ids([r.get("subject_reference")]), (),
                   {"present": True, "event": r.get("event_code"), "retention_until": r.get("retention_until")}, source)
    return None


def parse_sql(path: Path, file_id: str, captured_at: str) -> list[Obs]:
    con = sqlite3.connect(":memory:")
    con.executescript(path.read_text(errors="replace"))
    con.row_factory = sqlite3.Row
    tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    scope: dict[str, str] = {}
    if "snapshot_scope" in tables:
        scope = {r["origin_system"]: r["captured_at"] for r in con.execute("select * from snapshot_scope")}
    out: list[Obs] = []
    for table in tables:
        if table == "snapshot_scope":
            continue
        rows = [dict(r) for r in con.execute(f'select * from "{table}"')]
        for r in rows:
            if "record_json" in r:
                try:
                    rec = json.loads(r["record_json"])
                except json.JSONDecodeError:
                    continue
                t = captured_at  # each table is a copy captured at snapshot time (drift is judged at that time)
                o = record_to_obs(file_id, t, r.get("collection_name", ""), rec, f"sql:{table}")
                if o:
                    o.system = r.get("origin_system") or o.system
                    out.append(o)
            elif table in INVENTORY_TABLES:
                sysname = system_of(r.get("resource_id", "")) or ""
                t = scope.get(sysname) or r.get("updated_at") or captured_at
                o = _inventory_row(file_id, t, table, r, f"sql:{table}")
                if o:
                    out.append(o)
    con.close()
    return out


# --------------------------------------------------------------------------- JSON
def _readback_obs(file_id: str, section: str, key: str, r: dict, t_default: str) -> list[Obs]:
    rid = r.get("resource_id")
    t = r.get("observed_at") or t_default
    src = f"json:{section}.{key}"
    if not rid:
        return []
    if section == "account_service":
        prof = r.get("profile")
        present = prof is not None and (r.get("status_code") in (200, None))
        ids = extract_ids(list((prof or {}).values()))
        return [_mk(file_id, t, rid, "account", ids, extract_phones([(prof or {}).get("phone")]),
                    {"present": bool(present)}, src)]
    if section == "analytics_platform":
        vals = [r.get("customer_reference"), r.get("email"), r.get("email_hash")]
        return [_mk(file_id, t, rid, "analytics", extract_ids(vals), extract_phones([r.get("phone")]),
                    {"attributable": any(_nn(v) for v in vals + [r.get("phone")])}, src)]
    if section == "backup_service":
        return [_mk(file_id, t, rid, "backup", extract_ids([r.get("customer_email"), r.get("customer_reference")]), (),
                    {"restore_allowed": bool(_truthy(r.get("restore_allowed")))}, src)]
    if section == "export_service":
        return [_mk(file_id, t, rid, "export", extract_ids([r.get("owner_email"), r.get("customer_reference")]),
                    extract_phones([r.get("owner_phone")]), {"present": True, "available": None}, src)]
    if section == "integration_service":
        return [_mk(file_id, t, rid, "data_flow", extract_ids([r.get("customer_email"), r.get("customer_reference")]), (),
                    {"enabled": bool(_truthy(r.get("enabled"))), "delivery": r.get("last_delivery_status")}, src)]
    if section == "search_service":
        hits = (r.get("hits") or {}).get("documents") or []
        total = (r.get("hits") or {}).get("total", len(hits))
        ids: set[str] = set()
        for d in hits:
            ids |= extract_ids([d.get("email"), d.get("customer_reference"), d.get("email_hash")])
        ids |= extract_ids([r.get("query")])
        return [_mk(file_id, t, rid, "search", ids, (), {"present": bool(total)}, src)]
    if section == "security_service":
        return [_mk(file_id, t, rid, "audit", extract_ids([r.get("subject_reference")]), (),
                    {"present": True, "event": r.get("event_code"), "retention_until": r.get("retention_until")}, src)]
    if section == "support_platform":
        exposed = any(_nn(r.get(k)) for k in ("requester_name", "requester_email", "requester_phone"))
        return [_mk(file_id, t, rid, "support_message",
                    extract_ids([r.get("customer_reference"), r.get("requester_email")]),
                    extract_phones([r.get("requester_phone")]),
                    {"present": True, "exposed": exposed, "redacted": not exposed}, src)]
    return []


def parse_json(path: Path, file_id: str, captured_at: str) -> list[Obs]:
    d = json.loads(path.read_text(errors="replace"))
    out: list[Obs] = []
    if isinstance(d, dict) and "snapshots" in d:
        for s in d["snapshots"]:
            t = s.get("captured_at") or d.get("exported_at") or captured_at
            coll = s.get("collection", "")
            for rec in s.get("records", []):
                if coll == "operational_record_history":
                    o = ledger_to_obs(file_id, rec.get("recorded_at") or t, rec.get("system", ""), rec.get("resource_id", ""),
                                      rec.get("record_type", ""), rec.get("result", ""), rec.get("lookup_value", ""),
                                      f"json:{s.get('data_store')}.{coll}")
                else:
                    o = record_to_obs(file_id, t, coll, rec, f"json:{s.get('data_store')}.{coll}")
                if o:
                    if s.get("data_store") and coll != "operational_record_history":
                        o.system = o.system if o.system != "unknown" else s["data_store"]
                    out.append(o)
    elif isinstance(d, dict) and "service_exports" in d:
        t0 = d.get("exported_at") or captured_at
        for section, groups in d["service_exports"].items():
            for key, lst in groups.items():
                for r in lst:
                    out.extend(_readback_obs(file_id, section, key, r, t0))
    elif isinstance(d, dict) and "records" in d and ("origin" in d or "source" in d) and "collection" not in d:
        for r in d["records"]:
            o = ledger_to_obs(file_id, r.get("recorded_at") or captured_at, r.get("system", ""), r.get("resource_id", ""),
                              r.get("record_type", ""), r.get("result", ""), r.get("subject_reference", ""),
                              f"json:{d.get('origin')}")
            if o:
                out.append(o)
    elif isinstance(d, dict) and "collection" in d and "records" in d:
        coll = d["collection"]
        t = d.get("captured_at") or captured_at
        for rec in d["records"]:
            if coll == "customer_export_jobs":
                rec = dict(rec)
                o = record_to_obs(file_id, t, coll, rec, f"json:{d.get('source')}.{coll}")
            else:
                o = record_to_obs(file_id, t, coll, rec, f"json:{d.get('source')}.{coll}")
            if o:
                out.append(o)
    else:
        raise ValueError("unrecognized JSON layout")
    return out


# --------------------------------------------------------------------------- logs
KV_RE = re.compile(r'([A-Za-z_][\w\-]*)=("(?:[^"\\]|\\.)*"|[^\s]*)')
TS_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\s+(.*)$")


def _strip_q(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1].replace('\\"', '"')
    return v


def _kvs(text: str) -> dict:
    return {k: _strip_q(v) for k, v in KV_RE.findall(text)}


def parse_log_line(line: str) -> Optional[tuple[str, dict]]:
    """Return (timestamp, fields) with service/event/trace_id normalized, or None."""
    m = TS_RE.match(line.strip())
    if not m:
        return None
    ts, rest = m.group(1), m.group(2)
    d: dict = {}
    if rest.startswith("{"):
        try:
            j = json.loads(rest)
        except json.JSONDecodeError:
            return None
        d = {k: ("null" if v is None else (json.dumps(v) if isinstance(v, (dict, list)) else str(v))) for k, v in j.items()}
        d.setdefault("service", d.get("logger", ""))
    elif " message=\"" in rest or rest.startswith("requestId="):
        head, _, msg = rest.partition("message=")
        d = _kvs(head)
        msg = msg.strip()
        if msg.startswith('"') and msg.endswith('"'):
            msg = msg[1:-1].replace('\\"', '"')
        d.update(_kvs(msg))
        d["service"] = d.get("function", "")
        d["trace_id"] = d.get("requestId", "")
    elif rest.startswith("["):
        m2 = re.match(r"\[(\w+)\]\s+\[([\w\-]+)\]\s+\[([\w\-]+)\]\s+(\w+):\s*(.*)$", rest)
        if not m2:
            return None
        d = _kvs(m2.group(5))
        d.update(level=m2.group(1), service=m2.group(2), trace_id=m2.group(3), event=m2.group(4))
    elif "|" in rest.split("=")[0]:
        parts = [p.strip() for p in rest.split("|", 5)]
        if len(parts) < 6:
            return None
        d = _kvs(parts[5])
        d.update(level=parts[0], service=parts[1], trace_id=parts[2], event=parts[3])
        # operations_stream: LEVEL | service | trace | event | kvs  (5 parts)
    elif re.match(r"<\d+>1 ", rest):
        m2 = re.match(r"<\d+>1\s+\S+\s+([\w\-]+)\s+([\w\-]+)\s+-\s+-\s+(.*)$", rest)
        if not m2:
            return None
        d = _kvs(m2.group(3))
        d.update(service=m2.group(1), trace_id=m2.group(2))
    else:
        d = _kvs(rest)
    return ts, d


def _parse_pipe(line: str) -> Optional[tuple[str, dict]]:
    # 2026-07-03T07:51:30Z | DEBUG | log_router | collector-5 | export_started | kv...
    m = re.match(r"^(\d{4}-\d\d-\d\dT[\d:]+Z)\s+\|\s+(\w+)\s+\|\s+([\w\-]+)\s+\|\s+([\w\-]+)\s+\|\s+([\w\-]+)\s+\|\s*(.*)$", line.strip())
    if not m:
        return None
    d = _kvs(m.group(6))
    d.update(level=m.group(2), service=m.group(3), trace_id=m.group(4), event=m.group(5))
    return m.group(1), d


ID_FIELDS_SKIP = {"instance", "pod", "region", "level", "service", "trace_id", "event", "pool", "dependency",
                  "route", "cache", "index", "detector", "connector_name", "archive_key", "file", "policy"}


def _line_ids(d: dict) -> set[str]:
    return extract_ids([v for k, v in d.items() if k not in ID_FIELDS_SKIP])


def _line_phones(d: dict) -> set[str]:
    return extract_phones([v for k, v in d.items() if k not in ID_FIELDS_SKIP])


def parse_log(path: Path, file_id: str, captured_at: str) -> list[Obs]:
    events: list[tuple[str, dict]] = []
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        parsed = _parse_pipe(line) or parse_log_line(line)
        if parsed:
            events.append(parsed)
    trace_ids: dict[str, set[str]] = {}
    trace_phones: dict[str, set[str]] = {}
    for ts, d in events:
        tr = d.get("trace_id")
        if not tr:
            continue
        trace_ids.setdefault(tr, set()).update(_line_ids(d))
        trace_phones.setdefault(tr, set()).update(_line_phones(d))
    out: list[Obs] = []
    for ts, d in events:
        ev = d.get("event", "")
        rid = d.get("resource_id", "")
        if not ev:
            continue
        tr = d.get("trace_id", "")
        ids = _line_ids(d) | (trace_ids.get(tr, set()) if tr else set())
        phones = _line_phones(d) | (trace_phones.get(tr, set()) if tr else set())
        src = f"log:{ev}"
        mk = lambda kind, state: _mk(file_id, ts, rid, kind, ids, phones, state, src)  # noqa: E731
        if ev == "record_lookup":
            o = ledger_to_obs(file_id, ts, d.get("system", ""), rid, d.get("record_type", ""), d.get("result", ""),
                              d.get("subject_reference", ""), src)
            if o:
                out.append(o)
            continue
        if not rid:
            continue
        if ev == "profile_lookup_completed" and prefix_of(rid) == "account":
            if d.get("service") not in ("account_service", "", None):
                continue  # e.g. profile_service replicas are not the account system of record
            rows = d.get("rows")
            present = (str(rows) not in ("0", "")) if rows is not None else d.get("http_status") == "200"
            if d.get("http_status") == "404":
                present = False
            out.append(mk("account", {"present": present, "service": d.get("service")}))
        elif ev == "index_query_completed":
            hit = d.get("result") == "hit" or (d.get("total_hits", "0") not in ("0", ""))
            out.append(mk("search", {"present": hit}))
        elif ev == "analytics_profile_read_completed":
            attrib = any(_nn(d.get(k)) for k in ("customer_reference", "email", "phone", "email_hash"))
            out.append(mk("analytics", {"attributable": attrib}))
        elif ev == "analytics_profile_read":
            attrib = (d.get("direct_identifiers", "0") not in ("0", "")) or any(_nn(d.get(k)) for k in ("email", "phone"))
            out.append(mk("analytics", {"attributable": attrib}))
        elif ev == "analytics_identity_scan":
            out.append(mk("analytics", {"attributable": True}))
        elif ev == "contact_lookup_completed":
            out.append(mk("marketing", {"present": d.get("rows", "1") not in ("0",), "opt_in": _truthy(d.get("email_opt_in"))}))
        elif ev == "support_ticket_read_completed":
            exposed = any(_nn(d.get(k)) for k in ("requester_name", "requester_email", "requester_phone"))
            out.append(mk("support_message", {"present": True, "exposed": exposed, "redacted": not exposed}))
        elif ev == "message_pii_scan":
            out.append(mk("support_message", {"present": True, "exposed": True, "redacted": False}))
        elif ev == "message_read":
            text = d.get("message_text", "")
            exposed = bool(EMAIL_RE.search(text) or PHONE_RE.search(text))
            out.append(mk("support_message", {"present": True, "exposed": exposed, "redacted": not exposed}))
        elif ev == "attachment_pii_scan":
            out.append(mk("support_attachment", {"present": True, "file": d.get("file")}))
        elif ev in ("connector_config_read_completed", "connector_config_loaded", "connector_heartbeat"):
            enabled = _truthy(d.get("enabled"))
            sched = _truthy(d.get("scheduler_registered"))
            active = bool(enabled) and (sched is not False)
            out.append(mk("data_flow", {"enabled": active, "delivery": d.get("last_delivery_status") or d.get("last_run")}))
        elif ev == "restore_policy_read_completed":
            out.append(mk("backup", {"restore_allowed": bool(_truthy(d.get("restore_allowed"))),
                                     "archive_present": _truthy(d.get("archive_present"))}))
        elif ev == "restore_authorization_completed":
            out.append(mk("backup", {"restore_allowed": d.get("decision", "deny").lower() == "allow"}))
        elif ev == "restore_candidate_evaluated":
            allowed = bool(_truthy(d.get("eligible"))) and not bool(_truthy(d.get("deletion_block")))
            out.append(mk("backup", {"restore_allowed": allowed}))
        elif ev == "export_download_check_completed":
            out.append(mk("export", {"present": True, "available": _truthy(d.get("available"))}))
        elif ev == "export_metadata_read_completed":
            out.append(mk("export", {"present": True, "available": _truthy(d.get("available"))}))
        elif ev == "audit_record_read_completed":
            out.append(mk("audit", {"present": d.get("rows", "1") not in ("0",), "event": d.get("event_code"),
                                    "retention_until": d.get("retention_until")}))
        elif ev == "contact_query_completed":
            out.append(mk("secondary_identifier", {"present": d.get("rows", "1") not in ("0",), "store": d.get("source")}))
        elif ev == "deletion_marker_check":
            pass  # corroborates a secondary-identifier hit; the hit itself is recorded by contact_query_completed
    return out


# --------------------------------------------------------------------------- text
LEDGER_LINE = re.compile(
    r"^-?\s*(\d{4}-\d\d-\d\dT[\d:]+Z)\s*\|\s*([\w]+)\s*\|\s*([\w]+)\s*\|\s*([\w]+)\s*\|\s*([\w]+)\s*\|\s*(.*?)\s*$")


def parse_text(path: Path, file_id: str, captured_at: str) -> list[Obs]:
    out: list[Obs] = []
    for line in path.read_text(errors="replace").splitlines():
        m = LEDGER_LINE.match(line.strip())
        if m:
            o = ledger_to_obs(file_id, m.group(1), m.group(2), m.group(3), m.group(4), m.group(5), m.group(6), "txt:handover")
            if o:
                out.append(o)
    return out


# --------------------------------------------------------------------------- documents
@dataclass
class DocText:
    file_id: str
    captured_at: str
    text: str
    kind: str  # pdf | image


def parse_pdf_text(path: Path) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
