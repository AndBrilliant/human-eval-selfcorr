"""Arm 3 (execution-grounded positive control) protocol configuration.

Stage 0, Arm 1, and Arm 2 are CLOSED and IMMUTABLE. This module defines
Arm 3 only. Arm 3 contains NO correction or revision phase: the frozen
candidate enters and leaves unchanged; T0 never changes.
"""
import sys
from pathlib import Path

ARM_DIR = Path(__file__).resolve().parent
BENCHMARK_DIR = ARM_DIR.parent.parent
sys.path.insert(0, str(BENCHMARK_DIR))

import config as root_config  # Stage 0 config (read-only reuse of constants)

# ── Identity ──────────────────────────────────────────────────────────
ARM_NAME = "execution_grounded"
SCHEMA_VERSION = "1.0"
EXPERIMENT_VERSION = "arm3-execution-v1"

# ── Source corpus (Stage 0, immutable) ────────────────────────────────
FROZEN_SOURCE_PATH = root_config.FROZEN_PATH
# Arm 3 must abort BEFORE ANY API CALL if the source ledger hash differs.
APPROVED_FROZEN_SHA256 = (
    "a08167e369c3b3ec353f7f0bd84f843e7add0cb80f2ad4eb498fca6dc6c53aa3"
)
# Used ONLY by the dedicated execution-signal integrity validator and the
# final certifier — never by the evaluator request path.
APPROVED_BASELINE_SHA256 = (
    "bf52d7622141788c51b0c5a5870b225c28424f5f5cf834e0f5086ba13a827a04"
)
BASELINE_PATH = root_config.BASELINE_PATH

# ── Arm 3 ledgers (separate from Stage 0, Arm 1, Arm 2) ──────────────
DATA_DIR = root_config.DATA_DIR
SIGNALS_PATH = DATA_DIR / "arm3_execution_signals.jsonl"
RESPONSES_PATH = DATA_DIR / "arm3_execution_responses.jsonl"
DECISIONS_PATH = DATA_DIR / "arm3_execution.jsonl"
RUN_LOG_PATH = DATA_DIR / "arm3_execution_run_log.jsonl"
FAILURES_PATH = DATA_DIR / "arm3_execution_failures.jsonl"
# Written ONLY by validate_execution_signals.py after successful
# certification of the COMPLETE 164-task signal ledger. Phase B refuses
# to construct an API client unless this artifact certifies the CURRENT
# signal ledger (matching execution_signals_sha256).
CERTIFICATION_PATH = DATA_DIR / "arm3_execution_signal_certification.json"

# ── Execution treatment texts (EXACT; reveal only PASS vs FAIL) ──────
TREATMENT_PASS = (
    "EXECUTION RESULT: PASS\n"
    "The candidate passed all reference tests."
)
TREATMENT_FAIL = (
    "EXECUTION RESULT: FAIL\n"
    "The candidate failed at least one reference test."
)
TREATMENT_FAIL_TIMEOUT = (
    "EXECUTION RESULT: FAIL\n"
    "The candidate did not complete within the execution timeout."
)

# ── Evaluator request (exact) ─────────────────────────────────────────
EVALUATOR_MODEL = "gpt-5.4-2026-03-05"   # same snapshot as Stage 0
EVALUATOR_TEMPERATURE = 0
EVALUATOR_REASONING_EFFORT = "none"
EVALUATOR_MAX_COMPLETION_TOKENS = 16

# Exact evaluator prompt template. Instantiated per task with the verbatim
# HumanEval prompt, the exact frozen candidate_code, and the exact
# PASS/FAIL treatment text. Never paraphrased.
EVALUATOR_PROMPT_TEMPLATE = (
    "You are checking a Python solution that you produced earlier for a "
    "programming problem.\n"
    "\n"
    "Determine whether the candidate solution is fully correct for all "
    "inputs allowed by the problem specification.\n"
    "\n"
    "You have been given the result of executing this exact candidate "
    "against the reference tests.\n"
    "\n"
    "Do not modify, repair, rewrite, or improve the solution.\n"
    "Do not propose an alternative solution.\n"
    "Do not infer or invent any execution information beyond the result "
    "shown.\n"
    "Evaluate the existing candidate only.\n"
    "\n"
    "Programming problem:\n"
    "---BEGIN PROBLEM---\n"
    "{task_prompt}\n"
    "---END PROBLEM---\n"
    "\n"
    "Your previously produced solution:\n"
    "---BEGIN CANDIDATE---\n"
    "{candidate_code}\n"
    "---END CANDIDATE---\n"
    "\n"
    "Execution grounding:\n"
    "---BEGIN EXECUTION RESULT---\n"
    "{execution_treatment}\n"
    "---END EXECUTION RESULT---\n"
    "\n"
    "Is the candidate solution fully correct?\n"
    "\n"
    "Answer with exactly YES or NO.\n"
    "Do not output anything else."
)

# ── Cost accounting (operational only; never modifies N) ─────────────
ARM_COST_CAP_USD = 10.00
PRICE_PER_MTOK = root_config.PRICE_PER_MTOK  # (2.50, 15.00)
