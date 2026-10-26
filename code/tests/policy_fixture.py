import tempfile
from pathlib import Path

from ghost_audit.policy import PolicyEngine
from synth import Synth

_cache = {}


def engine() -> PolicyEngine:
    if "e" not in _cache:
        d = Path(tempfile.mkdtemp())
        Synth(d).write()
        _cache["e"] = PolicyEngine(d / "deletion_rules.csv")
    return _cache["e"]
