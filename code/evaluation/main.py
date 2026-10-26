"""Evaluation harness.

    python3 code/evaluation/main.py            # score the public sample, run synthetic cases, write reports

Scores the pipeline against dataset/sample_output.csv (case, step, field and sequence level),
runs the team-made SYNTHETIC scenarios in code/tests/scenarios.py, and renders
evaluation_report.md and usage_report.md from the measured numbers (nothing is typed in by hand
except the narrative sections kept in NARRATIVE below).
"""
from __future__ import annotations

import csv
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODE = HERE.parent
ROOT = CODE.parent
sys.path.insert(0, str(CODE))
sys.path.insert(0, str(CODE / "tests"))

from ghost_audit.auditor import Auditor  # noqa: E402
from ghost_audit.output import FIELDS, steps_json  # noqa: E402
from scenarios import SCENARIOS, run_scenario  # noqa: E402

KEY = ("phase", "system_id", "resource_id", "operation")


def load_expected(path: Path) -> dict[str, tuple[str, list[dict]]]:
    with open(path, newline="") as f:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(f)}


def lcs(a: list, b: list) -> int:
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a)):
        for j in range(len(b)):
            dp[i + 1][j + 1] = dp[i][j] + 1 if a[i] == b[j] else max(dp[i][j + 1], dp[i + 1][j])
    return dp[-1][-1]


def score(pred: dict[str, tuple[str, list[dict]]], exp: dict[str, tuple[str, list[dict]]], cases: list[str]) -> dict:
    tp_strict = tp_relaxed = n_pred = n_true = 0
    field_hits = Counter()
    field_total = 0
    state_hits = ev_hits = matched = 0
    seq_exact = seq_sets_equal = 0
    lcs_sum = 0.0
    rows_exact = 0
    status_ok = 0
    per_status = defaultdict(lambda: [0, 0])
    for c in cases:
        ps, psteps = pred[c]
        es, esteps = exp[c]
        status_ok += ps == es
        per_status[es][1] += 1
        per_status[es][0] += ps == es
        rows_exact += (ps == es and [{k: s[k] for k in FIELDS} for s in psteps] == esteps)
        strict_p = {tuple(s[k] for k in FIELDS[1:]) for s in psteps}
        strict_e = {tuple(s[k] for k in FIELDS[1:]) for s in esteps}
        rel_p = {tuple(s[k] for k in FIELDS[1:6]) for s in psteps}
        rel_e = {tuple(s[k] for k in FIELDS[1:6]) for s in esteps}
        tp_strict += len(strict_p & strict_e)
        tp_relaxed += len(rel_p & rel_e)
        n_pred += len(psteps)
        n_true += len(esteps)
        # field level: positionally aligned steps
        for i in range(max(len(psteps), len(esteps))):
            field_total += 1
            if i < len(psteps) and i < len(esteps):
                for k in FIELDS:
                    field_hits[k] += psteps[i][k] == esteps[i][k]
        # matched-by-key steps: do state / evidence agree?
        eidx = {tuple(s[k] for k in KEY): s for s in esteps}
        for s in psteps:
            e = eidx.get(tuple(s[k] for k in KEY))
            if e:
                matched += 1
                state_hits += s["state"] == e["state"]
                ev_hits += s["evidence_id"] == e["evidence_id"]
        # sequence
        pk = [tuple(s[k] for k in KEY) for s in psteps]
        ek = [tuple(s[k] for k in KEY) for s in esteps]
        seq_exact += pk == ek
        if set(pk) == set(ek):
            seq_sets_equal += 1
        denom = max(len(pk), len(ek))
        lcs_sum += (lcs(pk, ek) / denom) if denom else 1.0
    n = len(cases)
    prec = tp_strict / n_pred if n_pred else 1.0
    rec = tp_strict / n_true if n_true else 1.0
    prec_r = tp_relaxed / n_pred if n_pred else 1.0
    rec_r = tp_relaxed / n_true if n_true else 1.0
    f1 = lambda p, r: 2 * p * r / (p + r) if p + r else 0.0  # noqa: E731
    return {
        "cases": n, "status_acc": status_ok / n, "row_exact": rows_exact / n,
        "per_status": {k: (v[0], v[1]) for k, v in sorted(per_status.items())},
        "steps_pred": n_pred, "steps_true": n_true,
        "step_prec_strict": prec, "step_rec_strict": rec, "step_f1_strict": f1(prec, rec),
        "step_prec_rel": prec_r, "step_rec_rel": rec_r, "step_f1_rel": f1(prec_r, rec_r),
        "field_acc": {k: field_hits[k] / field_total for k in FIELDS}, "field_total": field_total,
        "matched": matched, "state_acc_matched": state_hits / matched if matched else 1.0,
        "evidence_acc_matched": ev_hits / matched if matched else 1.0,
        "seq_exact": seq_exact / n, "seq_given_same_steps": (seq_exact / seq_sets_equal) if seq_sets_equal else 1.0,
        "seq_lcs": lcs_sum / n,
    }


