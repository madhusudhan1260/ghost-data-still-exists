"""Does the request text actually authorize deletion?

Deterministic, multilingual (en/es/pt/fr/ja/ko) lexicon classifier. Explicit
negations and non-deletion intents (access, export, correction, marketing opt-out,
"keep my account") always win over deletion vocabulary, because the specification
says a non-deletion request is NOT_APPLICABLE "even if other fields resemble a
deletion case".
"""
from __future__ import annotations

import re
import unicodedata

NON_DELETION = [
    r"not asking for deletion", r"not (?:a )?request to (?:erase|delete)", r"do not close", r"don'?t close",
    r"don'?t delete", r"do not delete", r"please keep the account", r"keep (?:my|the) account",
    r"still (?:need|want|use)", r"leave my (?:profile|account)", r"without removing", r"leave the account active",
    r"marketing lists only", r"stop the promotional", r"promotional emails", r"unsubscribe", r"opt[- ]?out",
    r"(?:a )?copy of (?:last|the information|my (?:data|information|account))", r"download my", r"portable format", r"what personal information", r"information request",
    r"out of date", r"misspelled", r"correct it", r"change the email", r"replace it with", r"resetting the login",
    r"reset(?:ting)? (?:my )?password", r"can'?t access", r"continue using", r"don'?t want (?:to )?(?:delete|close)",
    r"no quiero (?:eliminar|borrar)", r"ne (?:supprimez|veux pas)", r"n[ãa]o (?:quero )?(?:excluir|apagar)",
    r"削除しないで", r"削除は(?:不要|希望しません)", r"삭제하지 (?:마|말)",
]
DELETION = [
    r"\bdelet", r"\berase", r"\berasure", r"\bremov", r"\bgone\b", r"\btraces\b", r"still existed", r"profile is gone",
    r"\bclose (?:my|this|the) account", r"closed\b", r"\bclose the account", r"permanently deleted",
    r"personal[- ]data deletion", r"deletion",
    r"eliminaci[oó]n", r"eliminar", r"borrar", r"suppression", r"supprimer", r"exclus[aã]o", r"excluir",
    r"削除", r"消去", r"삭제", r"del[ée]te",
]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    return s.replace("’", "'")


def authorizes_deletion(text: str) -> tuple[bool, str]:
    t = _norm(text)
    for pat in NON_DELETION:
        m = re.search(pat, t)
        if m:
            return False, f"non-deletion intent: '{m.group(0)}'"
    for pat in DELETION:
        m = re.search(pat, t)
        if m:
            return True, f"deletion vocabulary: '{m.group(0)}'"
    return False, "no deletion request found in text"
