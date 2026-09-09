#!/usr/bin/env python3
"""Local regression tests for Arm 3 (execution-grounded positive control),
including the executable integrity gate, strong cross-bound ledger
validation, and production-completeness invariants.

NO model/API calls anywhere: evaluation is driven by fake clients.
Execution tests run small synthetic programs or real frozen candidates
locally (subprocess only). The real Stage 0 frozen ledger is read
read-only as the approved source corpus.

Run:  python3 tests/test_arm3_execution.py   (from benchmark_v2/)
"""
from __future__ import annotations

import contextlib
import hashlib
import inspect
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
from arms.execution import config as acfg
import arms.execution.run_execution_signals as sig
import arms.execution.run_execution_evaluator as ev
import arms.execution.validate_execution_signals as vsig
import arms.execution.validate_execution as vfin


# ── Fakes / helpers ───────────────────────────────────────────────────

FAKE_FINGERPRINT = "fp_arm3_fake"
FAKE_CREATED = 1777300000


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
        id="fake-arm3-resp", model=acfg.EVALUATOR_MODEL,
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=text), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=600, completion_tokens=1),
        system_fingerprint=FAKE_FINGERPRINT, created=FAKE_CREATED,
        service_tier="default", _request_id="req_arm3_fake",
    )


def text_client(text):
    return make_raw_client(lambda kw: fake_response(text))


def silent_log(event, **fields):
    pass


def sig_paths(tmpdir):
    d = Path(tmpdir)
    return sig.Paths(signals=d / "signals.jsonl", failures=d / "failures.jsonl",
                     log=d / "log.jsonl")


def ev_paths(tmpdir):
    d = Path(tmpdir)
    return ev.Paths(signals=d / "signals.jsonl", responses=d / "responses.jsonl",
                    decisions=d / "decisions.jsonl", failures=d / "failures.jsonl",
                    log=d / "log.jsonl",
                    certification=d / "certification.json")


def real_frozen():
    return sig.load_frozen_records(acfg.FROZEN_SOURCE_PATH)


SYNTH_PASS_CODE = "def probe(x):\n    return x + 1\n"
SYNTH_PASS_TEST = ("def check(candidate):\n"
                   "    assert candidate(1) == 2\n"
                   "    assert candidate(0) == 1\n")
SYNTH_FAIL_CODE = "def probe(x):\n    return x + 2\n"
SYNTH_LOOP_CODE = "def probe(x):\n    while True:\n        pass\n"
SYNTH_RAISE_CODE = "def probe(x):\n    raise RuntimeError('secret detail')\n"


def synth_task(code, task_id="Synth/0", task_index=0):
    return {"task_id": task_id, "task_index": task_index,
            "prompt": "def probe(x):\n",
            "entry_point": "probe",
            "candidate_code": code,
            "candidate_sha256": sig.sha256_text(code)}


def real_baseline():
    return {r["task_id"]: r["baseline_correct"]
            for r in sig.load_jsonl(acfg.BASELINE_PATH)}


def fabricate_full_signals(paths, n=164, status_fn=None):
    """Write n signal records from the REAL frozen ledger, aligned with
    the actual Stage 0 outcome by default (fixture construction only)."""
    tasks = real_frozen()[:n]
    baseline = real_baseline()
    for t in tasks:
        if status_fn:
            status = status_fn(t)
        else:
            status = "pass" if baseline[t["task_id"]] else "fail_assertion"
        rec = sig.build_signal_record(t, status, 0.1, 0)
        sig.append_jsonl(paths.signals, rec)


def certify(paths):
    """Run the REAL integrity validator against the tmp signal ledger."""
    argv = sys.argv
    sys.argv = ["validate_execution_signals.py", "--signals",
                str(paths.signals), "--certification",
                str(paths.certification)]
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = vsig.main()
    finally:
        sys.argv = argv
    return rc, out.getvalue()


def fabricate_responses_decisions(paths, start=0, n=164, verdict_fn=None):
    """Write consistent response+decision records for tasks[start:n]."""
    tasks = real_frozen()[start:n]
    signals = {s["task_id"]: s for s in sig.load_jsonl(paths.signals)}
    for t in tasks:
        text = verdict_fn(t) if verdict_fn else "YES"
        res = ev.EvalResult(
            text=text, response_id=f"fake-{t['task_id']}",
            response_model=acfg.EVALUATOR_MODEL, finish_reason="stop",
            prompt_tokens=600, completion_tokens=1, duration_s=0.1,
            transport_attempts=1, system_fingerprint=FAKE_FINGERPRINT,
            response_created=FAKE_CREATED, service_tier="default",
            request_id="req_fake")
        prompt = ev.build_evaluator_prompt(
            t["prompt"], t["candidate_code"],
            signals[t["task_id"]]["execution_treatment_text"])
        rr = ev.build_response_record(t, signals[t["task_id"]], res, prompt)
        ev.append_jsonl(paths.responses, rr)
        dr = ev.build_decision_record(t, rr)
        ev.append_jsonl(paths.decisions, dr)


