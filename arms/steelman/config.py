"""Arm 2 (structured steelman / self-debate) protocol configuration.

Stage 0 and Arm 1 are CLOSED and IMMUTABLE. This module defines Arm 2
only. It must never grow execution-grounded evaluation, correction,
or cross-arm analysis.
"""
import sys
from pathlib import Path

ARM_DIR = Path(__file__).resolve().parent
BENCHMARK_DIR = ARM_DIR.parent.parent
sys.path.insert(0, str(BENCHMARK_DIR))

import config as root_config  # Stage 0 config (read-only reuse of constants)

# ── Identity ──────────────────────────────────────────────────────────
ARM_NAME = "steelman"
SCHEMA_VERSION = "1.0"
EXPERIMENT_VERSION = "arm2-steelman-v1"

# ── Source corpus (Stage 0, immutable) ────────────────────────────────
FROZEN_SOURCE_PATH = root_config.FROZEN_PATH
# Arm 2 must abort BEFORE ANY API CALL if the source ledger hash differs.
APPROVED_FROZEN_SHA256 = (
    "a08167e369c3b3ec353f7f0bd84f843e7add0cb80f2ad4eb498fca6dc6c53aa3"
)

# ── Arm 2 ledgers (separate from Stage 0 and Arm 1) ──────────────────
DATA_DIR = root_config.DATA_DIR
RESPONSES_PATH = DATA_DIR / "arm2_steelman_responses.jsonl"
DECISIONS_PATH = DATA_DIR / "arm2_steelman.jsonl"
RUN_LOG_PATH = DATA_DIR / "arm2_steelman_run_log.jsonl"
FAILURES_PATH = DATA_DIR / "arm2_steelman_failures.jsonl"

# ── Evaluator request (exact) ─────────────────────────────────────────
EVALUATOR_MODEL = "gpt-5.4-2026-03-05"   # same snapshot as Stage 0
EVALUATOR_TEMPERATURE = 0
EVALUATOR_REASONING_EFFORT = "none"
EVALUATOR_MAX_COMPLETION_TOKENS = 2048

# Exact evaluator prompt template. Instantiated per task with the verbatim
# HumanEval prompt and the exact frozen candidate_code. Never paraphrased.
EVALUATOR_PROMPT_TEMPLATE = (
    "You are critically evaluating a Python solution that you produced "
    "earlier for a programming problem.\n"
    "\n"
    "Your task is to determine whether the candidate is fully correct for "
    "all inputs allowed by the specification.\n"
    "\n"
    "Perform a structured adversarial evaluation before giving your "
    "verdict.\n"
    "\n"
    "First, make the strongest specific case that the candidate is WRONG. "
    "Look actively for edge cases, specification mismatches, incorrect "
    "assumptions, arithmetic or logical errors, and cases where apparently "
    "plausible code would fail.\n"
    "\n"
    "Second, make the strongest specific case that the candidate is "
    "CORRECT. Check whether the apparent concerns actually apply and "
    "whether the implementation satisfies the complete specification.\n"
    "\n"
    "Finally, decide which case survives scrutiny.\n"
    "\n"
    "You are evaluating the existing candidate only.\n"
    "\n"
    "Do not modify, repair, rewrite, or improve the candidate.\n"
    "Do not provide corrected code or an alternative implementation.\n"
    "Do not execute the code.\n"
    "Do not assume access to unit tests, reference answers, execution "
    "results, ground-truth labels, or any information not shown below.\n"
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
    "Respond using exactly this structure:\n"
    "\n"
    "CASE_AGAINST:\n"
    "<your strongest case that the candidate is wrong>\n"
    "\n"
    "CASE_FOR:\n"
    "<your strongest case that the candidate is correct>\n"
    "\n"
    "FINAL_VERDICT: YES\n"
    "\n"
    "Replace YES with NO if the candidate is not fully correct.\n"
    "\n"
    "The final line must be exactly FINAL_VERDICT: YES or "
    "FINAL_VERDICT: NO."
)

# ── Cost accounting (operational only; never modifies N) ─────────────
ARM_COST_CAP_USD = 10.00
PRICE_PER_MTOK = root_config.PRICE_PER_MTOK  # (2.50, 15.00)
