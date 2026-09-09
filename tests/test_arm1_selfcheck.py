#!/usr/bin/env python3
"""Local regression tests for Arm 1 (pure self-check).

NO model/API calls anywhere: evaluation is driven by fake clients.
The real Stage 0 frozen ledger is read locally (read-only) as the
approved source corpus; baseline.jsonl is NEVER read.

Run:  python3 tests/test_arm1_selfcheck.py   (from benchmark_v2/)
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as root_config
from arms.selfcheck import config as acfg
import arms.selfcheck.run_selfcheck as rs
import arms.selfcheck.validate_selfcheck as vs


# ── Fakes / helpers ───────────────────────────────────────────────────

FAKE_FINGERPRINT = "fp_arm1_fake"
FAKE_CREATED = 1777100000


class RawCompletions:
    def __init__(self, resp_fn):
        self.resp_fn = resp_fn
        self.calls = 0
        self.kwargs_seen = []

    def create(self, **kwargs):
        self.calls += 1
        self.kwargs_seen.append(kwargs)
        result = self.resp_fn(kwargs)
        if isinstance(result, Exception):
            raise result
        return result


def make_raw_client(resp_fn):
    comp = RawCompletions(resp_fn)
    return SimpleNamespace(chat=SimpleNamespace(completions=comp)), comp


def fake_response(text):
    return SimpleNamespace(
        id="fake-arm1-resp", model=acfg.EVALUATOR_MODEL,
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=text), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=500, completion_tokens=1),
        system_fingerprint=FAKE_FINGERPRINT, created=FAKE_CREATED,
        service_tier="default", _request_id="req_arm1_fake",
    )


def text_client(text):
    return make_raw_client(lambda kw: fake_response(text))


def silent_log(event, **fields):
    pass


def tmp_paths(tmpdir):
    d = Path(tmpdir)
    return rs.Paths(responses=d / "responses.jsonl", decisions=d / "decisions.jsonl",
                    failures=d / "failures.jsonl", log=d / "log.jsonl")


def real_frozen():
    return rs.load_frozen_records(acfg.FROZEN_SOURCE_PATH)


def fabricate_arm_ledgers(paths, n=164, verdict_fn=None, mutate_resp=None,
                          mutate_dec=None):
    """Write n consistent response+decision records from the REAL frozen
    ledger, without any API call."""
    tasks = real_frozen()[:n]
    for t in tasks:
        text = verdict_fn(t) if verdict_fn else "YES"
        res = rs.EvalResult(
            text=text, response_id=f"fake-{t['task_id']}",
            response_model=acfg.EVALUATOR_MODEL, finish_reason="stop",
            prompt_tokens=500, completion_tokens=1, duration_s=0.1,
            transport_attempts=1, system_fingerprint=FAKE_FINGERPRINT,
            response_created=FAKE_CREATED, service_tier="default",
            request_id="req_fake")
        prompt = rs.build_evaluator_prompt(t["prompt"], t["candidate_code"])
        rr = rs.build_response_record(t, res, prompt)
        if mutate_resp:
            rr = mutate_resp(t, rr)
        rs.append_jsonl(paths.responses, rr)
        dr = rs.build_decision_record(t, rr)
        if mutate_dec:
            dr = mutate_dec(t, dr)
        rs.append_jsonl(paths.decisions, dr)


def run_validator(paths):
    argv = sys.argv
    sys.argv = ["validate_selfcheck.py", "--responses", str(paths.responses),
                "--decisions", str(paths.decisions)]
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = vs.main()
    finally:
        sys.argv = argv
    return rc, out.getvalue()


# ── Corpus integrity ──────────────────────────────────────────────────

class TestCorpusIntegrity(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="arm1_ci_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

    def test_approved_source_hash_accepted(self):
        rs.verify_source_ledger()  # must not raise on the real corpus

    def test_wrong_source_hash_aborts_before_any_client(self):
        tampered = Path(self.tmpdir) / "frozen_tampered.jsonl"
        data = Path(acfg.FROZEN_SOURCE_PATH).read_text()
        tampered.write_text(data + " ")  # one extra byte
        client_called = []

        def factory():
            client_called.append(True)
            raise AssertionError("client factory must never be reached")

        with self.assertRaises(SystemExit):
            rs.verify_source_ledger(tampered)
        # and main() verifies before constructing any client:
        argv = ["--responses", str(Path(self.tmpdir) / "r.jsonl"),
                "--decisions", str(Path(self.tmpdir) / "d.jsonl"),
                "--failures", str(Path(self.tmpdir) / "f.jsonl"),
                "--log", str(Path(self.tmpdir) / "l.jsonl")]
        real_verify = rs.verify_source_ledger
        rs.verify_source_ledger = lambda: real_verify(tampered)
        try:
            with self.assertRaises(SystemExit):
                rs.main(argv, client_factory=factory)
        finally:
            rs.verify_source_ledger = real_verify
        self.assertEqual(client_called, [])

    def test_wrong_candidate_hash_aborts(self):
        tasks = real_frozen()[:2]
        tasks[1]["candidate_sha256"] = "0" * 64
        bad = Path(self.tmpdir) / "frozen_badcand.jsonl"
        # re-derive the file hash check bypass: write a ledger whose
        # candidate hash doesn't match its candidate_code.
        with open(bad, "w") as f:
            for t in tasks:
                rec = {"task_id": t["task_id"], "task_index": t["task_index"],
                       "prompt": t["prompt"], "candidate_code": t["candidate_code"],
                       "candidate_sha256": t["candidate_sha256"]}
                f.write(json.dumps(rec) + "\n")
        with self.assertRaises(SystemExit):
            rs.load_frozen_records(bad)

    def test_163_cannot_validate_as_full_arm(self):
        paths = tmp_paths(self.tmpdir)
        fabricate_arm_ledgers(paths, n=163)
        rc, out = run_validator(paths)
        self.assertEqual(rc, 1)
        self.assertIn("Frozen evaluator responses: 163", out)
        self.assertIn("VALID ARM 1 SELFCHECK: NO", out)


# ── Ground-truth blindness ────────────────────────────────────────────

class TestGroundTruthBlindness(unittest.TestCase):

    def test_no_arm_source_references_ground_truth(self):
        """Arm 1 runtime modules must not reference baseline.jsonl or
        ground-truth fields outside comments/docstrings stating absence."""
        import re
        arm_dir = Path(acfg.ARM_DIR)
        for py in arm_dir.glob("*.py"):
            text = py.read_text()
            for lineno, line in enumerate(text.splitlines(), 1):
                code = line.split("#", 1)[0]
                # docstrings are allowed to state blindness; check only
                # actual open/read/import statements and field accesses.
                if re.search(r"""(open|read_text|read_bytes|load_jsonl)\s*\(""",
                             code):
                    self.assertNotIn("baseline.jsonl", code,
                                     f"{py}:{lineno}: {line}")
                    self.assertNotIn("BASELINE_PATH", code,
                                     f"{py}:{lineno}: {line}")

    def test_runner_needs_no_baseline_file(self):
        """Full runner pass succeeds using only the frozen ledger; no
        baseline.jsonl is consulted (it is absent from the tmp paths and
        never opened)."""
        tmpdir = tempfile.mkdtemp(prefix="arm1_blind_")
        self.addCleanup(shutil.rmtree, tmpdir, True)
        paths = tmp_paths(tmpdir)
        tasks = real_frozen()[:3]
        client, comp = text_client("YES")
        rc = rs.run(paths, client, tasks, cap_usd=10.0, log=silent_log)
        self.assertEqual(rc, 0)
        self.assertEqual(comp.calls, 3)

    def test_evaluator_prompt_contains_no_ground_truth(self):
        tasks = real_frozen()[:5]
        for t in tasks:
            p = rs.build_evaluator_prompt(t["prompt"], t["candidate_code"])
            for forbidden in ("baseline_correct", "test_status", "test_details",
                              "PASS", "AssertionError", "def check("):
                self.assertNotIn(forbidden, p, f"{t['task_id']}: {forbidden}")


# ── Exact request ─────────────────────────────────────────────────────

class TestExactRequest(unittest.TestCase):

    def test_request_parameters_and_single_user_message(self):
        tmpdir = tempfile.mkdtemp(prefix="arm1_req_")
        self.addCleanup(shutil.rmtree, tmpdir, True)
        paths = tmp_paths(tmpdir)
        tasks = real_frozen()[:1]
        client, comp = text_client("NO")
        rc = rs.run(paths, client, tasks, cap_usd=10.0, log=silent_log)
        self.assertEqual(rc, 0)
        self.assertEqual(comp.calls, 1)
        kw = comp.kwargs_seen[0]
        self.assertEqual(kw["model"], "gpt-5.4-2026-03-05")
        self.assertEqual(kw["temperature"], 0)
        self.assertEqual(kw["reasoning_effort"], "none")
        self.assertEqual(kw["max_completion_tokens"], 16)
        self.assertEqual(len(kw["messages"]), 1)
        self.assertEqual(kw["messages"][0]["role"], "user")
        # prompt is the exact template instantiation
        expected = rs.build_evaluator_prompt(tasks[0]["prompt"],
                                             tasks[0]["candidate_code"])
        self.assertEqual(kw["messages"][0]["content"], expected)


# ── Verdict parser ────────────────────────────────────────────────────

class TestVerdictParser(unittest.TestCase):

    def test_accepts(self):
        self.assertEqual(rs.parse_verdict("YES"), ("YES", 1, "valid"))
        self.assertEqual(rs.parse_verdict(" yes "), ("YES", 1, "valid"))
        self.assertEqual(rs.parse_verdict("NO"), ("NO", 0, "valid"))
        self.assertEqual(rs.parse_verdict("\nno\n"), ("NO", 0, "valid"))

    def test_rejects(self):
        for bad in ("YES.", "NO because the loop is wrong", "MAYBE",
                    '{"verdict":"YES"}', "", "YES NO", "I think YES", "yes\nno"):
            v, a, ps = rs.parse_verdict(bad)
            self.assertIsNone(v, bad)
            self.assertIsNone(a, bad)
            self.assertEqual(ps, "invalid_verdict", bad)


# ── Freeze-before-parse ───────────────────────────────────────────────

class TestFreezeBeforeParse(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="arm1_fbp_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.paths = tmp_paths(self.tmpdir)
        self.tasks = real_frozen()[:2]

    def test_parse_crash_then_resume_no_resample(self):
        client, comp = text_client("YES")
        real_parse = rs.parse_verdict

        def crash_parse(raw):
            raise RuntimeError("simulated parse crash")

        rs.parse_verdict = crash_parse
        try:
            with self.assertRaises(RuntimeError):
                rs.run(self.paths, client, self.tasks, cap_usd=10.0,
                       log=silent_log)
        finally:
            rs.parse_verdict = real_parse
        self.assertEqual(comp.calls, 1)  # one response frozen before crash

        responses = rs.validate_response_ledger(self.paths.responses)
        self.assertEqual(len(responses), 1)
        tid = self.tasks[0]["task_id"]
        raw_hash_1 = responses[tid]["raw_evaluator_response_sha256"]

        # Resume: parsing must reuse the frozen response, no new call.
        rc = rs.run(self.paths, client, self.tasks, cap_usd=10.0,
                    log=silent_log)
        self.assertEqual(rc, 0)
        self.assertEqual(comp.calls, 2, "frozen response was resampled!")
        responses2 = rs.validate_response_ledger(self.paths.responses)
        self.assertEqual(raw_hash_1,
                         responses2[tid]["raw_evaluator_response_sha256"])
        decisions = rs.load_decisions(self.paths.decisions)
        self.assertEqual(decisions[tid]["verdict"], "YES")
        self.assertEqual(decisions[tid]["acceptance"], 1)

    def test_missing_decisions_ledger_reparses_without_resample(self):
        client, comp = text_client("NO")
        rc = rs.run(self.paths, client, self.tasks, cap_usd=10.0,
                    log=silent_log)
        self.assertEqual(rc, 0)
        self.assertEqual(comp.calls, 2)
        self.paths.decisions.unlink()  # decisions lost; responses frozen
        rc = rs.run(self.paths, client, self.tasks, cap_usd=10.0,
                    log=silent_log)
        self.assertEqual(rc, 0)
        self.assertEqual(comp.calls, 2, "evaluator was re-queried!")
        decisions = rs.load_decisions(self.paths.decisions)
        self.assertEqual(len(decisions), 2)


# ── Invalid-verdict non-resampling ────────────────────────────────────

class TestInvalidVerdict(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="arm1_inv_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.paths = tmp_paths(self.tmpdir)
        self.tasks = real_frozen()[:2]

    def test_maybe_halts_and_never_resamples(self):
        client, comp = text_client("MAYBE")
        rc = rs.run(self.paths, client, self.tasks, cap_usd=10.0,
                    log=silent_log)
        self.assertEqual(rc, 3)  # halted loudly
        self.assertEqual(comp.calls, 1)  # exactly one evaluator call
        # raw response frozen
        responses = rs.validate_response_ledger(self.paths.responses)
        tid = self.tasks[0]["task_id"]
        self.assertEqual(responses[tid]["raw_evaluator_response"], "MAYBE")
        # decision recorded as invalid, no binary A
        decisions = rs.load_decisions(self.paths.decisions)
        self.assertEqual(decisions[tid]["parse_status"], "invalid_verdict")
        self.assertIsNone(decisions[tid]["verdict"])
        self.assertIsNone(decisions[tid]["acceptance"])
        # second task never evaluated (halted)
        self.assertNotIn(self.tasks[1]["task_id"], responses)
        # resume: still halts, still no new calls
        rc2 = rs.run(self.paths, client, self.tasks, cap_usd=10.0,
                     log=silent_log)
        self.assertEqual(rc2, 3)
        self.assertEqual(comp.calls, 1, "malformed verdict was resampled!")


# ── Structural-response failure ───────────────────────────────────────

class TestStructuralFailure(unittest.TestCase):

    def test_each_malformed_case_is_fatal_with_one_call(self):
        from common.model_client import (
            FatalConfigError, StructuralResponseError)

        def multi_choice(kwargs):
            c = SimpleNamespace(message=SimpleNamespace(content="YES"),
                                finish_reason="stop")
            return SimpleNamespace(id="r", model=acfg.EVALUATOR_MODEL,
                                   choices=[c, c], usage=None)

        def empty_model(kwargs):
            return SimpleNamespace(
                id="r", model="",
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content="YES"),
                    finish_reason="stop")], usage=None)

        def none_content(kwargs):
            return SimpleNamespace(
                id="r", model=acfg.EVALUATOR_MODEL,
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content=None),
                    finish_reason="stop")], usage=None)

        def list_content(kwargs):
            return SimpleNamespace(
                id="r", model=acfg.EVALUATOR_MODEL,
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content=["YES"]),
                    finish_reason="stop")], usage=None)

        for fn in (multi_choice, empty_model, none_content, list_content):
            client, comp = make_raw_client(fn)
            with self.assertRaises(StructuralResponseError):
                rs.evaluate_one(client, "p", silent_log, task_id="T")
            self.assertEqual(comp.calls, 1, f"{fn.__name__}: retried!")
            self.assertTrue(issubclass(StructuralResponseError,
                                       FatalConfigError))


# ── Transport retry ───────────────────────────────────────────────────

class TestTransportRetry(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="arm1_tr_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.paths = tmp_paths(self.tmpdir)
        self.tasks = real_frozen()[:1]

    def test_one_transient_failure_then_success(self):
        import httpx
        import openai
        state = {"n": 0}

        def flaky(kwargs):
            state["n"] += 1
            if state["n"] == 1:
                return openai.APIConnectionError(
                    request=httpx.Request("POST", "https://api.openai.com"))
            return fake_response("YES")

        client, comp = make_raw_client(flaky)
        saved = root_config.GEN_BACKOFF_BASE_S
        root_config.GEN_BACKOFF_BASE_S = 0.0  # no sleeping in tests
        try:
            rc = rs.run(self.paths, client, self.tasks, cap_usd=10.0,
                        log=silent_log)
        finally:
            root_config.GEN_BACKOFF_BASE_S = saved
        self.assertEqual(rc, 0)
        self.assertEqual(comp.calls, 2)
        responses = rs.validate_response_ledger(self.paths.responses)
        tid = self.tasks[0]["task_id"]
        # exactly one frozen evaluator response, retry recorded
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[tid]["transport_attempts"], 2)


# ── Validator ─────────────────────────────────────────────────────────

class TestArmValidator(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="arm1_val_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.paths = tmp_paths(self.tmpdir)

    def test_valid_full_arm(self):
        fabricate_arm_ledgers(
            self.paths,
            verdict_fn=lambda t: "YES" if t["task_index"] % 3 else "NO")
        rc, out = run_validator(self.paths)
        self.assertEqual(rc, 0)
        self.assertIn("Frozen evaluator responses: 164", out)
        self.assertIn("Parsed self-check decisions: 164", out)
        self.assertIn("YES + NO: 164", out)
        self.assertIn("Invalid verdicts: 0", out)
        self.assertIn("VALID ARM 1 SELFCHECK: YES", out)

    def test_rejects_tampered_raw_response(self):
        def mutate(t, rr):
            rr["raw_evaluator_response"] = "NO"  # stored hash now stale
            return rr
        fabricate_arm_ledgers(self.paths, mutate_resp=mutate)
        rc, out = run_validator(self.paths)
        self.assertEqual(rc, 1)
        self.assertIn("VALID ARM 1 SELFCHECK: NO", out)

    def test_rejects_tampered_decision(self):
        def mutate(t, dr):
            if t["task_index"] == 0:
                dr["acceptance"] = 0  # parser would say 1 for "YES"
            return dr
        fabricate_arm_ledgers(self.paths, mutate_dec=mutate)
        rc, out = run_validator(self.paths)
        self.assertEqual(rc, 1)
        self.assertIn("VALID ARM 1 SELFCHECK: NO", out)

    def test_invalid_verdict_fails_validation(self):
        fabricate_arm_ledgers(self.paths, verdict_fn=lambda t: "MAYBE")
        rc, out = run_validator(self.paths)
        self.assertEqual(rc, 1)
        self.assertIn("Invalid verdicts: 164", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
