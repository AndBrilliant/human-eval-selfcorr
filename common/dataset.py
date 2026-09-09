"""Load the canonical HumanEval dataset with hard integrity checks.

Fails loudly (DatasetIntegrityError) before anything expensive happens if
the dataset is not exactly the canonical 164 tasks. Uses explicit
exceptions, NOT assert: `python -O` disables assertions, and experimental
integrity checks must remain active under every invocation mode.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import List

import config


class DatasetIntegrityError(RuntimeError):
    """The dataset is missing, corrupted, or not the canonical 164 tasks."""


def load_tasks(path: Path = config.DATASET_PATH) -> List[dict]:
    """Return the 164 HumanEval tasks in canonical order (by task index).

    Hard-fails if: file missing, file hash changed, task count != 164,
    duplicate task IDs, unexpected task IDs, or missing required fields.
    """
    path = Path(path)
    if not path.is_file():
        raise DatasetIntegrityError(f"Dataset file missing: {path}")

    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != config.DATASET_SHA256:
        raise DatasetIntegrityError(
            f"Dataset integrity failure: {path} sha256 is {digest}, "
            f"expected {config.DATASET_SHA256}. Refusing to run against a "
            f"changed dataset."
        )

    tasks = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]

    if len(tasks) != config.EXPECTED_N_TASKS:
        raise DatasetIntegrityError(
            f"Expected {config.EXPECTED_N_TASKS} HumanEval tasks, "
            f"got {len(tasks)}"
        )

    ids = [t.get("task_id") for t in tasks]
    if len(set(ids)) != len(ids):
        raise DatasetIntegrityError("Duplicate task IDs in dataset")
    expected_ids = {f"HumanEval/{i}" for i in range(config.EXPECTED_N_TASKS)}
    if set(ids) != expected_ids:
        raise DatasetIntegrityError(
            f"Task ID set mismatch. Missing: {sorted(expected_ids - set(ids))}, "
            f"unexpected: {sorted(set(ids) - expected_ids)}"
        )

    for t in tasks:
        for field in ("task_id", "prompt", "entry_point", "test"):
            if field not in t:
                raise DatasetIntegrityError(
                    f"Task {t.get('task_id')} missing field {field!r}"
                )

    # Deterministic canonical order: by numeric task index.
    tasks.sort(key=lambda t: int(t["task_id"].split("/")[1]))
    for i, t in enumerate(tasks):
        t["task_index"] = i
    return tasks
