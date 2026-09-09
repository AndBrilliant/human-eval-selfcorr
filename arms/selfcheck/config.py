"""Arm 1 (pure self-check) protocol configuration.

Stage 0 is CLOSED and IMMUTABLE. This module defines Arm 1 only. It must
never grow steelman/debate, execution-grounded evaluation, or analysis.
"""
import sys
from pathlib import Path

ARM_DIR = Path(__file__).resolve().parent
BENCHMARK_DIR = ARM_DIR.parent.parent
sys.path.insert(0, str(BENCHMARK_DIR))

import config as root_config  # Stage 0 config (read-only reuse of constants)

# ── Identity ──────────────────────────────────────────────────────────
ARM_NAME = "selfcheck"
SCHEMA_VERSION = "1.0"
EXPERIMENT_VERSION = "arm1-selfcheck-v1"

# ── Source corpus (Stage 0, immutable) ────────────────────────────────
FROZEN_SOURCE_PATH = root_config.FROZEN_PATH
# Arm 1 must abort BEFORE ANY API CALL if the source ledger hash differs.
APPROVED_FROZEN_SHA256 = (
    "a08167e369c3b3ec353f7f0bd84f843e7add0cb80f2ad4eb498fca6dc6c53aa3"
)

# ── Arm 1 ledgers (separate from Stage 0) ─────────────────────────────
DATA_DIR = root_config.DATA_DIR
RESPONSES_PATH = DATA_DIR / "arm1_selfcheck_responses.jsonl"
DECISIONS_PATH = DATA_DIR / "arm1_selfcheck.jsonl"
RUN_LOG_PATH = DATA_DIR / "arm1_selfcheck_run_log.jsonl"
FAILURES_PATH = DATA_DIR / "arm1_selfcheck_failures.jsonl"

# ── Evaluator request (exact) ─────────────────────────────────────────
EVALUATOR_MODEL = "gpt-5.4-2026-03-05"   # same snapshot as Stage 0
EVALUATOR_TEMPERATURE = 0
EVALUATOR_REASONING_EFFORT = "none"
EVALUATOR_MAX_COMPLETION_TOKENS = 16

# Exact evaluator prompt template. Instantiated per task with the verbatim
# HumanEval prompt and the exact frozen candidate_code. Never paraphrased.
EVALUATOR_PROMPT_TEMPLATE = (
    "You are checking a Python solution that you produced earlier for a "
    "programming problem.\n"
    "\n"
    "Determine whether the candidate solution is fully correct for all "
    "inputs allowed by the problem specification.\n"
    "\n"
    "Do not modify, repair, rewrite, or improve the solution.\n"
    "Do not propose an alternative solution.\n"
    "Do not execute the code.\n"
    "Do not assume access to unit tests, reference answers, execution "
    "results, or any information not shown below.\n"
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
    "Is the candidate solution fully correct?\n"
    "\n"
    "Answer with exactly YES or NO.\n"
    "Do not output anything else."
)

# ── Cost accounting (operational only; never modifies N) ─────────────
ARM_COST_CAP_USD = 10.00
PRICE_PER_MTOK = root_config.PRICE_PER_MTOK  # (2.50, 15.00)
