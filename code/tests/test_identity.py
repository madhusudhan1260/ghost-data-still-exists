"""Identity resolution (synthetic data)."""
from ghost_audit.identity import IdentityIndex
from synth import Synth


def _index(tmp_path, build):
    s = Synth(tmp_path)
    build(s)
    s.write()
    return IdentityIndex(tmp_path)


def test_current_and_historical_email_resolve_to_same_user(tmp_path):
    def b(s):
        s.user("user_1", "A", "new@x.org", "+1 555 0001", history=[("old@x.org", "2026-01-10T00:00:00Z")])
    idx = _index(tmp_path, b)
    cur = idx.resolve("NEW@x.org", "2026-02-01T00:00:00Z")
    old = idx.resolve("old@x.org", "2026-02-01T00:00:00Z")
    assert cur.user.user_id == old.user.user_id == "user_1"
    assert cur.via == "current" and old.via == "historical"
    assert old.audit_ids  # the audit event that proves the historical match is recorded


def test_hash_of_historical_email_is_known(tmp_path):
    import hashlib

    def b(s):
        s.user("user_1", "A", "new@x.org", "+1 555 0001", history=[("old@x.org", "2026-01-10T00:00:00Z")])
    idx = _index(tmp_path, b)
    assert hashlib.sha256(b"old@x.org").hexdigest() in idx.users["user_1"].hashes


def test_recycled_address_is_not_merged(tmp_path):
    # user_1 owned shared@x.org until 2026-03-01; afterwards user_2 holds it.
    def b(s):
        s.user("user_1", "A", "a.new@x.org", "+1 555 0001", history=[("shared@x.org", "2026-03-01T00:00:00Z")])
        s.users.append(["user_2", "B", "shared@x.org", "+1 555 0002", "0" * 64, "2026-03-02T00:00:00Z"])
    idx = _index(tmp_path, b)
    before = idx.resolve("shared@x.org", "2026-02-01T00:00:00Z")
    after = idx.resolve("shared@x.org", "2026-04-01T00:00:00Z")
    assert before.user.user_id == "user_1" and before.via == "historical"
    assert after.user.user_id == "user_2" and after.via == "current"


def test_unknown_email_is_not_fabricated(tmp_path):
    idx = _index(tmp_path, lambda s: s.user("user_1", "A", "a@x.org", "+1 555 0001"))
    r = idx.resolve("nobody@x.org", "2026-01-01T00:00:00Z")
    assert r.user is None and r.via == "none"


def test_same_name_different_user_not_linked(tmp_path):
    def b(s):
        s.user("user_1", "Nils Lund", "nils.l@x.org", "+46 70 1")
        s.user("user_2", "Nils Lund", "nils.lund@x.org", "+46 70 2")
    idx = _index(tmp_path, b)
    assert idx.resolve("nils.l@x.org", "2026-01-01T00:00:00Z").user.user_id == "user_1"
    assert idx.users["user_1"].identifiers().isdisjoint(idx.users["user_2"].identifiers() - {"user_2"})
