"""Deterministic evaluation of `checks[].expect` against real command output.

The verifier model is a judge: it reads evidence and returns a verdict. It is
not an execution oracle. Until v0.2 every `expect` was only serialised into the
prompt, so "PASS" could be a model's opinion about a command that actually
exited non-zero.

This module is the code-side judgement. It is pure: no IO, no subprocess, no
network. `verifier.verify_round` runs it before it considers calling a model,
and a failure short-circuits the model entirely.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from agentgate.collector import CheckOutput
from agentgate.spec import CheckSpec

_TAIL_CHARS = 400


@dataclass(frozen=True)
class CheckFailure:
    """One check whose real output did not satisfy its declared `expect`."""

    name: str
    reason: str
    reproduce: str
    expect: str
    actual: str


@dataclass(frozen=True)
class MechanicalResult:
    """Outcome of evaluating every check without a model in the loop."""

    passed: tuple[str, ...]
    failed: tuple[CheckFailure, ...]

    @property
    def ok(self) -> bool:
        return not self.failed

    @property
    def total(self) -> int:
        return len(self.passed) + len(self.failed)

    def as_document(self) -> dict[str, Any]:
        return {
            "passed": list(self.passed),
            "failed": [
                {"name": item.name, "reason": item.reason} for item in self.failed
            ],
        }


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _tail_text(value: str) -> str:
    text = value.strip()
    if len(text) <= _TAIL_CHARS:
        return text
    return "..." + text[-_TAIL_CHARS:]


def _actual_text(output: CheckOutput) -> str:
    return (
        f"exit={output.exit_code}; "
        f"stdout_tail={_json(_tail_text(output.stdout))}; "
        f"stderr_tail={_json(_tail_text(output.stderr))}"
    )


def evaluate_expect(check: CheckSpec, output: CheckOutput) -> tuple[bool, str]:
    """Judge one check's real output against its `expect` object.

    Every declared key is evaluated; a failure on `exit` never hides a failure
    on `stdout_contains`. Returns `(True, "")` when everything holds, otherwise
    `(False, reason)` where `reason` is stable enough to be a defect `why`:
    it contains expected values only, never the observed output.
    """

    expect = check.expect
    reasons: list[str] = []

    if "exit" in expect:
        wanted = int(expect["exit"])
        if output.exit_code != wanted:
            reasons.append(f"exit expected {wanted} actual {output.exit_code}")

    if "stdout_contains" in expect:
        needle = str(expect["stdout_contains"])
        if needle not in output.stdout:
            reasons.append(f"stdout_contains expected {_json(needle)}")

    if "stdout_not_contains" in expect:
        needle = str(expect["stdout_not_contains"])
        if needle in output.stdout:
            reasons.append(f"stdout_not_contains forbidden {_json(needle)} appeared")

    if "stdout_regex" in expect:
        pattern = str(expect["stdout_regex"])
        if re.search(pattern, output.stdout) is None:
            reasons.append(f"stdout_regex expected {_json(pattern)}")

    return (not reasons, "; ".join(reasons))


def evaluate_checks(checks: Iterable[CheckOutput]) -> MechanicalResult:
    passed: list[str] = []
    failed: list[CheckFailure] = []
    for output in checks:
        ok, reason = evaluate_expect(output.spec, output)
        if ok:
            passed.append(output.spec.name)
            continue
        failed.append(
            CheckFailure(
                name=output.spec.name,
                reason=reason,
                reproduce=output.spec.run,
                expect=_json(output.spec.expect),
                actual=_actual_text(output),
            )
        )
    return MechanicalResult(passed=tuple(passed), failed=tuple(failed))


def build_mechanical_verdict(
    result: MechanicalResult,
    checks: Iterable[CheckOutput],
) -> dict[str, Any]:
    """Build a verdict document from mechanical failures only.

    The shape matches `schemas/verdict.schema.json`; no schema change is needed.
    `where` carries the `mechanical:` prefix so a report can never confuse this
    with a model-authored defect.
    """

    defects = [
        {
            "id": f"M{index}",
            "severity": "blocking",
            "where": f"mechanical:{item.name}",
            "why": item.reason,
            "reproduce": item.reproduce,
            "expect": item.expect,
            "actual": item.actual,
        }
        for index, item in enumerate(result.failed, start=1)
    ]
    failures = {item.name: item.reason for item in result.failed}
    check_docs: list[dict[str, str]] = []
    for output in checks:
        name = output.spec.name
        reason = failures.get(name)
        check_docs.append(
            {
                "name": name,
                "status": "FAIL" if reason is not None else "PASS",
                "evidence": reason
                if reason is not None
                else f"exit={output.exit_code}",
            }
        )
    names = ", ".join(item.name for item in result.failed)
    return {
        "verdict": "FAIL",
        "summary": f"mechanical checks failed: {names}",
        "checks": check_docs,
        "defects": defects,
    }
