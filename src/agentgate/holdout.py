"""Held-out checks: acceptance criteria the executor is never shown.

Why this module exists
----------------------
Every public `checks[].run` is rendered verbatim into the execution prompt, so
an executor can optimise the checklist instead of the implementation. A small
set of checks kept out of that prompt makes overfitting *visible*: code written
to satisfy the visible list alone fails the hidden one.

Two rules keep it a detector rather than a second exam:

1. **It never reaches a model.** Not the execution prompt, not the dry-run
   listing, not the defect feedback, not the verifier input. The full command
   goes to `holdout.json` in the round directory -- a local artifact a human can
   read. The report only carries the index and the tail of the output.
2. **A failure ends the run.** Back-feeding it as a defect would mean filling
   `defect.reproduce`, which *must* be a directly executable command, and from
   the next round on the executor would simply write to that command. So the
   outcome is `FAILED: holdout-failed` with no verdict and no extra round.

The checks themselves run through the same machinery as public checks
(`collector.run_spec_checks`), including the per-round timeout.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentgate.collector import CheckOutput, run_spec_checks
from agentgate.expectations import evaluate_expect
from agentgate.spec import TaskSpec

#: How much of the check's last output line the report keeps. Enough to name
#: the failure, short enough that it cannot become a specification.
_TAIL_CHARS = 160


def output_tail(output: CheckOutput) -> str:
    """Last non-empty line the check printed, stderr first.

    stderr first because a failing assertion writes there; stdout is the
    fallback for checks that report their own failures.
    """

    for text in (output.stderr, output.stdout):
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            tail = lines[-1]
            return tail if len(tail) <= _TAIL_CHARS else "..." + tail[-_TAIL_CHARS:]
    return "(no output)"


@dataclass(frozen=True)
class HoldoutFailure:
    """One held-out check that did not satisfy its `expect`."""

    index: int
    name: str
    reason: str
    tail: str


@dataclass(frozen=True)
class HoldoutResult:
    checks: int
    failures: tuple[HoldoutFailure, ...]
    outputs: tuple[CheckOutput, ...]

    @property
    def ok(self) -> bool:
        return not self.failures

    def as_document(self) -> dict[str, Any]:
        """The full local record: commands, expectations, raw output.

        Written to `holdout.json` only. Never rendered into a prompt or a
        report, because the `run` strings are exactly what the executor must
        not learn.
        """

        reasons = {item.name: item.reason for item in self.failures}
        return {
            "checked": self.checks,
            "checks": [
                {
                    "index": index,
                    "name": output.spec.name,
                    "run": output.spec.run,
                    "expect": output.spec.expect,
                    "exit_code": output.exit_code,
                    "status": "FAIL" if output.spec.name in reasons else "PASS",
                    "reason": reasons.get(output.spec.name, ""),
                    "duration_s": round(output.duration_s, 3),
                    "stdout": output.stdout,
                    "stderr": output.stderr,
                }
                for index, output in enumerate(self.outputs, start=1)
            ],
        }

    def summary_document(self) -> dict[str, Any]:
        """What state.json and the report keep: indexes and tails, no commands."""

        return {
            "checked": self.checks,
            "failed": [
                {"index": item.index, "name": item.name, "tail": item.tail}
                for item in self.failures
            ],
        }

    def reason_line(self, round_no: int) -> str:
        """The terminal reason, phrased for a human reading the report."""

        details = "; ".join(f"#{item.index} {item.tail}" for item in self.failures)
        return (
            f"holdout-failed: round {round_no} did not clear its held-out "
            f"checks ({details})"
        )


def evaluate_holdout(task: TaskSpec, worktree: Path) -> HoldoutResult:
    """Run the declared held-out checks and judge them without a model."""

    outputs = run_spec_checks(
        task.holdout_checks,
        worktree,
        timeout_s=task.budget.max_minutes_per_round * 60,
    )
    failures: list[HoldoutFailure] = []
    for index, output in enumerate(outputs, start=1):
        ok, reason = evaluate_expect(output.spec, output)
        if ok:
            continue
        failures.append(
            HoldoutFailure(
                index=index,
                name=output.spec.name,
                reason=reason,
                tail=output_tail(output),
            )
        )
    return HoldoutResult(
        checks=len(outputs),
        failures=tuple(failures),
        outputs=outputs,
    )