def run_final_validator(paths):
    argv = sys.argv
    sys.argv = ["validate_execution.py", "--signals", str(paths.signals),
                "--responses", str(paths.responses), "--decisions",
                str(paths.decisions)]
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = vfin.main()
    finally:
        sys.argv = argv
    return rc, out.getvalue()


def tamper_line(path, index, mutate):
    lines = Path(path).read_text().splitlines()
    rec = json.loads(lines[index])
    rec = mutate(rec) or rec
    lines[index] = json.dumps(rec)
    Path(path).write_text("\n".join(lines) + "\n")


class Arm3Fixture(unittest.TestCase):
    """Base: full certified 164-signal ledger in a tmp dir."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="arm3_fx_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.paths = ev_paths(self.tmpdir)
        self.tasks = real_frozen()
        fabricate_full_signals(sig.Paths(self.paths.signals,
                                         self.paths.failures, self.paths.log))
        rc, out = certify(self.paths)
        assert rc == 0, f"fixture certification failed: {out}"


# ── Source integrity ──────────────────────────────────────────────────

class TestSourceIntegrity(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="arm3_si_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

    def test_approved_source_hash_accepted(self):
        sig.verify_source_ledger()

    def test_wrong_source_hash_aborts(self):
        tampered = Path(self.tmpdir) / "frozen_tampered.jsonl"
        tampered.write_text(
            Path(acfg.FROZEN_SOURCE_PATH).read_text() + " ")
        with self.assertRaises(SystemExit):
            sig.verify_source_ledger(tampered)

    def test_altered_candidate_hash_aborts(self):
        tasks = real_frozen()[:2]
        bad = Path(self.tmpdir) / "bad.jsonl"
        tasks[1]["candidate_sha256"] = "0" * 64
        with open(bad, "w") as f:
            for t in tasks:
                f.write(json.dumps({k: t[k] for k in
                                    ("task_id", "task_index", "prompt",
                                     "entry_point", "candidate_code",
                                     "candidate_sha256")})
                        + "\n")
        with self.assertRaises(SystemExit):
            sig.load_frozen_records(bad)


# ── Execution treatment mapping ───────────────────────────────────────

class TestTreatments(unittest.TestCase):

    def test_pass_treatment_exact(self):
        task = synth_task(SYNTH_PASS_CODE)
        rec = sig.execute_task(task, SYNTH_PASS_TEST, silent_log)
        self.assertEqual(rec["execution_status"], "pass")
        self.assertEqual(rec["execution_test_status"], "pass")
        self.assertEqual(rec["execution_treatment_text"],
                         "EXECUTION RESULT: PASS\n"
                         "The candidate passed all reference tests.")

    def test_fail_assertion_treatment_exact(self):
        task = synth_task(SYNTH_FAIL_CODE)
        rec = sig.execute_task(task, SYNTH_PASS_TEST, silent_log)
        self.assertEqual(rec["execution_status"], "fail")
        self.assertEqual(rec["execution_test_status"], "fail_assertion")
        self.assertEqual(rec["execution_treatment_text"],
                         "EXECUTION RESULT: FAIL\n"
                         "The candidate failed at least one reference test.")

    def test_timeout_maps_to_exact_timeout_treatment(self):
        task = synth_task(SYNTH_LOOP_CODE)
        saved = root_config.TEST_TIMEOUT_S
        root_config.TEST_TIMEOUT_S = 3.0
        try:
            rec = sig.execute_task(task, SYNTH_PASS_TEST, silent_log)
        finally:
            root_config.TEST_TIMEOUT_S = saved
        self.assertEqual(rec["execution_status"], "fail")
        self.assertEqual(rec["execution_test_status"], "timeout")
        self.assertEqual(rec["execution_treatment_text"],
                         "EXECUTION RESULT: FAIL\n"
                         "The candidate did not complete within the "
                         "execution timeout.")

    def test_exception_maps_to_fail_without_details(self):
        task = synth_task(SYNTH_RAISE_CODE)
        rec = sig.execute_task(task, SYNTH_PASS_TEST, silent_log)
        self.assertEqual(rec["execution_status"], "fail")
        self.assertEqual(rec["execution_test_status"], "fail_error")
        self.assertEqual(rec["execution_treatment_text"], acfg.TREATMENT_FAIL)
        self.assertNotIn("secret detail", rec["execution_treatment_text"])
        self.assertNotIn("RuntimeError", rec["execution_treatment_text"])

    def test_infra_error_retries_per_policy(self):
        from common.execution import TestInfraError
        calls = {"n": 0}
        real = sig.run_tests_once

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] < 3:
                raise TestInfraError("simulated spawn failure")
            return real(*a, **k)

        sig.run_tests_once = flaky
        saved = root_config.TEST_BACKOFF_BASE_S
        root_config.TEST_BACKOFF_BASE_S = 0.0
        try:
            task = synth_task(SYNTH_PASS_CODE)
            rec = sig.execute_task(task, SYNTH_PASS_TEST, silent_log)
        finally:
            sig.run_tests_once = real
            root_config.TEST_BACKOFF_BASE_S = saved
        self.assertEqual(calls["n"], 3)
        self.assertEqual(rec["execution_infra_retry_count"], 2)
        self.assertEqual(rec["execution_status"], "pass")

    def test_exact_frozen_candidate_is_executed(self):
        seen = {}
        real = sig.run_tests_once

        def spy(candidate_code, test_code, entry_point, timeout_s):
            seen["candidate"] = candidate_code
            return real(candidate_code, test_code, entry_point, timeout_s)

        sig.run_tests_once = spy
        try:
            task = synth_task(SYNTH_PASS_CODE)
            sig.execute_task(task, SYNTH_PASS_TEST, silent_log)
        finally:
            sig.run_tests_once = real
        self.assertEqual(seen["candidate"], SYNTH_PASS_CODE)
        self.assertEqual(sig.sha256_text(seen["candidate"]),
                         task["candidate_sha256"])


# ── Phase A freeze/resume ─────────────────────────────────────────────

class TestPhaseAFreezeResume(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="arm3_pa_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.paths = sig_paths(self.tmpdir)
        self.tasks = real_frozen()[:3]
        self.tests = {t["task_id"]: SYNTH_PASS_TEST for t in self.tasks}

    def test_signal_freezes_and_resume_never_reexecutes(self):
        calls = {"n": 0}
        real = sig.run_tests_once

        def counting(*a, **k):
            calls["n"] += 1
            return real(*a, **k)

        sig.run_tests_once = counting
        try:
            rc1 = sig.run_signals(self.paths, self.tasks, self.tests,
                                  silent_log)
            rc2 = sig.run_signals(self.paths, self.tasks, self.tests,
                                  silent_log)
        finally:
            sig.run_tests_once = real
        self.assertEqual((rc1, rc2), (0, 0))
        self.assertEqual(calls["n"], 3, "frozen signal was re-executed!")
        signals = sig.load_jsonl(self.paths.signals)
        self.assertEqual(len(signals), 3)

    def test_no_ground_truth_in_signal_records(self):
        sig.run_signals(self.paths, self.tasks, self.tests, silent_log)
        for rec in sig.load_jsonl(self.paths.signals):
            for forbidden in ("baseline_correct", "verdict", "acceptance",
                              "arm1", "arm2"):
                self.assertNotIn(forbidden, rec)


# ── Signal-ledger hardening ───────────────────────────────────────────

class TestSignalLedgerHardening(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="arm3_slh_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.tasks = real_frozen()
        self.frozen_by_id = {t["task_id"]: t for t in self.tasks}
        self.path = Path(self.tmpdir) / "signals.jsonl"

    def write(self, records):
        with open(self.path, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    def good(self, task, status=None):
        baseline = real_baseline()
        if status is None:
            status = "pass" if baseline[task["task_id"]] else "fail_assertion"
        return sig.build_signal_record(task, status, 0.1, 0)

    def test_unexpected_task_id_rejected(self):
        rec = self.good(self.tasks[0])
        rec["task_id"] = "HumanEval/999"
        self.write([rec])
        with self.assertRaises(SystemExit):
            sig.validate_signal_ledger(self.path, self.frozen_by_id)

    def test_163_expected_plus_1_unexpected_never_complete(self):
        records = [self.good(t) for t in self.tasks[:163]]
        bogus = self.good(self.tasks[163])
        bogus["task_id"] = "HumanEval/999"
        records.append(bogus)
        self.write(records)
        with self.assertRaises(SystemExit):
            sig.validate_signal_ledger(self.path, self.frozen_by_id)

    def test_wrong_task_index_rejected(self):
        rec = self.good(self.tasks[0])
        rec["task_index"] = rec["task_index"] + 1
        self.write([rec])
        with self.assertRaises(SystemExit):
            sig.validate_signal_ledger(self.path, self.frozen_by_id)

    def test_timeout_with_generic_fail_treatment_rejected(self):
        rec = self.good(self.tasks[0], status="pass")
        rec["execution_test_status"] = "timeout"
        rec["execution_status"] = "fail"
        rec["execution_treatment_text"] = acfg.TREATMENT_FAIL
        self.write([rec])
        with self.assertRaises(SystemExit):
            sig.validate_signal_ledger(self.path, self.frozen_by_id)

    def test_pass_status_with_fail_execution_status_rejected(self):
        rec = self.good(self.tasks[0], status="pass")
        rec["execution_test_status"] = "pass"
        rec["execution_status"] = "fail"
        rec["execution_treatment_text"] = acfg.TREATMENT_FAIL
        self.write([rec])
        with self.assertRaises(SystemExit):
            sig.validate_signal_ledger(self.path, self.frozen_by_id)

    def test_unknown_execution_test_status_rejected(self):
        rec = self.good(self.tasks[0], status="pass")
        rec["execution_test_status"] = "weird_status"
        rec["execution_status"] = "pass"
        rec["execution_treatment_text"] = acfg.TREATMENT_PASS
        self.write([rec])
        with self.assertRaises(SystemExit):
            sig.validate_signal_ledger(self.path, self.frozen_by_id)


# ── Phase B: gate + evaluator ─────────────────────────────────────────

class TestPhaseB(Arm3Fixture):

    def test_missing_certification_aborts_zero_calls(self):
        self.paths.certification.unlink()
        client, comp = text_client("YES")
        with self.assertRaises(SystemExit):
            ev.run_evaluator(self.paths, client, 10.0, silent_log)
        self.assertEqual(comp.calls, 0)

    def test_invalid_certification_aborts_zero_calls(self):
        cert = json.loads(self.paths.certification.read_text())
        cert["certification_status"] = "invalid"
        self.paths.certification.write_text(json.dumps(cert))
        client, comp = text_client("YES")
        with self.assertRaises(SystemExit):
            ev.run_evaluator(self.paths, client, 10.0, silent_log)
        self.assertEqual(comp.calls, 0)

    def test_stale_certification_aborts_zero_calls(self):
        cert = json.loads(self.paths.certification.read_text())
        cert["execution_signals_sha256"] = "0" * 64
        self.paths.certification.write_text(json.dumps(cert))
        client, comp = text_client("YES")
        with self.assertRaises(SystemExit):
            ev.run_evaluator(self.paths, client, 10.0, silent_log)
        self.assertEqual(comp.calls, 0)

    def test_missing_certified_at_aborts(self):
        cert = json.loads(self.paths.certification.read_text())
        cert["certified_at"] = ""
        self.paths.certification.write_text(json.dumps(cert))
        client, comp = text_client("YES")
        with self.assertRaises(SystemExit):
            ev.run_evaluator(self.paths, client, 10.0, silent_log)
        self.assertEqual(comp.calls, 0)

    def test_main_does_not_construct_client_without_certification(self):
        self.paths.certification.unlink()
        called = []

        def factory():
            called.append(True)
            raise AssertionError("client factory must never be reached")

        argv = ["--signals", str(self.paths.signals),
                "--responses", str(self.paths.responses),
                "--decisions", str(self.paths.decisions),
                "--failures", str(self.paths.failures),
                "--log", str(self.paths.log),
                "--certification", str(self.paths.certification)]
        with self.assertRaises(SystemExit):
            ev.main(argv, client_factory=factory)
        self.assertEqual(called, [])

    def test_incomplete_signals_abort_zero_calls(self):
        lines = self.paths.signals.read_text().splitlines()
        self.paths.signals.write_text("\n".join(lines[:2]) + "\n")
        client, comp = text_client("YES")
        with self.assertRaises(SystemExit):
            ev.run_evaluator(self.paths, client, 10.0, silent_log)
        self.assertEqual(comp.calls, 0)

    def test_exact_request_and_treatment(self):
        fabricate_responses_decisions(self.paths, start=1)  # task 0 pending
        client, comp = text_client("YES")
        rc = ev.run_evaluator(self.paths, client, 10.0, silent_log)
        self.assertEqual(rc, 0)
        self.assertEqual(comp.calls, 1)
        kw = comp.kwargs_seen[0]
        self.assertEqual(kw["model"], "gpt-5.4-2026-03-05")
        self.assertEqual(kw["temperature"], 0)
        self.assertEqual(kw["reasoning_effort"], "none")
        self.assertEqual(kw["max_completion_tokens"], 16)
        self.assertEqual(len(kw["messages"]), 1)
        self.assertEqual(kw["messages"][0]["role"], "user")
        signals = {s["task_id"]: s
                   for s in sig.load_jsonl(self.paths.signals)}
        t = self.tasks[0]
        expected = ev.build_evaluator_prompt(
            t["prompt"], t["candidate_code"],
            signals[t["task_id"]]["execution_treatment_text"])
        self.assertEqual(kw["messages"][0]["content"], expected)
        content = kw["messages"][0]["content"]
        self.assertIn(t["candidate_code"], content)
        self.assertIn(signals[t["task_id"]]["execution_treatment_text"],
                      content)
        for forbidden in ("baseline_correct", "T0", "def check(",
                          "AssertionError", "Traceback"):
            self.assertNotIn(forbidden, content)

    def test_binary_parser_matches_arm1(self):
        from arms.selfcheck.run_selfcheck import parse_verdict as arm1_parse
        for text in ("YES", " yes ", "NO", "\nno\n", "YES.", "MAYBE",
                     '{"verdict":"YES"}', "", "NO because x"):
            self.assertEqual(ev.parse_verdict(text), arm1_parse(text), text)

    def test_invalid_verdict_freezes_halts_no_resample(self):
        client, comp = text_client("MAYBE")
        rc = ev.run_evaluator(self.paths, client, 10.0, silent_log)
        self.assertEqual(rc, 3)
        self.assertEqual(comp.calls, 1)
        decisions = sig.load_jsonl(self.paths.decisions)
        self.assertEqual(decisions[0]["parse_status"], "invalid_verdict")
        self.assertIsNone(decisions[0]["verdict"])
        rc2 = ev.run_evaluator(self.paths, client, 10.0, silent_log)
        self.assertEqual(rc2, 3)
        self.assertEqual(comp.calls, 1, "invalid verdict was resampled!")

    def test_structural_api_failure_fatal_one_call(self):
        from common.model_client import StructuralResponseError

        def none_content(kwargs):
            return SimpleNamespace(
                id="r", model=acfg.EVALUATOR_MODEL,
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content=None),
                    finish_reason="stop")], usage=None)

        client, comp = make_raw_client(none_content)
        with self.assertRaises(StructuralResponseError):
            ev.evaluate_one(client, "p", silent_log, task_id="T")
        self.assertEqual(comp.calls, 1)

    def test_freeze_before_parse_survives_crash(self):
        fabricate_responses_decisions(self.paths, start=1)  # task 0 pending
        client, comp = text_client("YES")
        real_build = ev.build_decision_record

        def crash_build(task, response_rec):
            raise RuntimeError("simulated parse/store crash")

        ev.build_decision_record = crash_build
        try:
            with self.assertRaises(RuntimeError):
                ev.run_evaluator(self.paths, client, 10.0, silent_log)
        finally:
            ev.build_decision_record = real_build
        self.assertEqual(comp.calls, 1)
        responses = sig.load_jsonl(self.paths.responses)
        h1 = [r for r in responses
              if r["task_id"] == "HumanEval/0"][0]["raw_evaluator_response_sha256"]
        rc = ev.run_evaluator(self.paths, client, 10.0, silent_log)
        self.assertEqual(rc, 0)
        self.assertEqual(comp.calls, 1, "frozen response was resampled!")
        responses2 = sig.load_jsonl(self.paths.responses)
        h2 = [r for r in responses2
              if r["task_id"] == "HumanEval/0"][0]["raw_evaluator_response_sha256"]
        self.assertEqual(h1, h2)

    def test_transport_retry_then_success(self):
        import httpx
        import openai
        fabricate_responses_decisions(self.paths, start=1)  # task 0 pending
        state = {"n": 0}

        def flaky(kwargs):
            state["n"] += 1
            if state["n"] == 1:
                return openai.APIConnectionError(
                    request=httpx.Request("POST", "https://api.openai.com"))
            return fake_response("NO")

        client, comp = make_raw_client(flaky)
        saved = root_config.GEN_BACKOFF_BASE_S
        root_config.GEN_BACKOFF_BASE_S = 0.0
        try:
            rc = ev.run_evaluator(self.paths, client, 10.0, silent_log)
        finally:
            root_config.GEN_BACKOFF_BASE_S = saved
        self.assertEqual(rc, 0)
        self.assertEqual(comp.calls, 2)
        responses = sig.load_jsonl(self.paths.responses)
        r0 = [r for r in responses if r["task_id"] == "HumanEval/0"][0]
        self.assertEqual(r0["transport_attempts"], 2)


# ── Strong response-ledger binding (Issue 1) ──────────────────────────

class TestResponseBinding(Arm3Fixture):

    def setUp(self):
        super().setUp()
        # 163 valid frozen responses/decisions; task HumanEval/1 would
        # otherwise require exactly ONE new evaluator call.
        fabricate_responses_decisions(self.paths, start=1)

    def assert_aborts_zero_calls(self):
        client, comp = text_client("YES")
        with self.assertRaises(SystemExit):
            ev.run_evaluator(self.paths, client, 10.0, silent_log)
        self.assertEqual(comp.calls, 0)

    def test_wrong_candidate_sha_aborts(self):
        tamper_line(self.paths.responses, 0,
                    lambda r: r.update(candidate_sha256="0" * 64))
        self.assert_aborts_zero_calls()

    def test_wrong_task_index_aborts(self):
        tamper_line(self.paths.responses, 0,
                    lambda r: r.update(task_index=999))
        self.assert_aborts_zero_calls()

    def test_wrong_execution_status_aborts(self):
        def flip(r):
            r["execution_status"] = ("fail" if r["execution_status"] == "pass"
                                     else "pass")
        tamper_line(self.paths.responses, 0, flip)
        self.assert_aborts_zero_calls()

    def test_wrong_treatment_aborts(self):
        tamper_line(self.paths.responses, 0,
                    lambda r: r.update(
                        execution_treatment_text=acfg.TREATMENT_FAIL_TIMEOUT
                        if r["execution_treatment_text"] != acfg.TREATMENT_FAIL_TIMEOUT
                        else acfg.TREATMENT_PASS))
        self.assert_aborts_zero_calls()

    def test_wrong_evaluator_prompt_aborts(self):
        tamper_line(self.paths.responses, 0,
                    lambda r: r.update(evaluator_prompt="unrelated text"))
        self.assert_aborts_zero_calls()

    def test_unexpected_task_id_aborts(self):
        lines = self.paths.responses.read_text().splitlines()
        rec = json.loads(lines[0])
        rec["task_id"] = "HumanEval/999"
        lines.append(json.dumps(rec))
        self.paths.responses.write_text("\n".join(lines) + "\n")
        self.assert_aborts_zero_calls()

    def test_wrong_response_model_aborts(self):
        tamper_line(self.paths.responses, 0,
                    lambda r: r.update(response_model="some-other-model"))
        self.assert_aborts_zero_calls()


# ── Strong decision-ledger binding (Issue 1B) ─────────────────────────

class TestDecisionBinding(Arm3Fixture):

    def setUp(self):
        super().setUp()
        fabricate_responses_decisions(self.paths, start=1)

    def test_decision_without_matching_response_aborts(self):
        # remove the first response record but keep its decision
        lines = self.paths.responses.read_text().splitlines()
        self.paths.responses.write_text("\n".join(lines[1:]) + "\n")
        with self.assertRaises(SystemExit):
            ev.run_evaluator(self.paths, text_client("YES")[0], 10.0,
                             silent_log)

    def test_decision_wrong_candidate_sha_aborts(self):
        tamper_line(self.paths.decisions, 0,
                    lambda r: r.update(candidate_sha256="0" * 64))
        with self.assertRaises(SystemExit):
            ev.run_evaluator(self.paths, text_client("YES")[0], 10.0,
                             silent_log)

    def test_decision_wrong_raw_sha_aborts(self):
        tamper_line(self.paths.decisions, 0,
                    lambda r: r.update(raw_evaluator_response_sha256="f" * 64))
        with self.assertRaises(SystemExit):
            ev.run_evaluator(self.paths, text_client("YES")[0], 10.0,
                             silent_log)

    def test_decision_parser_mismatch_aborts(self):
        tamper_line(self.paths.decisions, 0,
                    lambda r: r.update(acceptance=0))  # raw says YES
        with self.assertRaises(SystemExit):
            ev.run_evaluator(self.paths, text_client("YES")[0], 10.0,
                             silent_log)

    def test_correctly_stored_invalid_verdict_is_legitimate(self):
        # Add task 0's response raw "MAYBE" + correctly parsed decision.
        t = self.tasks[0]
        signals = {s["task_id"]: s
                   for s in sig.load_jsonl(self.paths.signals)}
        res = ev.EvalResult(
            text="MAYBE", response_id="fake-0",
            response_model=acfg.EVALUATOR_MODEL, finish_reason="stop",
            prompt_tokens=600, completion_tokens=1, duration_s=0.1,
            transport_attempts=1, system_fingerprint=FAKE_FINGERPRINT,
            response_created=FAKE_CREATED, service_tier="default",
            request_id="req_fake")
        prompt = ev.build_evaluator_prompt(
            t["prompt"], t["candidate_code"],
            signals[t["task_id"]]["execution_treatment_text"])
        rr = ev.build_response_record(t, signals[t["task_id"]], res, prompt)
        ev.append_jsonl(self.paths.responses, rr)
        ev.append_jsonl(self.paths.decisions,
                        ev.build_decision_record(t, rr))
        client, comp = text_client("YES")
        rc = ev.run_evaluator(self.paths, client, 10.0, silent_log)
        self.assertEqual(rc, 3)          # established invalid-verdict halt
        self.assertEqual(comp.calls, 0)  # zero NEW API calls, no resampling


# ── Production completeness (Issue 2) ─────────────────────────────────

class TestProductionCompleteness(Arm3Fixture):

    def test_no_subset_parameter_exists(self):
        params = inspect.signature(ev.run_evaluator).parameters
        self.assertNotIn("frozen_records", params)
        self.assertNotIn("tasks", params)
        self.assertNotIn("limit", params)

    def test_full_certified_state_completes_with_zero_calls(self):
        fabricate_responses_decisions(self.paths)  # all 164
        client, comp = text_client("YES")
        rc = ev.run_evaluator(self.paths, client, 10.0, silent_log)
        self.assertEqual(rc, 0)
        self.assertEqual(comp.calls, 0)
        self.assertEqual(len(sig.load_jsonl(self.paths.responses)), 164)
        self.assertEqual(len(sig.load_jsonl(self.paths.decisions)), 164)

    def test_163_prepopulated_one_pending_completes_with_one_call(self):
        fabricate_responses_decisions(self.paths, start=1)
        client, comp = text_client("NO")
        rc = ev.run_evaluator(self.paths, client, 10.0, silent_log)
        self.assertEqual(rc, 0)
        self.assertEqual(comp.calls, 1)
        self.assertEqual(len(sig.load_jsonl(self.paths.responses)), 164)
        self.assertEqual(len(sig.load_jsonl(self.paths.decisions)), 164)


# ── Certification artifact ────────────────────────────────────────────

class TestCertificationArtifact(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="arm3_cert_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.paths = ev_paths(self.tmpdir)

    def test_successful_validator_writes_certification(self):
        fabricate_full_signals(sig.Paths(self.paths.signals,
                                         self.paths.failures, self.paths.log))
        rc, out = certify(self.paths)
        self.assertEqual(rc, 0)
        self.assertTrue(self.paths.certification.is_file())
        cert = json.loads(self.paths.certification.read_text())
        self.assertEqual(cert["certification_status"], "valid")
        self.assertEqual(cert["expected_tasks"], 164)
        self.assertEqual(cert["frozen_execution_signals"], 164)
        self.assertEqual(cert["execution_pass"], 155)
        self.assertEqual(cert["execution_fail"], 9)
        self.assertEqual(cert["execution_t0_mismatches"], 0)
        self.assertIsInstance(cert["certified_at"], str)
        self.assertTrue(cert["certified_at"].strip())
        self.assertEqual(cert["source_frozen_ledger_sha256"],
                         acfg.APPROVED_FROZEN_SHA256)
        self.assertEqual(cert["baseline_ledger_sha256"],
                         acfg.APPROVED_BASELINE_SHA256)

    def test_certification_contains_exact_signal_ledger_sha(self):
        fabricate_full_signals(sig.Paths(self.paths.signals,
                                         self.paths.failures, self.paths.log))
        rc, out = certify(self.paths)
        self.assertEqual(rc, 0)
        cert = json.loads(self.paths.certification.read_text())
        actual = hashlib.sha256(
            self.paths.signals.read_bytes()).hexdigest()
        self.assertEqual(cert["execution_signals_sha256"], actual)

    def test_t0_mismatch_fails_and_produces_no_certification(self):
        baseline = real_baseline()

        def flip(t):
            if t["task_index"] == 0:
                return "fail_assertion" if baseline[t["task_id"]] else "pass"
            return "pass" if baseline[t["task_id"]] else "fail_assertion"
        fabricate_full_signals(sig.Paths(self.paths.signals,
                                         self.paths.failures,
                                         self.paths.log), status_fn=flip)
        rc, out = certify(self.paths)
        self.assertEqual(rc, 1)
        self.assertIn("Execution/T0 mismatches: 1", out)
        self.assertFalse(self.paths.certification.is_file())

    def test_failed_validation_removes_preexisting_certification(self):
        fabricate_full_signals(sig.Paths(self.paths.signals,
                                         self.paths.failures, self.paths.log))
        rc, out = certify(self.paths)
        self.assertEqual(rc, 0)
        self.assertTrue(self.paths.certification.is_file())
        lines = self.paths.signals.read_text().splitlines()
        rec = json.loads(lines[0])
        if rec["execution_status"] == "pass":
            rec["execution_status"] = "fail"
            rec["execution_test_status"] = "fail_assertion"
            rec["execution_treatment_text"] = acfg.TREATMENT_FAIL
        else:
            rec["execution_status"] = "pass"
            rec["execution_test_status"] = "pass"
            rec["execution_treatment_text"] = acfg.TREATMENT_PASS
        lines[0] = json.dumps(rec)
        self.paths.signals.write_text("\n".join(lines) + "\n")
        rc, out = certify(self.paths)
        self.assertEqual(rc, 1)
        self.assertFalse(self.paths.certification.is_file())


# ── Blindness ─────────────────────────────────────────────────────────

class TestBlindness(unittest.TestCase):

    def test_no_arm3_runtime_reads_forbidden_ledgers(self):
        runtime = [Path(acfg.ARM_DIR) / "run_execution_signals.py",
                   Path(acfg.ARM_DIR) / "run_execution_evaluator.py"]
        for py in runtime:
            for lineno, line in enumerate(py.read_text().splitlines(), 1):
                code = line.split("#", 1)[0]
                if re.search(r"""(open|read_text|read_bytes|load_jsonl)\s*\(""",
                             code):
                    for forbidden in ("baseline.jsonl", "arm1", "arm2"):
                        self.assertNotIn(forbidden, code.lower(),
                                         f"{py}:{lineno}: {line}")
        for py in runtime:
            text = py.read_text()
            for forbidden in ("arm1_selfcheck", "arm2_steelman",
                              "baseline_correct"):
                self.assertNotIn(forbidden, text, f"{py}: {forbidden}")


# ── Final certifier (canonical strong validation) ─────────────────────

class TestFinalValidator(Arm3Fixture):

    def test_full_validator_passes(self):
        fabricate_responses_decisions(
            self.paths,
            verdict_fn=lambda t: "YES" if t["task_index"] % 4 else "NO")
        rc, out = run_final_validator(self.paths)
        self.assertEqual(rc, 0)
        self.assertIn("Frozen execution signals: 164", out)
        self.assertIn("Frozen evaluator responses: 164", out)
        self.assertIn("Parsed Arm 3 decisions: 164", out)
        self.assertIn("Execution/T0 mismatches: 0", out)
        self.assertIn("YES + NO: 164", out)
        self.assertIn("VALID ARM 3 EXECUTION POSITIVE CONTROL: YES", out)

    def test_rejects_tampered_raw_response(self):
        fabricate_responses_decisions(self.paths)
        tamper_line(self.paths.responses, 0,
                    lambda r: r.update(raw_evaluator_response="NO"))
        rc, out = run_final_validator(self.paths)
        self.assertEqual(rc, 1)
        self.assertIn("VALID ARM 3 EXECUTION POSITIVE CONTROL: NO", out)

    def test_rejects_wrong_treatment_in_response(self):
        fabricate_responses_decisions(self.paths)
        tamper_line(self.paths.responses, 0,
                    lambda r: r.update(
                        execution_treatment_text=acfg.TREATMENT_FAIL_TIMEOUT
                        if r["execution_treatment_text"] != acfg.TREATMENT_FAIL_TIMEOUT
                        else acfg.TREATMENT_PASS))
        rc, out = run_final_validator(self.paths)
        self.assertEqual(rc, 1)

    def test_rejects_timeout_signal_with_generic_treatment(self):
        fabricate_responses_decisions(self.paths)
        tamper_line(self.paths.signals, 0, lambda r: r.update(
            execution_test_status="timeout",
            execution_status="fail",
            execution_treatment_text=acfg.TREATMENT_FAIL))
        rc, out = run_final_validator(self.paths)
        self.assertEqual(rc, 1)

    def test_rejects_wrong_signal_task_index(self):
        fabricate_responses_decisions(self.paths)
        tamper_line(self.paths.signals, 0,
                    lambda r: r.update(task_index=999))
        rc, out = run_final_validator(self.paths)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
