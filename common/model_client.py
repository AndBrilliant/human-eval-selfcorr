"""GPT-5.4 generation client for benchmark_v2 Stage 0.

Reuses the environment's existing access conventions (see
mcp-servers/paper-tools/check-citations/api_client.py):
  - API key read from ~/.keys/openai into OPENAI_API_KEY
  - OpenAI chat completions endpoint
  - GPT-5.4-class models require max_completion_tokens (not max_tokens)

Deliberate differences from the old harness client (infrastructure fixes,
not experimental logic):
  - The SDK's built-in retries are DISABLED (max_retries=0) so every
    transport retry is explicit, logged, and auditable.
  - Full response metadata (response id, model, finish_reason, usage)
    is returned for the audit record, not just the text.
  - reasoning_effort is passed explicitly (first-class create() parameter
    in openai SDK 1.75.0).

Transport retry discipline (experimental statement):
    Each HumanEval task contributes exactly one successfully returned and
    frozen candidate. Transient transport failures may cause the identical
    logical request to be reissued; retries are infrastructure-driven and
    never conditioned on candidate content. We do NOT claim that transport
    retries are literally the same server-side model sample; the number of
    transport attempts is recorded per task.

Retryable: connection errors, timeouts, rate limits, HTTP
408/409/425/429/5xx. Non-retryable errors (other 4xx, e.g. auth/config or
a rejected reasoning_effort value) raise FatalConfigError and abort the
whole run loudly rather than fabricating per-task failures.
"""
from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import openai

import config


class FatalConfigError(RuntimeError):
    """Non-retryable API/configuration error. Abort the run loudly."""


class InfrastructureExhausted(RuntimeError):
    """All transport retries for one task were exhausted."""


class StructuralResponseError(FatalConfigError):
    """The API returned structurally malformed response metadata (not
    exactly one choice, empty response_model, or content that is not a
    Python str — including content=None).

    This is a FATAL protocol failure detected BEFORE freezing. It is NOT
    retryable: the API already returned a response, and reissuing the
    request would constitute another model sample. One returned malformed
    response -> abort loudly -> no freeze -> no second model request.
    A content value of "" (empty string) IS valid experimental data."""


@dataclass
class GenerationResult:
    text: str
    response_id: Optional[str]
    response_model: Optional[str]
    finish_reason: Optional[str]
    prompt_tokens: int
    completion_tokens: int
    duration_s: float
    transport_attempts: int   # total transport attempts (1 = no retries)
    # API/backend provenance; null when genuinely unavailable.
    system_fingerprint: Optional[str] = None
    response_created: Optional[int] = None
    service_tier: Optional[str] = None
    request_id: Optional[str] = None


def load_api_key(key_file: Path = config.OPENAI_KEY_FILE) -> None:
    """Mirror the existing convention: ~/.keys/openai -> OPENAI_API_KEY."""
    if os.environ.get("OPENAI_API_KEY"):
        return
    key_file = Path(key_file)
    if not key_file.is_file():
        raise FatalConfigError(f"OpenAI key file missing: {key_file}")
    key = key_file.read_text().strip()
    if not key:
        raise FatalConfigError(f"OpenAI key file empty: {key_file}")
    os.environ["OPENAI_API_KEY"] = key


def make_client() -> openai.OpenAI:
    load_api_key()
    # max_retries=0: all retries are ours, logged, and never silent.
    return openai.OpenAI(timeout=config.API_TIMEOUT_S, max_retries=0)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (openai.APIConnectionError,
                        openai.APITimeoutError,
                        openai.RateLimitError,
                        openai.InternalServerError)):
        return True
    if isinstance(exc, openai.APIStatusError):
        code = getattr(exc, "status_code", None)
        return code in (408, 409, 425, 429) or (code is not None and code >= 500)
    return False


def generate_one_candidate(
    client: openai.OpenAI,
    generation_prompt: str,
    log_event: Callable[..., None],
    task_id: str = "",
) -> GenerationResult:
    """Return one successfully returned candidate for one task.

    Transient transport errors reissue the identical logical request with
    exponential backoff + full jitter; every retry is logged via log_event.
    Raises InfrastructureExhausted if all attempts fail, FatalConfigError
    for non-retryable errors.
    """
    for attempt in range(1, config.GEN_MAX_ATTEMPTS + 1):
        try:
            t0 = time.time()
            resp = client.chat.completions.create(
                model=config.GENERATOR_MODEL,
                messages=[{"role": "user", "content": generation_prompt}],
                temperature=config.GENERATOR_TEMPERATURE,
                max_completion_tokens=config.MAX_COMPLETION_TOKENS,
                reasoning_effort=config.REASONING_EFFORT,
                timeout=config.API_TIMEOUT_S,
            )
            duration = time.time() - t0

            # ── Structural response checks (BEFORE any freezing) ──
            # Exactly one completion choice, a non-empty response_model,
            # and assistant content representable as a string. An EMPTY
            # string is valid experimental data. Anything else is an
            # infrastructure/protocol failure — never freeze it.
            choices = getattr(resp, "choices", None)
            if not isinstance(choices, list) or len(choices) != 1:
                raise StructuralResponseError(
                    f"expected exactly 1 completion choice, got "
                    f"{None if choices is None else len(choices)}")
            response_model = getattr(resp, "model", None)
            if not response_model or not isinstance(response_model, str):
                raise StructuralResponseError(
                    f"empty or non-string response_model: {response_model!r}")
            content = getattr(choices[0].message, "content", None)
            # content="" is valid experimental data; content=None is NOT.
            if not isinstance(content, str):
                raise StructuralResponseError(
                    f"assistant content is not a Python str: "
                    f"{type(content).__name__}")

            choice = choices[0]
            usage = getattr(resp, "usage", None)
            return GenerationResult(
                text=content,
                response_id=getattr(resp, "id", None),
                response_model=response_model,
                finish_reason=getattr(choice, "finish_reason", None),
                prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
                duration_s=round(duration, 2),
                transport_attempts=attempt,
                system_fingerprint=getattr(resp, "system_fingerprint", None),
                response_created=getattr(resp, "created", None),
                service_tier=getattr(resp, "service_tier", None),
                request_id=getattr(resp, "_request_id", None),
            )
        except Exception as exc:
            # A returned-but-malformed response is FATAL: no retry, no
            # second model sample, nothing frozen.
            if isinstance(exc, StructuralResponseError):
                raise
            if not _is_retryable(exc):
                raise FatalConfigError(
                    f"Non-retryable API error on {task_id}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            if attempt == config.GEN_MAX_ATTEMPTS:
                raise InfrastructureExhausted(
                    f"{task_id}: transport failed after "
                    f"{config.GEN_MAX_ATTEMPTS} attempts; last error: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            delay = min(
                config.GEN_BACKOFF_MAX_S,
                config.GEN_BACKOFF_BASE_S * config.GEN_BACKOFF_FACTOR ** (attempt - 1),
            )
            delay = random.uniform(0.0, delay)  # full jitter
            log_event(
                "gen_transport_retry",
                task_id=task_id,
                attempt=attempt,
                max_attempts=config.GEN_MAX_ATTEMPTS,
                error=f"{type(exc).__name__}: {str(exc)[:300]}",
                backoff_s=round(delay, 1),
            )
            time.sleep(delay)
    raise AssertionError("unreachable")


def cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    in_p, out_p = config.PRICE_PER_MTOK
    return (prompt_tokens * in_p + completion_tokens * out_p) / 1_000_000