def diffs(pred, exp, cases):
    out = []
    for c in cases:
        ps, psteps = pred[c]
        es, esteps = exp[c]
        key = lambda s: (s["phase"], s["system_id"], s["resource_id"], s["operation"], s["evidence_id"])  # noqa: E731
        P, E = {key(s) for s in psteps}, {key(s) for s in esteps}
        if ps != es or P != E or [key(s) for s in psteps] != [key(s) for s in esteps]:
            out.append((c, ps, es, sorted(E - P), sorted(P - E)))
    return out


def selftest_account_linking(aud: Auditor, cases) -> dict:
    """Label-free validation of the activity-chain linker.

    Accounts that evidence links directly to a user are known without any activity. We hide them, re-derive them from
    `system_activity.csv` alone and count how often the linker recovers the same account id. `anchored` cases have other
    identity-linked resources with worker events; `unanchored` ones only have the account and an anonymized profile.
    """
    from ghost_audit.auditor import Work
    from ghost_audit.intent import authorizes_deletion
    import random

    def fresh():
        ws = []
        for c in cases:
            ok, why = authorizes_deletion(c.request_text)
            w = Work(c, ok, why)
            if ok:
                w.res = aud.idx.resolve(c.email, c.requested_at)
                if w.res.user:
                    aud._link(w)
            ws.append(w)
        return ws

    out = {}
    for label in ("anchored", "unanchored"):
        works = fresh()
        aud._learn_phase(works)
        truth = {}
        for w in works:
            if w.deletion and w.account_rid and any(a.rid == w.account_rid for a in aud.act.account_deletes):
                has = bool(aud._anchors(w))
                if has == (label == "anchored"):
                    truth[w.case.case_id] = w.account_rid
        ids = sorted(truth)
        if label == "anchored":
            random.Random(5).shuffle(ids)
            ids = ids[: len(ids) // 2]  # keep the other half as evidence-known claims, like a real run
        hidden = set(ids)
        for w in works:
            if w.case.case_id in hidden:
                w.account_rid, w.account_event = None, None
        aud._assign_accounts(works)
        ok = bad = none = 0
        for w in works:
            if w.case.case_id in hidden:
                if w.account_rid == truth[w.case.case_id]:
                    ok += 1
                elif w.account_rid is None:
                    none += 1
                else:
                    bad += 1
        out[label] = {"hidden": len(hidden), "recovered": ok, "wrong": bad, "unmatched": none}
    return out


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


NARRATIVE_IMPROVEMENTS = """\
| # | Failure found when scoring the public sample (measured) | Improvement implemented | Exact rows after |
|---|---|---|---|
| 0 | First end-to-end run | - | 12/32 |
| 1+2 | (1) Findings cited `evidence_02`, a bundle *exported* two days after `audit_at` (its inner snapshot is dated at audit time); (2) a positive record from an older snapshot beat a later HTTP 404 that carries no identifier (case_26 became INCOMPLETE) | (1) Citations must come from a file captured at or before `audit_at`; the inner snapshot time is still used for state. (2) After identity linking, every observation of the *same resource id* is joined into the timeline | 22/32 |
| 3 | Account-delete events that belong to the *other* public sample/test requests were taken for the target case's (case_32 picked the wrong account) | The public sample requests are loaded as linking context (never scored or emitted) | 23/32 |
| 4 | Hand-operated deletions (`Target resource:` activity template) were not linked; screenshots/PDFs were not parsed at all | Proximity linking restricted to that template; PDF text + OCR of screenshots bound to users through the data catalog (`LINK_RESOURCE_IDENTITY`) | 25/32 |
| 5 | A support-index replica copy (legacy contact index entry for a historical address) was ignored; an anonymized analytics profile of a hand-operated case was unlinked | `contact_query_completed` mapped to SECONDARY_IDENTIFIER (rule_02 DELETE); analytics pairing by proximity | 27/32 |
| 6 | Absent accounts with no row anywhere (case_01/02) had no citation | Cite the closest earlier snapshot whose scope covers `account_service` | 29/32 |
| 7 | Greedy account linking let a coincidental phase match steal another case's account (self-test that hides 92 evidence-known accounts and re-derives them from activity alone: 60 recovered, 15 wrong, 17 unmatched) | Likelihood phase model per (side, action type) + optimal (Hungarian) assignment per connected component; proximity rule limited to the manual template (same self-test: 91 recovered, 0 wrong, 1 unmatched) | 29/32 |
"""

NARRATIVE_LIMITS = """\
* **Identifiers that exist nowhere in participant-visible data.** `audit_record_6645` (case_18) and `audit_record_5569` (case_20) appear only in `sample_output.csv`; `account_004` (case_04) has no activity row or evidence row. The pipeline cannot emit ids it cannot observe, so those three sample rows can never match exactly. The same limitation applies to {no_audit} of the {n_req} hidden-set predictions that contain no `security_service` verification step (no audit record for the user is present at or before `audit_at`).
* **Chain linking is statistical for unanchored cases.** A completed case that consists only of an absent account, an audit record and an anonymized analytics profile has no identity-bearing resource to anchor on; its account is chosen by the learned phase model plus the optimal assignment. {ambiguous} of the {n_req} requests are flagged "among N equally plausible deletion events" in the run notes and may be wrong.
* **Hand-built scenarios.** The twelve screenshot/PDF cases rely on OCR (Apple Vision, cached) and on catalog links; the correct answers for their account ids and secondary artefacts cannot be validated beyond the ten public hand-built samples.
* **Heuristic intent classifier.** Language coverage is en/es/pt/fr/ja/ko plus explicit negations; it matches all 32 public requests and isolates 10 non-deletion requests in the 518, but an unseen phrasing could be misclassified. No LLM fallback is used.
* **No hidden-set accuracy is claimed.** All accuracy numbers above are on the 32 public samples, which were also used while developing the pipeline (the `Exact rows after` column shows that tuning). The synthetic tests are independent of the dataset but were written by the same team.
"""


def render_eval_report(gen: dict, g_all: dict, g_gen: dict, g_hand: dict, d_list: list, synth_rows: list, extra: dict) -> str:
    L = []
    L.append("# Evaluation report - Ghost Data Still Exists\n")
    L.append(f"_Generated by `python3 code/evaluation/main.py` on {time.strftime('%Y-%m-%d %H:%M')} from the final pipeline "
             f"(the same code that produced the submitted `output.csv`)._\n")
    L.append("## 1. How the pipeline was scored\n")
    L.append("The 32 rows of `dataset/sample_output.csv` are the only labelled data, so they are the only real accuracy "
             "measurement. Predictions for them are produced by the same deterministic code path as the 518 hidden requests "
             "(the 518 requests are loaded as linking context, never scored). Comparisons are exact: JSON is parsed, object-key "
             "order and whitespace are ignored, array order is not. No fuzzy matching hides errors.\n")
    L.append("## 2. Results on the public sample (32 cases)\n")
    L.append("| Metric | All 32 | Generic cases 11-32 (22) | Hand-built cases 01-10 (10) |\n|---|---|---|---|")
    rows = [
        ("Case-level status accuracy", "status_acc", pct), ("Rows exactly equal (status + full step array)", "row_exact", pct),
        ("Step precision (phase, system, resource, operation, state, evidence)", "step_prec_strict", pct),
        ("Step recall (same)", "step_rec_strict", pct), ("Step F1 (same)", "step_f1_strict", pct),
        ("Step F1 ignoring `evidence_id`", "step_f1_rel", pct),
        ("`state` correct on steps matched by key", "state_acc_matched", pct),
        ("`evidence_id` correct on steps matched by key", "evidence_acc_matched", pct),
        ("Sequence: rows whose ordered step keys are identical", "seq_exact", pct),
        ("Sequence: order correct when the step sets are equal", "seq_given_same_steps", pct),
        ("Sequence: mean LCS ratio of ordered step keys", "seq_lcs", pct),
    ]
    for label, k, f in rows:
        L.append(f"| {label} | {f(g_all[k])} | {f(g_gen[k])} | {f(g_hand[k])} |")
    L.append("")
    L.append("Status accuracy by true status (correct/total): " + ", ".join(f"{k} {a}/{b}" for k, (a, b) in g_all["per_status"].items()) + ".\n")
    L.append("### Field-level accuracy (steps aligned by position, all 32 cases)\n")
    L.append("| " + " | ".join(FIELDS) + " |\n|" + "---|" * len(FIELDS))
    L.append("| " + " | ".join(pct(g_all["field_acc"][k]) for k in FIELDS) + " |\n")
    L.append(f"Positions compared: {g_all['field_total']} (a missing or extra step counts as a miss for every field). "
             f"Steps predicted: {g_all['steps_pred']}, expected: {g_all['steps_true']}, matched by (phase, system, resource, operation): {g_all['matched']}.\n")
    L.append("## 3. Differences that remain on the public sample\n")
    if not d_list:
        L.append("None.\n")
    else:
        for c, ps, es, missing, extra_ in d_list:
            L.append(f"* **{c}** - predicted {ps}, expected {es}; missing {len(missing)} step(s), extra {len(extra_)} step(s)"
                     + (": missing " + "; ".join(f"{m[3]} {m[2]}" for m in missing[:4]) if missing else "")
                     + (" | extra " + "; ".join(f"{m[3]} {m[2]} [{m[4] or '-'}]" for m in extra_[:3]) if extra_ else ""))
        L.append("")
    L.append("## 4. Additional evaluation cases created by the team (SYNTHETIC)\n")
    L.append("These are fictional datasets (people, ids and timestamps invented) that follow the real file schemas. They are built "
             "by `code/tests/synth.py`, defined in `code/tests/scenarios.py`, and run through the production pipeline; they are **not** "
             "real dataset evidence and are not mixed into the numbers above.\n")
    L.append("| Scenario | Purpose | Result |\n|---|---|---|")
    for name, purpose, ok, detail in synth_rows:
        L.append(f"| `{name}` | {purpose} | {'PASS' if ok else 'FAIL'} |")
    L.append("")
    L.append(f"Result: {sum(1 for r in synth_rows if r[2])}/{len(synth_rows)} synthetic scenarios pass. In addition `pytest code/tests` runs "
             f"{extra['pytest']} (identity resolution incl. a recycled address, multilingual intent, policy per rule, timeline/"
             "post-audit evidence, six log dialects + SQL replicas + JSON layouts, step ordering, validator errors for bad operation/state, unknown or late evidence ids, "
             "wrong case set, JSON escaping and empty arrays, Hungarian assignment).\n")
    L.append("## 5. Failures found and improvements made\n")
    L.append(NARRATIVE_IMPROVEMENTS)
    L.append("Other defects found and fixed while building (all covered by tests): recycled e-mail addresses were claimed by two users "
             "(ownership is now temporal); tombstones must be redacted, never deleted; a log from a non-account service (`profile_service`, 300 decoy "
             "lookups) was counted as account evidence; identity linking via old phone numbers and old e-mail hashes; replicas that "
             "still hold a record the primary has dropped are reported (replication drift); evidence captured after `audit_at` "
             "(including 14 hidden-set cases where an account is recreated after the audit) never changes the answer.\n")
    st_ = extra.get("selftest")
    if st_:
        L.append("### Label-free self-test of the activity-chain linker (computed by this script)\n")
        L.append("Accounts that the evidence links directly to a user are hidden and re-derived from `system_activity.csv` alone "
                 "(all 550 public+hidden requests take part in the global assignment, no labels involved):\n")
        L.append("| Group | Accounts hidden | Recovered | Wrong | Unmatched |\n|---|---|---|---|---|")
        for k, v in st_.items():
            L.append(f"| {k} | {v['hidden']} | {v['recovered']} | {v['wrong']} | {v['unmatched']} |")
        L.append("")
    L.append("## 6. Hidden-set run (518 requests) - structural checks only\n")
    L.append(f"* Rows: {extra['rows']} (header `case_id,deletion_status,steps_json`), sorted by `case_id`, one row per request.\n"
             f"* Status mix: {extra['status_counts']}.\n"
             f"* `output.csv` is re-validated on every run (allowed operation/state pairs, consecutive `step_no`, phase and within-phase order, "
             f"evidence ids exist and are not captured after `audit_at`, future steps carry `\"\"`): "
             f"{'0 problems' if extra['validation'] == 0 else str(extra['validation']) + ' problems'}.\n"
             f"* All {extra['evidence_files']} evidence files parsed with {extra['parse_failures']} failures "
             f"(SQL executed with sqlite3, PDFs with pdfplumber, 11 screenshots by OCR).\n"
             "* No accuracy is claimed for these rows because their labels are hidden.\n")
    L.append("## 7. Remaining limitations\n")
    st = extra.get("stats") or {}
    L.append(NARRATIVE_LIMITS.format(no_audit=st.get("no_audit_record_rows", "n/a"), n_req=st.get("requests", "n/a"),
                                     ambiguous=st.get("ambiguous_account_links", "n/a")))
    return "\n".join(L) + "\n"


def render_usage_report(stats: dict) -> str:
    sc = stats["status_counts"]
    return f"""# Token usage and cost - final full-dataset run

This is the run that produced the submitted `output.csv` (`python3 code/main.py`, recorded in `code/evaluation/run_stats.json`).

## Result

**No language model was used.** The solution is a deterministic Python pipeline, so for this run:

| Measure | Value |
|---|---|
| Model providers | none |
| Model names | none |
| Model calls | {stats['model_calls']} |
| Input tokens | {stats['input_tokens']} |
| Output tokens | {stats['output_tokens']} |
| Total tokens | {stats['input_tokens'] + stats['output_tokens']} |
| Average tokens per request | 0 ({stats['requests']} requests) |
| Estimated total cost | ${stats['estimated_cost_usd']:.2f} |
| Estimated cost per request | $0.00 |
| Per-model totals | n/a (no models) |

The zero values are measured, not estimated: `main.py` contains no API client and reads no API keys.

## What did run

| Item | Value |
|---|---|
| Requests processed | {stats['requests']} (`{stats['requests_file']}`) |
| Statuses produced | COMPLETE {sc.get('COMPLETE', 0)}, INCOMPLETE {sc.get('INCOMPLETE', 0)}, NOT_APPLICABLE {sc.get('NOT_APPLICABLE', 0)} |
| Steps emitted | {stats['steps_total']} |
| Evidence files parsed | {stats['evidence_files']} (failures: {len(stats['evidence_parse_failures'])}) |
| Observations extracted | {stats['observations']} |
| Wall-clock time of the run | {stats['elapsed_seconds']} s (single process, OCR served from cache) |
| OCR | {len(stats['ocr_files'])} screenshots; engine Apple Vision (on-device, bundled Swift helper `ghost_audit/ocr/vision_ocr.swift`), transcripts cached in `code/cache/ocr_cache.json`; no network, no tokens |

## Why no LLM, and where one could help

The task contract (closed set of operations/states, strict ordering, exact ids) is better served by deterministic rules: every id,
timestamp and citation in the output is copied from a parsed record, so nothing can be invented. An LLM would only help for
(1) free-text intent in languages beyond the six covered, (2) reading screenshots on machines without an OCR engine, and
(3) explaining findings to a human. If added, calls would be cached by content hash, constrained to a JSON schema, validated by
`ghost_audit/output.py::validate_output`, and their provider/model/tokens/cost logged in this file; keys would come from environment variables only.
"""


def main() -> int:
    ds = ROOT / "dataset"
    if not (ds / "sample_output.csv").exists():
        print("dataset/ not found - run from a checkout that contains the dataset", file=sys.stderr)
        return 2
    aud = Auditor(ds)
    sample = aud.load_cases(ds / "sample_deletion_requests.csv")
    ctx = aud.load_cases(ds / "deletion_requests.csv")
    decisions = aud.audit_all(sample, ctx)
    pred = {d.case.case_id: (d.status, json.loads(steps_json(d))) for d in decisions}
    exp = load_expected(ds / "sample_output.csv")
    ids = sorted(exp)
    gen = [c for c in ids if int(c.split("_")[1]) >= 11]
    hand = [c for c in ids if int(c.split("_")[1]) < 11]
    g_all, g_gen, g_hand = score(pred, exp, ids), score(pred, exp, gen), score(pred, exp, hand)
    d_list = diffs(pred, exp, ids)
    synth_rows = []
    for sc in SCENARIOS:
        _, probs = run_scenario(sc)
        synth_rows.append((sc.name, sc.purpose, not probs, probs))
    import subprocess
    try:
        out = subprocess.run([sys.executable, "-m", "pytest", "-q", str(CODE / "tests")], capture_output=True, text=True, cwd=ROOT, timeout=600).stdout
        pytest_line = [l for l in out.splitlines() if "passed" in l or "failed" in l][-1].strip()
    except Exception as e:  # noqa: BLE001
        pytest_line = f"pytest not run ({e})"
    stats = json.loads((HERE / "run_stats.json").read_text()) if (HERE / "run_stats.json").exists() else None
    from ghost_audit.output import validate_output
    out_csv = ROOT / "output.csv"
    ids_req = [c.case_id for c in ctx]
    problems = validate_output(out_csv, ids_req, set(aud.inv.files), {k: v.captured_at for k, v in aud.inv.files.items()},
                               {c.case_id: c.audit_at for c in ctx}) if out_csv.exists() else ["output.csv missing"]
    rows = sum(1 for _ in open(out_csv)) - 1 if out_csv.exists() else 0
    selftest = selftest_account_linking(aud, sample + ctx)
    extra = {"selftest": selftest, "pytest": pytest_line, "rows": rows, "status_counts": (stats or {}).get("status_counts"), "validation": len(problems),
             "evidence_files": len(aud.inv.files), "parse_failures": len(aud.inv.report.failures), "stats": stats}
    reports = {"evaluation_report.md": render_eval_report({}, g_all, g_gen, g_hand, d_list, synth_rows, extra)}
    if stats:
        reports["usage_report.md"] = render_usage_report(stats)
    # AGENTS.md asks for evaluation/*.md inside code.zip, the starter README for code/evaluation/*.md:
    # the same generated text is written to both places so the two instructions are both satisfied.
    top = ROOT / "evaluation"
    top.mkdir(exist_ok=True)
    for name, text in reports.items():
        (HERE / name).write_text(text)
        (top / name).write_text(text)
    print(json.dumps({"all": {k: v for k, v in g_all.items() if not isinstance(v, dict)}, "synthetic": [(r[0], r[2]) for r in synth_rows],
                      "pytest": pytest_line, "validation_problems": len(problems)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
