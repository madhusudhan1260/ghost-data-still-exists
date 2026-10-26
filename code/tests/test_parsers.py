"""Log dialects, SQL snapshots and JSON layouts all normalize to the same observations."""
import json

from ghost_audit import parsers

LINES = {
    "kv": '2026-01-01T10:00:00Z level=INFO service=search_service trace_id=tr-1 resource_id=search_1 event=index_query_completed result=hit total_hits=1 email=a@x.org',
    "bracket": '2026-01-01T10:00:00Z [INFO] [search_service] [tr-1] index_query_completed: resource_id="search_1" result="hit" total_hits="1" email="a@x.org"',
    "pipe": '2026-01-01T10:00:00Z | INFO | search_service | tr-1 | index_query_completed | resource_id="search_1" result="hit" total_hits="1" email="a@x.org"',
    "cloud": '2026-01-01T10:00:00Z requestId=tr-1 level=INFO function=search_service message="resource_id=\\"search_1\\" event=\\"index_query_completed\\" result=\\"hit\\" total_hits=\\"1\\" email=\\"a@x.org\\""',
    "syslog": '2026-01-01T10:00:00Z <14>1 node-1 search_service tr-1 - - level=INFO resource_id="search_1" event="index_query_completed" result="hit" total_hits="1" email="a@x.org"',
    "json": '2026-01-01T10:00:00Z {"severity":"INFO","service":"search_service","trace_id":"tr-1","event":"index_query_completed","resource_id":"search_1","result":"hit","total_hits":"1","email":"a@x.org"}',
}


def test_every_log_dialect_yields_the_same_search_observation(tmp_path):
    for name, line in LINES.items():
        p = tmp_path / f"{name}.log"
        p.write_text(line + "\n")
        obs = parsers.parse_log(p, "evidence_01", "2026-01-01T10:00:01Z")
        assert len(obs) == 1, name
        assert (obs[0].rid, obs[0].kind, obs[0].state["present"], "a@x.org" in obs[0].ids) == ("search_1", "search", True, True), name


def test_trace_id_joins_lookup_key_to_resource(tmp_path):
    p = tmp_path / "t.log"
    p.write_text(
        '2026-01-01T10:00:00Z level=DEBUG service=account_service trace_id=tr-7 event=profile_lookup_started lookup_key=who@x.org\n'
        '2026-01-01T10:00:01Z level=INFO service=account_service trace_id=tr-7 resource_id=account_5 event=profile_lookup_completed rows=0 http_status=404\n')
    (o,) = parsers.parse_log(p, "evidence_01", "2026-01-01T10:00:01Z")
    assert o.rid == "account_5" and o.state["present"] is False and "who@x.org" in o.ids


def test_sql_snapshot_with_replicas(tmp_path):
    p = tmp_path / "s.sql"
    p.write_text("""BEGIN TRANSACTION;
CREATE TABLE customer_data_primary (resource_id TEXT PRIMARY KEY, origin_system TEXT, collection_name TEXT, record_json TEXT, updated_at TEXT);
CREATE TABLE customer_data_read_replica (resource_id TEXT PRIMARY KEY, origin_system TEXT, collection_name TEXT, record_json TEXT, updated_at TEXT);
INSERT INTO customer_data_read_replica VALUES('account_1','account_service','customer_profiles','{"resource_id":"account_1","customer_reference":"user_1","full_name":"A","email":"a@x.org","phone":null,"account_status":"ACTIVE","signup_channel":"x"}','2026-01-01T00:00:00Z');
COMMIT;""")
    obs = parsers.parse_sql(p, "evidence_01", "2026-01-01T00:00:00Z")
    assert [(o.rid, o.kind, o.state["present"]) for o in obs] == [("account_1", "account", True)]
    assert obs[0].source == "sql:customer_data_read_replica"  # present only on the replica -> replication drift is visible


def test_json_readback_and_ledger(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"exported_at": "2026-01-01T00:00:00Z", "service_exports": {
        "backup_service": {"restore_entries": [{"resource_id": "backup_1", "customer_email": "a@x.org", "customer_reference": None,
                                                "restore_allowed": True, "observed_at": "2026-01-01T00:00:00Z", "archive_key": "k"}]}}}))
    (o,) = parsers.parse_json(p, "evidence_01", "2026-01-01T00:00:00Z")
    assert o.kind == "backup" and o.state["restore_allowed"] is True
    q = tmp_path / "l.json"
    q.write_text(json.dumps({"exported_at": "2026-01-01T00:00:00Z", "origin": "o", "records": [
        {"recorded_at": "2026-01-01T00:00:00Z", "system": "analytics_platform", "resource_id": "analytics_1",
         "record_type": "ANALYTICS_PROFILE", "result": "ANONYMIZED", "subject_reference": "no direct identifier"}]}))
    (o,) = parsers.parse_json(q, "evidence_02", "2026-01-01T00:00:00Z")
    assert o.kind == "analytics" and o.state["attributable"] is False


def test_handover_text_table(tmp_path):
    p = tmp_path / "h.txt"
    p.write_text("- 2026-01-01T00:00:00Z | account_service | account_1 | USER_PROFILE | NOT_FOUND | A@X.ORG\n")
    (o,) = parsers.parse_text(p, "evidence_03", "2026-01-01T00:00:00Z")
    assert o.state["present"] is False and "a@x.org" in o.ids  # e-mails are case-normalized
