"""Small shared helpers: timestamps, identifier extraction, resource-id typing."""
from __future__ import annotations

import datetime as dt
import re
from typing import Iterable, Optional

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
USER_RE = re.compile(r"\buser_\d+\b")
HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")
PHONE_RE = re.compile(r"\+\d[\d ()\-]{6,}\d")
RID_RE = re.compile(
    r"\b(?:account|search|support|analytics|export|data_flow|backup|audit_record|"
    r"tombstone|legal_hold|marketing|object|email_delivery|cache)_[A-Za-z0-9]+\b"
)

# resource-id prefix -> owning system
PREFIX_SYSTEM = {
    "account": "account_service",
    "search": "search_service",
    "support": "support_platform",
    "analytics": "analytics_platform",
    "export": "export_service",
    "data_flow": "integration_service",
    "backup": "backup_service",
    "audit_record": "security_service",
    "tombstone": "identity_service",
    "legal_hold": "legal_service",
    "marketing": "marketing_platform",
    "object": "object_storage",
    "email_delivery": "email_delivery_service",
    "cache": "cache_service",
}


def prefix_of(rid: str) -> str:
    for p in sorted(PREFIX_SYSTEM, key=len, reverse=True):
        if rid.startswith(p + "_"):
            return p
    return rid.split("_", 1)[0]


def system_of(rid: str) -> Optional[str]:
    return PREFIX_SYSTEM.get(prefix_of(rid))


def parse_ts(s: Optional[str]) -> Optional[dt.datetime]:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def norm_email(s: str) -> str:
    return s.strip().lower()


def norm_phone(s: str) -> str:
    return re.sub(r"\D", "", s)


def extract_ids(values: Iterable[object]) -> set[str]:
    """Pull emails (lower-cased), user ids, sha256 hashes out of arbitrary strings."""
    out: set[str] = set()
    for v in values:
        if not isinstance(v, str):
            continue
        for m in EMAIL_RE.findall(v):
            out.add(norm_email(m))
        for m in USER_RE.findall(v):
            out.add(m)
        for m in HASH_RE.findall(v.lower()):
            out.add(m)
    return out


def extract_phones(values: Iterable[object]) -> set[str]:
    out: set[str] = set()
    for v in values:
        if isinstance(v, str):
            for m in PHONE_RE.findall(v):
                out.add(norm_phone(m))
    return out
