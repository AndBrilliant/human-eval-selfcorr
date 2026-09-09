#!/usr/bin/env python3
"""Local regression tests for Arm 2 (structured steelman / self-debate).

NO model/API calls anywhere: evaluation is driven by fake clients.
The real Stage 0 frozen ledger is read locally (read-only) as the
approved source corpus; baseline.jsonl and Arm 1 ledgers are NEVER read.

Run:  python3 tests/test_arm2_steelman.py   (from benchmark_v2/)
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as root_config
from arms.steelman import config as acfg
import arms.steelman.run_steelman as rs
import arms.steelman.validate_steelman as vs


# ── Fakes / helpers ───────────────────────────────────────────────────

FAKE_FINGERPRINT = "fp_arm2_fake"
FAKE_CREATED = 1777200000

VALID_YES = ("CASE_AGAINST:\nThe loop might skip the last element.\n\n"
             "CASE_FOR:\nThe range covers all indices; concern does not apply.\n\n"
             "FINAL_VERDICT: YES")
VALID_NO = ("CASE_AGAINST:\nOff-by-one on empty input returns wrong value.\n\n"
            "CASE_FOR:\nThe docstring example passes, but edge cases fail.\n\n"
            "FINAL_VERDICT: NO")


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
        id="fake-arm2-resp", model=acfg.EVALUATOR_MODEL,
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=text), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=500, completion_tokens=300),
        system_fingerprint=FAKE_FINGERPRINT, created=FAKE_CREATED,
        service_tier="default", _request_id="req_arm2_fake",
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


def fabricate_arm_ledgers(paths, n=164, text_fn=None, mutate_resp=None,
                          mutate_dec=None):
    """Write n consistent response+decision records from the REAL frozen
    ledger, without any API call."""
    tasks = real_frozen()[:n]
    for t in tasks:
        text = text_fn(t) if text_fn else VALID_YES
        res = rs.EvalResult(
            text=text, response_id=f"fake-{t['task_id']}",
            response_model=acfg.EVALUATOR_MODEL, finish_reason="stop",
            prompt_tokens=500, completion_tokens=300, duration_s=0.1,
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
    sys.argv = ["validate_steelman.py", "--responses", str(paths.responses),
                "--decisions", str(paths.decisions)]
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = vs.main()
    finally:
        sys.argv = argv
    return rc, out.getvalue()


# ── Source integrity ──────────────────────────────────────────────────

class TestSourceIntegrity(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="arm2_si_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

    def test_approved_source_hash_accepted(self):
        rs.verify_source_ledger()  # must not raise on the real corpus

    def test_wrong_source_hash_aborts_before_any_client(self):
        tampered = Path(self.tmpdir) / "frozen_tampered.jsonl"
        data = Path(acfg.FROZEN_SOURCE_PATH).read_text()
        tampered.write_text(data + " ")
        client_called = []

        def factory():
            client_called.append(True)
            raise AssertionError("client factory must never be reached")

        with self.assertRaises(SystemExit):
            rs.verify_source_ledger(tampered)
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
        bad = Path(self.tmpdir) / "frozen_badcand.jsonl"
        tasks[1]["candidate_sha256"] = "0" * 64
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
        self.assertIn("Frozen Arm 2 responses: 163", out)
        self.assertIn("VALID ARM 2 STEELMAN: NO", out)


# ── Blindness (T0 and Arm 1) ──────────────────────────────────────────

class TestBlindness(unittest.TestCase):

    def test_no_arm2_source_references_forbidden_inputs(self):
        """Arm 2 runtime source must not operationally reference ground
        truth or Arm 1 ledgers."""
        arm_dir = Path(acfg.ARM_DIR)
        for py in arm_dir.glob("*.py"):
            for lineno, line in enumerate(py.read_text().splitlines(), 1):
                code = line.split("#", 1)[0]
                if re.search(r"""(open|read_text|read_bytes|load_jsonl)\s*\(""",
                             code):
                    for forbidden in ("baseline.jsonl", "BASELINE_PATH",
                                      "arm1_selfcheck"):
                        self.assertNotIn(forbidden, code,
                                         f"{py}:{lineno}: {line}")

    def test_runner_succeeds_with_baseline_and_arm1_absent(self):
        """Full runner pass uses only the frozen ledger; baseline.jsonl
        and Arm 1 ledgers are never consulted."""
        tmpdir = tempfile.mkdtemp(prefix="arm2_blind_")
        self.addCleanup(shutil.rmtree, tmpdir, True)
        paths = tmp_paths(tmpdir)
        tasks = real_frozen()[:3]
        client, comp = text_client(VALID_YES)
        rc = rs.run(paths, client, tasks, cap_usd=10.0, log=silent_log)
        self.assertEqual(rc, 0)
        self.assertEqual(comp.calls, 3)

    def test_evaluator_prompt_contains_no_ground_truth_or_arm1(self):
        tasks = real_frozen()[:5]
        for t in tasks:
            p = rs.build_evaluator_prompt(t["prompt"], t["candidate_code"])
            for forbidden in ("baseline_correct", "test_status", "test_details",
                              "A_selfcheck", "arm1", "Arm 1",
                              "AssertionError", "def check("):
                self.assertNotIn(forbidden, p, f"{t['task_id']}: {forbidden}")


# ── Exact request ─────────────────────────────────────────────────────

class TestExactRequest(unittest.TestCase):

    def test_request_parameters_and_single_user_message(self):
        tmpdir = tempfile.mkdtemp(prefix="arm2_req_")
        self.addCleanup(shutil.rmtree, tmpdir, True)
        paths = tmp_paths(tmpdir)
        tasks = real_frozen()[:1]
        client, comp = text_client(VALID_NO)
        rc = rs.run(paths, client, tasks, cap_usd=10.0, log=silent_log)
        self.assertEqual(rc, 0)
        self.assertEqual(comp.calls, 1)
        kw = comp.kwargs_seen[0]
        self.assertEqual(kw["model"], "gpt-5.4-2026-03-05")
        self.assertEqual(kw["temperature"], 0)
        self.assertEqual(kw["reasoning_effort"], "none")
        self.assertEqual(kw["max_completion_tokens"], 2048)
        self.assertEqual(len(kw["messages"]), 1)
        self.assertEqual(kw["messages"][0]["role"], "user")
        expected = rs.build_evaluator_prompt(tasks[0]["prompt"],
                                             tasks[0]["candidate_code"])
        self.assertEqual(kw["messages"][0]["content"], expected)


# ── Structured parser ─────────────────────────────────────────────────

class TestStructuredParser(unittest.TestCase):

    def test_valid_yes(self):
        ca, cf, v, a, ps = rs.parse_structured(VALID_YES)
        self.assertEqual(ps, "valid")
        self.assertEqual(v, "YES")
        self.assertEqual(a, 1)
        self.assertEqual(ca, "The loop might skip the last element.")
        self.assertEqual(cf, "The range covers all indices; concern does not apply.")

    def test_valid_no(self):
        ca, cf, v, a, ps = rs.parse_structured(VALID_NO)
        self.assertEqual((v, a, ps), ("NO", 0, "valid"))
        self.assertEqual(ca, "Off-by-one on empty input returns wrong value.")
        self.assertEqual(cf, "The docstring example passes, but edge cases fail.")

    def test_valid_with_surrounding_whitespace(self):
        ca, cf, v, a, ps = rs.parse_structured("\n\n  " + VALID_YES + "\n\n")
        self.assertEqual((v, a, ps), ("YES", 1, "valid"))

    def test_verdict_line_whitespace(self):
        text = VALID_YES.replace("FINAL_VERDICT: YES", "FINAL_VERDICT:   YES  ")
        ca, cf, v, a, ps = rs.parse_structured(text)
        self.assertEqual((v, a, ps), ("YES", 1, "valid"))

    def test_invalid_cases(self):
        base_sections = ("CASE_AGAINST:\nConcern.\n\nCASE_FOR:\nRebuttal.\n\n")
        invalids = {
            "missing CASE_AGAINST": "CASE_FOR:\nRebuttal.\n\nFINAL_VERDICT: YES",
            "missing CASE_FOR": "CASE_AGAINST:\nConcern.\n\nFINAL_VERDICT: YES",
            "reversed order": ("CASE_FOR:\nRebuttal.\n\nCASE_AGAINST:\nConcern.\n\n"
                               "FINAL_VERDICT: YES"),
            "empty CASE_AGAINST": "CASE_AGAINST:\n\nCASE_FOR:\nRebuttal.\n\nFINAL_VERDICT: YES",
            "empty CASE_FOR": "CASE_AGAINST:\nConcern.\n\nCASE_FOR:\n   \nFINAL_VERDICT: YES",
            "two CASE_AGAINST": ("CASE_AGAINST:\nConcern.\n\nCASE_AGAINST:\nMore.\n\n"
                                 "CASE_FOR:\nRebuttal.\n\nFINAL_VERDICT: YES"),
            "two CASE_FOR": ("CASE_AGAINST:\nConcern.\n\nCASE_FOR:\nRebuttal.\n\n"
                             "CASE_FOR:\nMore.\n\nFINAL_VERDICT: YES"),
            "two FINAL_VERDICT": base_sections + "FINAL_VERDICT: YES\nFINAL_VERDICT: NO",
            "verdict with period": base_sections + "FINAL_VERDICT: YES.",
            "bare YES": "YES",
            "content after verdict": base_sections + "FINAL_VERDICT: YES\n\nThanks!",
            "VERDICT: YES form": base_sections + "VERDICT: YES",
            "empty response": "",
            "whitespace only": "   \n\n  ",
        }
        for name, text in invalids.items():
            ca, cf, v, a, ps = rs.parse_structured(text)
            self.assertEqual(ps, "invalid_structure", f"{name} accepted!")
            self.assertIsNone(v, name)
            self.assertIsNone(a, name)

    def test_deterministic(self):
        self.assertEqual(rs.parse_structured(VALID_YES),
                         rs.parse_structured(VALID_YES))


# ── Freeze-before-parse ───────────────────────────────────────────────

class TestFreezeBeforeParse(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="arm2_fbp_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.paths = tmp_paths(self.tmpdir)
        self.tasks = real_frozen()[:2]

    def test_parse_crash_then_resume_no_resample(self):
        client, comp = text_client(VALID_YES)
        real_parse = rs.parse_structured

        def crash_parse(raw):
            raise RuntimeError("simulated parse crash")

        rs.parse_structured = crash_parse
        try:
            with self.assertRaises(RuntimeError):
                rs.run(self.paths, client, self.tasks, cap_usd=10.0,
                       log=silent_log)
        finally:
            rs.parse_structured = real_parse
        self.assertEqual(comp.calls, 1)

        responses = rs.validate_response_ledger(self.paths.responses)
        tid = self.tasks[0]["task_id"]
        raw_hash_1 = responses[tid]["raw_evaluator_response_sha256"]

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
        self.assertEqual(decisions[tid]["case_against"],
                         "The loop might skip the last element.")

    def test_missing_decisions_ledger_reparses_without_resample(self):
        client, comp = text_client(VALID_NO)
        rc = rs.run(self.paths, client, self.tasks, cap_usd=10.0,
                    log=silent_log)
        self.assertEqual(rc, 0)
        self.assertEqual(comp.calls, 2)
        self.paths.decisions.unlink()
        rc = rs.run(self.paths, client, self.tasks, cap_usd=10.0,
                    log=silent_log)
        self.assertEqual(rc, 0)
        self.assertEqual(comp.calls, 2, "evaluator was re-queried!")
        decisions = rs.load_decisions(self.paths.decisions)
        self.assertEqual(len(decisions), 2)


# ── Malformed-treatment non-resampling ────────────────────────────────

class TestMalformedTreatment(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="arm2_mal_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.paths = tmp_paths(self.tmpdir)
        self.tasks = real_frozen()[:2]

    def test_invalid_structure_halts_and_never_resamples(self):
        client, comp = text_client("FINAL_VERDICT: YES.")  # bad grammar
        rc = rs.run(self.paths, client, self.tasks, cap_usd=10.0,
                    log=silent_log)
        self.assertEqual(rc, 3)
        self.assertEqual(comp.calls, 1)
        responses = rs.validate_response_ledger(self.paths.responses)
        tid = self.tasks[0]["task_id"]
        self.assertEqual(responses[tid]["raw_evaluator_response"],
                         "FINAL_VERDICT: YES.")
        decisions = rs.load_decisions(self.paths.decisions)
        self.assertEqual(decisions[tid]["parse_status"], "invalid_structure")
        self.assertIsNone(decisions[tid]["verdict"])
        self.assertIsNone(decisions[tid]["acceptance"])
        self.assertNotIn(self.tasks[1]["task_id"], responses)
        rc2 = rs.run(self.paths, client, self.tasks, cap_usd=10.0,
                     log=silent_log)
        self.assertEqual(rc2, 3)
        self.assertEqual(comp.calls, 1, "malformed treatment was resampled!")


# ── Structural API errors ─────────────────────────────────────────────

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
        self.tmpdir = tempfile.mkdtemp(prefix="arm2_tr_")
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
            return fake_response(VALID_YES)

        client, comp = make_raw_client(flaky)
        saved = root_config.GEN_BACKOFF_BASE_S
        root_config.GEN_BACKOFF_BASE_S = 0.0
        try:
            rc = rs.run(self.paths, client, self.tasks, cap_usd=10.0,
                        log=silent_log)
        finally:
            root_config.GEN_BACKOFF_BASE_S = saved
        self.assertEqual(rc, 0)
        self.assertEqual(comp.calls, 2)
        responses = rs.validate_response_ledger(self.paths.responses)
        tid = self.tasks[0]["task_id"]
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[tid]["transport_attempts"], 2)


# ── Validator ─────────────────────────────────────────────────────────

class TestArm2Validator(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="arm2_val_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.paths = tmp_paths(self.tmpdir)

    def test_valid_full_arm(self):
        fabricate_arm_ledgers(
            self.paths,
            text_fn=lambda t: VALID_YES if t["task_index"] % 3 else VALID_NO)
        rc, out = run_validator(self.paths)
        self.assertEqual(rc, 0)
        self.assertIn("Frozen Arm 2 responses: 164", out)
        self.assertIn("Parsed Arm 2 decisions: 164", out)
        self.assertIn("YES + NO: 164", out)
        self.assertIn("Invalid structured responses: 0", out)
        self.assertIn("VALID ARM 2 STEELMAN: YES", out)

    def test_rejects_tampered_raw_response(self):
        def mutate(t, rr):
            rr["raw_evaluator_response"] = VALID_NO  # stored hash now stale
            return rr
        fabricate_arm_ledgers(self.paths, mutate_resp=mutate)
        rc, out = run_validator(self.paths)
        self.assertEqual(rc, 1)
        self.assertIn("VALID ARM 2 STEELMAN: NO", out)

    def test_rejects_tampered_decision(self):
        def mutate(t, dr):
            if t["task_index"] == 0:
                dr["acceptance"] = 0  # parser would say 1 for VALID_YES
            return dr
        fabricate_arm_ledgers(self.paths, mutate_dec=mutate)
        rc, out = run_validator(self.paths)
        self.assertEqual(rc, 1)
        self.assertIn("VALID ARM 2 STEELMAN: NO", out)

    def test_rejects_tampered_section_text(self):
        def mutate(t, dr):
            if t["task_index"] == 0:
                dr["case_against"] = "rewritten section text"
            return dr
        fabricate_arm_ledgers(self.paths, mutate_dec=mutate)
        rc, out = run_validator(self.paths)
        self.assertEqual(rc, 1)
        self.assertIn("VALID ARM 2 STEELMAN: NO", out)

    def test_invalid_structure_fails_validation(self):
        fabricate_arm_ledgers(self.paths, text_fn=lambda t: "FINAL_VERDICT: YES.")
        rc, out = run_validator(self.paths)
        self.assertEqual(rc, 1)
        self.assertIn("Invalid structured responses: 164", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
