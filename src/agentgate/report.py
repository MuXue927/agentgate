from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from agentgate.spec import TaskSpec


def _duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{seconds / 60:.1f}min"


def _cost_text(state: dict[str, Any]) -> str:
    cost = state.get("cost_usd")
    if not isinstance(cost, (int, float)):
        return "cost unavailable"
    return f"${cost:.4f}"


def _diff_stat(repo: Path, base_ref: str, final_branch: str) -> str:
    completed = subprocess.run(
        ["git", "diff", "--stat", f"{base_ref}..{final_branch}"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        return "(git diff --stat unavailable)"
    return completed.stdout.strip() or "(no changed files)"


def _round_line(round_state: dict[str, Any]) -> str:
    number = round_state["round"]
    verdict = round_state.get("verdict")
    if verdict is None:
        return f"- R{number}: {round_state.get('status', 'unknown')}"
    defects = verdict.get("defects", [])
    if verdict.get("verdict") == "PASS":
        return f"- R{number}: PASS"
    ids = ", ".join(defect.get("id", "?") for defect in defects)
    suffix = f"（{ids}）" if ids else ""
    return f"- R{number}: {verdict.get('verdict', 'UNKNOWN')}{suffix}"


def _conclusion(state: dict[str, Any]) -> list[str]:
    status = state.get("status", "unknown").upper()
    if status == "DONE":
        lines = ["Verification passed; the final branch is ready for human review."]
    elif status == "FAILED":
        lines = ["Verification did not converge within the configured round budget."]
    else:
        lines = ["The run stopped before convergence."]

    reason = state.get("terminal_reason")
    if isinstance(reason, str) and reason:
        lines.append(reason)
    return lines[:3]


def render_report(
    task: TaskSpec,
    state: dict[str, Any],
    *,
    repo: Path,
) -> str:
    status = state.get("status", "unknown").upper()
    rounds = state.get("rounds", [])
    final_branch = state.get("final_branch") or "(none)"
    base_commit = rounds[0].get("base_commit", "HEAD") if rounds else "HEAD"
    last_branch = rounds[-1].get("branch", final_branch) if rounds else "(none)"
    compare_branch = final_branch if state.get("status") == "done" else last_branch
    stat = _diff_stat(repo, base_commit, compare_branch) if rounds else "(no rounds)"

    lines = [
        (
            f"# {task.id}  {status}  {_duration(float(state.get('elapsed_seconds', 0)))}"
            f" / {len(rounds)} 轮 / {_cost_text(state)} / 双盲 {state.get('tier', '?')}"
        ),
        "",
        "## 结论",
        *_conclusion(state),
        "",
        "## 每轮",
        *(_round_line(item) for item in rounds),
        "",
        "## 改了哪些文件",
        "```text",
        stat,
        "```",
        "",
        "## 你需要做的",
    ]
    if rounds and state.get("status") == "done":
        lines.extend(
            (
                f"git diff {base_commit}..{final_branch}",
                f"git merge --no-ff {final_branch}",
            )
        )
    elif rounds:
        lines.append(f"Inspect defects in {last_branch} and the round verdicts.")
    else:
        lines.append("Inspect the run artifacts and terminal reason.")
    lines.append("")
    return "\n".join(lines)


def write_report(
    task: TaskSpec,
    state: dict[str, Any],
    *,
    run_dir: Path,
    repo: Path,
) -> Path:
    path = run_dir / "report.md"
    path.write_text(
        render_report(task, state, repo=repo),
        encoding="utf-8",
    )
    return path
