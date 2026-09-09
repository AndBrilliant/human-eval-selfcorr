"""Shared constants for benchmark_v2 Stage 0 (immutable baseline generation).

Stage 0 ONLY. This module must never grow evaluator-arm logic (no self-check,
no steelman/debate, no revision, no acceptance decisions, no statistics).

Experimental sampling statement:
    Each HumanEval task contributes exactly one successfully returned and
    frozen candidate. Transient transport failures may cause the identical
    logical request to be reissued; retries are infrastructure-driven and
    never conditioned on candidate content.
"""
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parent
DATA_DIR = BENCHMARK_DIR / "data"

# ── On-disk ledgers ───────────────────────────────────────────────────
# frozen_candidates.jsonl: written IMMEDIATELY after each successful API
# response (extract + hash + fsync), BEFORE any test execution. Once a
# task_id exists here, no code path may call the model for it again.
FROZEN_PATH = DATA_DIR / "frozen_candidates.jsonl"
# baseline.jsonl: scored records, derived exclusively from frozen candidates.
BASELINE_PATH = DATA_DIR / "baseline.jsonl"
# failures.jsonl: explicit infrastructure-failure records (never silent drops).
FAILURES_PATH = DATA_DIR / "failures.jsonl"
RUN_LOG_PATH = DATA_DIR / "run_log.jsonl"

# ── Dataset ───────────────────────────────────────────────────────────
# Canonical OpenAI HumanEval (164 tasks), verified 2026-09-09:
#   - 164 lines, task_ids HumanEval/0 .. HumanEval/163, canonical order
#   - official fields: task_id, prompt, entry_point, canonical_solution, test
#   - every test field defines check(candidate)
DATASET_PATH = Path("/home/Drew/human-eval-source-data/HumanEval.jsonl")
DATASET_SHA256 = "1d49078ba3e2b196b9344535bef34a43021f038fad9561d6ee7c53450609a6a2"
EXPECTED_N_TASKS = 164

# ── Record identity ───────────────────────────────────────────────────
SCHEMA_VERSION = "1.1"
EXPERIMENT_VERSION = "stage0-baseline-v1"

# ── Generator ─────────────────────────────────────────────────────────
# Immutable snapshot, not a moving alias. Access conventions follow the
# existing environment (mcp-servers/paper-tools/check-citations/
# api_client.py): OpenAI chat completions, key from ~/.keys/openai,
# GPT-5.4-class models require max_completion_tokens.
GENERATOR_MODEL = "gpt-5.4-2026-03-05"
GENERATOR_TEMPERATURE = 0
# reasoning_effort is a first-class create() parameter in openai SDK 1.75.0.
# The SDK's declared Literal is low/medium/high and does not list "none";
# the SDK does not enforce it client-side, so "none" is sent verbatim.
# If the snapshot rejects it server-side (400), the run aborts loudly
# (FatalConfigError) BEFORE any task is frozen. No silent fallback.
REASONING_EFFORT = "none"
OPENAI_KEY_FILE = Path.home() / ".keys" / "openai"
API_TIMEOUT_S = 300.0          # per-request timeout (reasoning models are slow)
MAX_COMPLETION_TOKENS = 8192

# The generation prompt asks ONLY for a solution to the programming problem.
# No evaluator language, no self-critique, no execution results, no hints.
GENERATION_PROMPT_TEMPLATE = (
    "Complete the following Python programming problem. Write the complete "
    "solution in Python, including the function definition and any imports "
    "it requires.\n"
    "Return only Python code. Do not include tests, examples, or "
    "explanations.\n\n"
    "{prompt}"
)

# ── Infrastructure transport retry policy ─────────────────────────────
# Applies ONLY to transient transport errors (connection, timeout, rate
# limit, HTTP 408/409/425/429/5xx). A retry reissues the identical logical
# request; it is never conditioned on candidate content and never counts
# as a new candidate. The number of transport attempts is recorded per task.
GEN_MAX_ATTEMPTS = 8
GEN_BACKOFF_BASE_S = 5.0       # exponential backoff, full jitter
GEN_BACKOFF_FACTOR = 2.0
GEN_BACKOFF_MAX_S = 300.0

# ── Test execution ────────────────────────────────────────────────────
# HumanEval reference tests executed against the frozen candidate in a
# fresh Python subprocess, with a 15-second per-task timeout. A candidate
# timeout is an INCORRECT solution (test_status="timeout",
# baseline_correct=false), not an infrastructure exclusion, and is never
# retried. Only local spawn/IO failures are retried (identical test).
TEST_TIMEOUT_S = 15.0
TEST_MAX_ATTEMPTS = 3
TEST_BACKOFF_BASE_S = 2.0
ALLOWED_TEST_STATUSES = ("pass", "fail_assertion", "fail_error", "timeout")

# ── Cost accounting (operational, not a scientific exclusion rule) ────
# USD per 1M tokens (input, output). Cumulative across resume runs:
# spend stored in already-frozen records is summed, then new generation
# cost is added. Restarting the process never resets spend to zero.
PRICE_PER_MTOK = (2.50, 15.00)
DEFAULT_COST_CAP_USD = 25.00
