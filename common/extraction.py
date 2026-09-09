"""Deterministic, strictly non-repairing candidate extraction.

Invariant: THE RAW MODEL RESPONSE IS PRESERVED VERBATIM.

Rules, in order:

  A. raw_model_response is stored exactly as returned by the API
     (handled by the caller; see raw_response_sha256).

  B. Default:
         candidate_code     = raw_model_response          (verbatim)
         extraction_method  = "raw_exact"

  C. The ONLY permitted transformation:
     If the entire non-whitespace response consists of EXACTLY ONE
     Markdown code fence, optionally tagged "python" or "py"
     (case-insensitive), unwrap that outer fence and use its interior
     code exactly:
         extraction_method  = "outer_fence_unwrapped"

This module does NOT:
  - scan for a function definition;
  - delete prose;
  - delete leading program statements (constants, assignments, imports);
  - delete decorators;
  - choose the first of multiple fences (multiple fences => raw_exact);
  - concatenate fences;
  - prepend the HumanEval prompt (there is NO completion-style repair
    path; the generation prompt demands a complete standalone solution);
  - reindent, dedent, normalize logic, or repair syntax;
  - make another model call.

A response that violates the requested code-only format is valid
experimental output and will generally fail execution naturally.

The extractor needs no entry_point and no task prompt. It is a pure
function of the raw response, so the validator can re-run it on
raw_model_response and must obtain the identical candidate_code and
extraction_method.
"""
from __future__ import annotations

import re
from typing import Tuple

# Matches a response that is ENTIRELY one fenced code block:
#   ```python\n<interior>```   (tag optional, "python"/"py", case-insensitive)
# Leading/trailing whitespace of the whole response is ignored for the
# match; the interior group is captured exactly, byte for byte.
_OUTER_FENCE_RE = re.compile(
    r"```(?:python|py)?[^\S\n]*\n(.*?)```[^\S\n]*",
    re.DOTALL | re.IGNORECASE,
)


def extract_candidate(raw_response: str) -> Tuple[str, str]:
    """Return (candidate_code, extraction_method)."""
    raw_response = raw_response or ""
    text = raw_response.strip()
    # Exactly two fence markers total: one opener, one closer, and an
    # interior that cannot itself contain a fence. Two or more fenced
    # blocks (or any stray fence) therefore fall through to raw_exact.
    if text.count("```") == 2:
        m = _OUTER_FENCE_RE.fullmatch(text)
        if m:
            return m.group(1), "outer_fence_unwrapped"
    return raw_response, "raw_exact"
