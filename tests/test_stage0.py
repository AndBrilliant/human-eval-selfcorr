#!/usr/bin/env python3
"""Local regression tests for the two critical Stage 0 invariants, plus
validator rejection tests.

NO model/API calls anywhere: generation is driven by a FakeClient that
returns canned responses. Test execution (where used) is a local
subprocess only.

Run:  python3 tests/test_stage0.py   (from benchmark_v2/)
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

import config
from common.dataset import load_tasks
from common.extraction import extract_candidate
from common.model_client import GenerationResult
import generate_baseline as gb
import validate_baseline as vb


# ── Fakes ─────────────────────────────────────────────────────────────

FAKE_SYSTEM_FINGERPRINT = "fp_fake_9f3c2a"
FAKE_CREATED = 1777000000
FAKE_SERVICE_TIER = "default"
FAKE_REQUEST_ID = "req_fake_abc123"


class FakeCompletions:
    """Mimics openai chat.completions: records calls, returns canned text
    with representative API/backend provenance values."""

    def __init__(self, text_fn):
        self.text_fn = text_fn
        self.calls = 0
        self.kwargs_seen = []

    def create(self, **kwargs):
        self.calls += 1
        self.kwargs_seen.append(kwargs)
        text = self.text_fn(kwargs)
        return SimpleNamespace(
            id=f"fake-resp-{self.calls}",
            model=config.GENERATOR_MODEL,
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=text),
                finish_reason="stop",
            )],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
            system_fingerprint=FAKE_SYSTEM_FINGERPRINT,
            created=FAKE_CREATED,
            service_tier=FAKE_SERVICE_TIER,
            _request_id=FAKE_REQUEST_ID,
        )


def make_fake_client(text_fn):
    comp = FakeCompletions(text_fn)
    client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
    return client, comp


class RawCompletions:
    """Like FakeCompletions, but the fixture returns the ENTIRE response
    object (for structural/provenance tests, not text wrapping)."""

    def __init__(self, resp_fn):
        self.resp_fn = resp_fn
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return self.resp_fn(kwargs)


def make_raw_client(resp_fn):
    comp = RawCompletions(resp_fn)
    client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
    return client, comp


def tmp_paths(tmpdir):
    d = Path(tmpdir)
    return gb.Paths(frozen=d / "frozen.jsonl", baseline=d / "baseline.jsonl",
                    failures=d / "failures.jsonl", log=d / "log.jsonl")


def silent_log(event, **fields):
    pass


def run_validator(paths):
    argv = sys.argv
    sys.argv = ["validate_baseline.py", "--frozen", str(paths.frozen),
                "--baseline", str(paths.baseline)]
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = vb.main()
    finally:
        sys.argv = argv
    return rc, out.getvalue()


def fabricate_ledgers(paths, n=164, mutate_frozen=None, mutate_scored=None):
    """Write n fully consistent frozen+scored records without any API
    call or test execution (records are fabricated, internally valid)."""
    tasks = load_tasks()[:n]
    for t in tasks:
        gen = GenerationResult(
            text=f"def {t['entry_point']}(*args, **kwargs):\n    return None\n",
            response_id=f"fake-{t['task_id']}",
            response_model=config.GENERATOR_MODEL,
            finish_reason="stop",
            prompt_tokens=100, completion_tokens=50,
            duration_s=0.1, transport_attempts=1,
            system_fingerprint=FAKE_SYSTEM_FINGERPRINT,
            response_created=FAKE_CREATED,
            service_tier=FAKE_SERVICE_TIER,
            request_id=FAKE_REQUEST_ID,
        )
        gp = config.GENERATION_PROMPT_TEMPLATE.format(prompt=t["prompt"])
        fr = gb.build_frozen_record(t, gen, gp)
        if mutate_frozen:
            fr = mutate_frozen(t, fr)
        gb.append_jsonl(paths.frozen, fr)
        sr = gb.build_scored_record(fr, "fail_assertion",
                                    "assertion failed: synthetic", False, 0.1, 0)
        if mutate_scored:
            sr = mutate_scored(t, sr)
        gb.append_jsonl(paths.baseline, sr)


# ── A. Extraction invariants ──────────────────────────────────────────

class TestExtraction(unittest.TestCase):

    def test_constants_retained(self):
        raw = "CONST = 3\n\ndef f(x):\n    return x + CONST\n"
        code, method = extract_candidate(raw)
        self.assertEqual(method, "raw_exact")
        self.assertEqual(code, raw)  # byte-identical, CONST intact

    def test_decorator_retained(self):
        raw = "import functools\n\n@functools.lru_cache(maxsize=None)\ndef f(n):\n    return n\n"
        code, method = extract_candidate(raw)
        self.assertEqual(method, "raw_exact")
        self.assertEqual(code, raw)

    def test_prose_not_cleaned(self):
        raw = "Here is my solution:\ndef f(x):\n    return x\nHope this helps!"
        code, method = extract_candidate(raw)
        self.assertEqual(method, "raw_exact")
        self.assertEqual(code, raw)  # prose preserved, not deleted

    def test_multiple_fences_not_reduced(self):
        raw = "```python\ndef f():\n    return 1\n```\n\n```python\ndef g():\n    return 2\n```"
        code, method = extract_candidate(raw)
        self.assertEqual(method, "raw_exact")
        self.assertEqual(code, raw)  # both fences preserved verbatim

    def test_single_whole_response_fence_unwrapped(self):
        interior = "CONST = 3\n\ndef f(x):\n        return x + CONST\n"
        raw = f"```python\n{interior}```"
        code, method = extract_candidate(raw)
        self.assertEqual(method, "outer_fence_unwrapped")
        self.assertEqual(code, interior)  # interior exact, indentation intact

    def test_fence_tag_case_insensitive(self):
        raw = "```PyThOn\ndef f():\n    return 1\n```"
        code, method = extract_candidate(raw)
        self.assertEqual(method, "outer_fence_unwrapped")
        self.assertEqual(code, "def f():\n    return 1\n")

    def test_bare_and_py_tags_unwrapped(self):
        for tag in ("```", "```py"):
            raw = f"{tag}\ndef f():\n    return 1\n```"
            code, method = extract_candidate(raw)
            self.assertEqual(method, "outer_fence_unwrapped", tag)
            self.assertEqual(code, "def f():\n    return 1\n")

    def test_non_python_fence_not_unwrapped(self):
        raw = "```javascript\nfunction f() { return 1; }\n```"
        code, method = extract_candidate(raw)
        self.assertEqual(method, "raw_exact")
        self.assertEqual(code, raw)

    def test_fence_with_surrounding_prose_not_unwrapped(self):
        raw = "Sure! Here you go:\n```python\ndef f():\n    return 1\n```"
        code, method = extract_candidate(raw)
        self.assertEqual(method, "raw_exact")
        self.assertEqual(code, raw)

    def test_empty_response(self):
        code, method = extract_candidate("")
        self.assertEqual((code, method), ("", "raw_exact"))

    def test_deterministic(self):
        raw = "```python\ndef f():\n    return 1\n```"
        self.assertEqual(extract_candidate(raw), extract_candidate(raw))


# ── B. Freeze-before-test invariant ───────────────────────────────────

class TestFreezeBeforeTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="hev2_test_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.tasks = load_tasks()[:3]
        self.paths = tmp_paths(self.tmpdir)

    def test_request_parameters(self):
        """The exact model request parameters are pinned."""
        captured = {}

        def fn(kwargs):
            captured.update(kwargs)
            return "def placeholder(*a, **k):\n    return None\n"

        client, comp = make_fake_client(fn)
        rc = gb.run(self.paths, client, cap_usd=25.0, limit=None,
                    log=silent_log, tasks=self.tasks)
        self.assertEqual(rc, 0)
        self.assertEqual(comp.calls, 3)
        self.assertEqual(captured["model"], "gpt-5.4-2026-03-05")
        self.assertEqual(captured["temperature"], 0)
        self.assertEqual(captured["reasoning_effort"], "none")
        self.assertEqual(captured["max_completion_tokens"],
                         config.MAX_COMPLETION_TOKENS)

    def test_frozen_written_before_tests_and_never_regenerated(self):
        """Simulated TestInfraError after freezing, then resume:
        generation is NOT invoked again; the identical candidate/hash
        is re-tested."""
        responses = {}

        def fn(kwargs):
            return "def placeholder(*a, **k):\n    return None\n"

        client, comp = make_fake_client(fn)

        # Run 1: generation succeeds (3 frozen), testing infra fails hard.
        real_run_tests_once = gb.run_tests_once

        def boom(*a, **k):
            raise gb.TestInfraError("simulated spawn failure")

        gb.run_tests_once = boom
        try:
            rc1 = gb.run(self.paths, client, cap_usd=25.0, limit=None,
                         log=silent_log, tasks=self.tasks)
        finally:
            gb.run_tests_once = real_run_tests_once
        self.assertEqual(rc1, 1)  # incomplete: scoring failed
        self.assertEqual(comp.calls, 3)

        frozen1 = gb.validate_frozen_ledger(self.paths.frozen)
        self.assertEqual(len(frozen1), 3)
        hashes1 = {t: r["candidate_sha256"] for t, r in frozen1.items()}
        for t, r in frozen1.items():
            responses[t] = r["raw_model_response"]

        # baseline.jsonl must be empty/absent; failure records exist.
        self.assertFalse(self.paths.baseline.is_file())
        failures = gb.load_jsonl(self.paths.failures)
        self.assertEqual(len(failures), 3)
        self.assertTrue(all(f["failure_stage"] == "testing" for f in failures))

        # Run 2 (resume): testing now works. Generation must NOT be called.
        rc2 = gb.run(self.paths, client, cap_usd=25.0, limit=None,
                     log=silent_log, tasks=self.tasks)
        self.assertEqual(rc2, 0)
        self.assertEqual(comp.calls, 3, "model was called again for a frozen task!")

        frozen2 = gb.validate_frozen_ledger(self.paths.frozen)
        self.assertEqual(hashes1,
                         {t: r["candidate_sha256"] for t, r in frozen2.items()})
        for t, r in frozen2.items():
            self.assertEqual(responses[t], r["raw_model_response"])

        scored = gb.load_scored_ledger(self.paths.baseline)
        self.assertEqual(len(scored), 3)
        for t, r in scored.items():
            self.assertEqual(r["candidate_sha256"], hashes1[t])
            self.assertIn("test_python_executable", r)
            self.assertIn("test_python_version", r)

    def test_test_failing_candidate_not_regenerated_on_resume(self):
        """A candidate that fails the reference tests is valid data and
        must never be regenerated."""
        client, comp = make_fake_client(
            lambda kw: "def placeholder(*a, **k):\n    return None\n")
        rc1 = gb.run(self.paths, client, cap_usd=25.0, limit=None,
                     log=silent_log, tasks=self.tasks)
        self.assertEqual(rc1, 0)
        scored1 = gb.load_scored_ledger(self.paths.baseline)
        self.assertTrue(any(r["baseline_correct"] is False
                            for r in scored1.values()))
        rc2 = gb.run(self.paths, client, cap_usd=25.0, limit=None,
                     log=silent_log, tasks=self.tasks)
        self.assertEqual(rc2, 0)
        self.assertEqual(comp.calls, 3, "incorrect candidate was regenerated!")

    def test_protocol_mismatch_refuses_resume(self):
        client, comp = make_fake_client(
            lambda kw: "def placeholder(*a, **k):\n    return None\n")
        gb.run(self.paths, client, cap_usd=25.0, limit=None,
               log=silent_log, tasks=self.tasks)
        # Tamper protocol metadata in the frozen ledger.
        lines = self.paths.frozen.read_text().splitlines()
        rec = json.loads(lines[0])
        rec["generator_model"] = "gpt-5.4"  # moving alias, not the snapshot
        lines[0] = json.dumps(rec)
        self.paths.frozen.write_text("\n".join(lines) + "\n")
        with self.assertRaises(SystemExit):
            gb.validate_frozen_ledger(self.paths.frozen)


# ── C. Validator rejections ───────────────────────────────────────────

class TestValidator(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="hev2_val_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.paths = tmp_paths(self.tmpdir)

    def test_valid_full_baseline(self):
        fabricate_ledgers(self.paths)
        rc, out = run_validator(self.paths)
        self.assertEqual(rc, 0)
        self.assertIn("Frozen candidate records: 164", out)
        self.assertIn("Scored baseline records: 164", out)
        self.assertIn("N0 + N1: 164", out)
        self.assertIn("VALID BASELINE: YES", out)

    def test_rejects_modified_candidate(self):
        def mutate(t, sr):
            sr["candidate_code"] += "\n# tampered\n"  # hash now stale too
            return sr
        fabricate_ledgers(self.paths, mutate_scored=mutate)
        rc, out = run_validator(self.paths)
        self.assertEqual(rc, 1)
        self.assertIn("VALID BASELINE: NO", out)

    def test_rejects_modified_raw_response(self):
        def mutate(t, fr):
            fr["raw_model_response"] += "\n# tampered\n"  # stored sha stale
            return fr
        fabricate_ledgers(self.paths, mutate_frozen=mutate)
        rc, out = run_validator(self.paths)
        self.assertEqual(rc, 1)
        self.assertIn("Raw-response hash mismatches: 164", out)

    def test_rejects_modified_task_prompt(self):
        def mutate(t, fr):
            fr["prompt"] = fr["prompt"] + "\n# tampered prompt\n"
            return fr
        fabricate_ledgers(self.paths, mutate_frozen=mutate)
        rc, out = run_validator(self.paths)
        self.assertEqual(rc, 1)
        self.assertIn("VALID BASELINE: NO", out)

    def test_rejects_wrong_model(self):
        def mutate(t, fr):
            fr["generator_model"] = "gpt-5.4"
            return fr
        fabricate_ledgers(self.paths, mutate_frozen=mutate)
        rc, out = run_validator(self.paths)
        self.assertEqual(rc, 1)
        self.assertIn("Protocol metadata mismatches: 164", out)

    def test_rejects_mixed_response_models(self):
        def mutate(t, fr):
            if t["task_index"] == 0:
                fr["response_model"] = "some-other-model"
            return fr
        fabricate_ledgers(self.paths, mutate_frozen=mutate)
        rc, out = run_validator(self.paths)
        self.assertEqual(rc, 1)
        self.assertIn("VALID BASELINE: NO", out)

    def test_rejects_incomplete_163(self):
        fabricate_ledgers(self.paths, n=163)
        rc, out = run_validator(self.paths)
        self.assertEqual(rc, 1)
        self.assertIn("Frozen candidate records: 163", out)
        self.assertIn("Missing candidates: 1", out)
        self.assertIn("VALID BASELINE: NO", out)

    def test_rejects_scored_without_frozen(self):
        fabricate_ledgers(self.paths)
        # Remove one frozen record; leave its scored record behind.
        keep = [l for l in self.paths.frozen.read_text().splitlines()
                if '"HumanEval/5"' not in l]
        self.paths.frozen.write_text("\n".join(keep) + "\n")
        rc, out = run_validator(self.paths)
        self.assertEqual(rc, 1)
        self.assertIn("VALID BASELINE: NO", out)


# ── D. Subprocess environment sanitization ────────────────────────────

class TestEnvSanitization(unittest.TestCase):

    def test_child_cannot_see_parent_api_key(self):
        """Fake OPENAI_API_KEY in the parent environment must not be
        visible to the candidate subprocess; PYTHONHASHSEED must be 0."""
        from common.execution import run_tests_once
        os.environ["OPENAI_API_KEY"] = "fake-secret-key-for-test"
        os.environ["SOME_OTHER_TOKEN"] = "fake-token-for-test"
        try:
            candidate = (
                "import os\n"
                "def probe():\n"
                "    leaked = sorted(\n"
                "        k for k in os.environ\n"
                "        if k == 'OPENAI_API_KEY' or k.endswith('_API_KEY')\n"
                "        or k.endswith('_TOKEN')\n"
                "    )\n"
                "    return leaked, os.environ.get('PYTHONHASHSEED')\n"
            )
            test_code = (
                "def check(candidate):\n"
                "    leaked, hashseed = candidate()\n"
                "    assert leaked == [], leaked\n"
                "    assert hashseed == '0', hashseed\n"
            )
            status, details, correct, dur = run_tests_once(
                candidate, test_code, "probe", 15.0)
            self.assertTrue(correct, f"{status}: {details}")
        finally:
            del os.environ["OPENAI_API_KEY"]
            del os.environ["SOME_OTHER_TOKEN"]


# ── E. API/backend provenance survival ────────────────────────────────

class TestProvenance(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="hev2_prov_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.tasks = load_tasks()[:3]
        self.paths = tmp_paths(self.tmpdir)

    def test_provenance_survives_to_frozen_ledger(self):
        """fake API response -> GenerationResult -> frozen_candidates.jsonl
        with values unchanged."""
        client, comp = make_fake_client(
            lambda kw: "def placeholder(*a, **k):\n    return None\n")
        rc = gb.run(self.paths, client, cap_usd=25.0, limit=None,
                    log=silent_log, tasks=self.tasks)
        self.assertEqual(rc, 0)
        frozen = gb.validate_frozen_ledger(self.paths.frozen)
        self.assertEqual(len(frozen), 3)
        for tid, rec in frozen.items():
            self.assertEqual(rec["system_fingerprint"], FAKE_SYSTEM_FINGERPRINT)
            self.assertEqual(rec["response_created"], FAKE_CREATED)
            self.assertEqual(rec["service_tier"], FAKE_SERVICE_TIER)
            self.assertEqual(rec["request_id"], FAKE_REQUEST_ID)
            self.assertEqual(rec["response_model"], config.GENERATOR_MODEL)

    def test_unavailable_optional_provenance_stored_as_null(self):
        """If the API response lacks optional provenance, store null,
        do not silently omit the field."""
        gen = GenerationResult(
            text="x", response_id="r", response_model=config.GENERATOR_MODEL,
            finish_reason="stop", prompt_tokens=1, completion_tokens=1,
            duration_s=0.1, transport_attempts=1,
        )
        self.assertIsNone(gen.system_fingerprint)
        self.assertIsNone(gen.service_tier)
        self.assertIsNone(gen.request_id)
        rec = gb.build_frozen_record(
            self.tasks[0], gen, "prompt-text")
        for field in ("system_fingerprint", "response_created",
                      "service_tier", "request_id"):
            self.assertIn(field, rec)
            self.assertIsNone(rec[field])

    def test_empty_string_content_accepted(self):
        """content='' is valid experimental data: accepted after exactly
        one call, yielding an empty candidate."""
        from common.model_client import generate_one_candidate

        def empty_string(kwargs):
            return SimpleNamespace(
                id="r", model=config.GENERATOR_MODEL,
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content=""), finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))

        client, comp = make_raw_client(empty_string)
        result = generate_one_candidate(client, "p", silent_log, task_id="T")
        self.assertEqual(comp.calls, 1)
        self.assertEqual(result.text, "")
        self.assertIsInstance(result.text, str)

    def test_structural_checks_reject_malformed_response(self):
        """Structurally malformed metadata (multiple choices, empty
        response_model, content=None, non-string content) is a FATAL
        protocol failure BEFORE freezing: exactly ONE API call, immediate
        fatal raise, no retry, no freeze."""
        from common.model_client import (
            FatalConfigError, StructuralResponseError,
            generate_one_candidate)

        def multi_choice(kwargs):
            c = SimpleNamespace(message=SimpleNamespace(content="x"),
                                finish_reason="stop")
            return SimpleNamespace(
                id="r", model=config.GENERATOR_MODEL, choices=[c, c],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))

        def empty_model(kwargs):
            return SimpleNamespace(
                id="r", model="",
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content="x"), finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))

        def none_content(kwargs):
            return SimpleNamespace(
                id="r", model=config.GENERATOR_MODEL,
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content=None), finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))

        def non_string_content(kwargs):
            return SimpleNamespace(
                id="r", model=config.GENERATOR_MODEL,
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content=["not", "a", "string"]),
                    finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))

        for fn in (multi_choice, empty_model, none_content, non_string_content):
            client, comp = make_raw_client(fn)
            with self.assertRaises(StructuralResponseError):
                generate_one_candidate(client, "p", silent_log, task_id="T")
            # fatal immediately: exactly one call, zero retries, no freeze
            self.assertEqual(comp.calls, 1)
            # and it is a FatalConfigError subclass (halts the Stage 0 run)
            self.assertTrue(issubclass(StructuralResponseError, FatalConfigError))


# ── F. Validator fingerprint reporting ────────────────────────────────

class TestValidatorFingerprints(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="hev2_fp_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.paths = tmp_paths(self.tmpdir)

    def test_fingerprints_reported_not_failed(self):
        """Differing system fingerprints appear in the summary but are
        NOT a validity failure."""
        def mutate(t, fr):
            if t["task_index"] % 2 == 0:
                fr["system_fingerprint"] = "fp_other_value"
            return fr
        fabricate_ledgers(self.paths, mutate_frozen=mutate)
        rc, out = run_validator(self.paths)
        self.assertEqual(rc, 0)
        self.assertIn("Unique system fingerprints:", out)
        self.assertIn("fp_other_value", out)
        self.assertIn("VALID BASELINE: YES", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
