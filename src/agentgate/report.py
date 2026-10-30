from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentgate.spec import TaskSpec
from agentgate.status import write_state


@dataclass(frozen=True)
class ChangedFile:
    path: str
    status: str
    added: int
    deleted: int


@dataclass(frozen=True)
class ChangeSummary:
    files: tuple[ChangedFile, ...]
    source: str

    @property
    def additions(self) -> int:
        return sum(item.added for item in self.files)

    @property
    def deletions(self) -> int:
        return sum(item.deleted for item in self.files)


def _duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{seconds / 60:.1f}min"


def _cost_text(state: dict[str, Any]) -> str:
    cost = state.get("cost_usd")
    if not isinstance(cost, (int, float)):
        return "cost unavailable"
    return f"${cost:.4f}"


def _round_line(round_state: dict[str, Any]) -> str:
    number = round_state["round"]
    verdict = round_state.get("verdict")
    if not isinstance(verdict, dict):
        return f"- R{number}: {round_state.get('status', 'unknown')}"
    defects = verdict.get("defects", [])
    if verdict.get("verdict") == "PASS":
        return f"- R{number}: PASS"
    ids = ", ".join(
        str(defect.get("id", "?")) for defect in defects if isinstance(defect, dict)
    )
    suffix = f"（{ids}）" if ids else ""
    return f"- R{number}: {verdict.get('verdict', 'UNKNOWN')}{suffix}"


def _round_fact(round_state: dict[str, Any]) -> str:
    number = round_state.get("round", "?")
    verdict = round_state.get("verdict")
    if not isinstance(verdict, dict):
        status = str(round_state.get("status", "unknown")).lower()
        if status == "executed":
            return f"R{number} 已执行但尚未验收"
        return f"R{number} 状态为 {status}"

    kind = str(verdict.get("verdict", "UNKNOWN"))
    if kind == "PASS":
        return f"R{number} PASS"
    defects = verdict.get("defects")
    defect_list = defects if isinstance(defects, list) else []
    if not defect_list or not isinstance(defect_list[0], dict):
        return f"R{number} {kind}（无 defect 详情）"
    first = defect_list[0]
    where = str(first.get("where", "未知位置"))
    actual = str(first.get("actual", "未知"))
    expect = str(first.get("expect", "未知"))
    extra = f" 等 {len(defect_list)} 项" if len(defect_list) > 1 else ""
    return f"R{number} {kind}（{where}：实际 {actual}，期望 {expect}{extra}）"


def _round_facts(rounds: list[dict[str, Any]]) -> str:
    if not rounds:
        return "没有完成的轮次。"
    return "；".join(_round_fact(item) for item in rounds) + "。"


def _status_changes(summary: ChangeSummary) -> str:
    statuses = {item.status for item in summary.files}
    if statuses == {"A"}:
        verb = "仅新增"
    elif statuses == {"M"}:
        verb = "仅修改"
    elif statuses == {"D"}:
        verb = "仅删除"
    else:
        verb = "共修改"
    names = "、".join(item.path for item in summary.files)
    detail = f"+{summary.additions}"
    if summary.deletions:
        detail += f" / -{summary.deletions}"
    return f"最终分支{verb} {names}（{detail}）。"


def _summary_fact(summary: ChangeSummary | None) -> str:
    if summary is None or not summary.files:
        return "最终分支无可核对的文本改动摘要。"
    return _status_changes(summary)


def _decision_fact(state: dict[str, Any]) -> str:
    status = str(state.get("status", "unknown")).lower()
    reason = str(state.get("terminal_reason", "")).strip()
    if status == "done":
        return "建议合并。"
    if status == "failed":
        return f"不建议合并；需先处理：{reason or '轮次预算耗尽'}。"
    return f"暂不合并；阻塞原因：{reason or '需要人工处理'}。"


def _mechanical_fact(round_list: list[dict[str, Any]]) -> str | None:
    if not round_list:
        return None
    mechanical = round_list[-1].get("mechanical")
    if not isinstance(mechanical, dict):
        return None
    failed = mechanical.get("failed") or []
    passed = mechanical.get("passed") or []
    total = len(passed) + len(failed)
    if failed:
        names = "、".join(str(item.get("name")) for item in failed)
        return f"机检未通过：{names}（{len(passed)}/{total} 通过）。"
    return f"机检：{total}/{total} 通过。"


def _conclusion(
    state: dict[str, Any],
    summary: ChangeSummary | None,
) -> list[str]:
    rounds = state.get("rounds", [])
    round_list = rounds if isinstance(rounds, list) else []
    facts = [
        _round_facts(round_list),
        _summary_fact(summary),
        _decision_fact(state),
    ]
    mechanical = _mechanical_fact(round_list)
    if mechanical is not None:
        facts.insert(0, mechanical)
    return facts


def _parse_numstat(output: str, statuses: dict[str, str]) -> ChangeSummary:
    files: list[ChangedFile] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added_text, deleted_text, path = parts[:3]
        try:
            added = int(added_text) if added_text != "-" else 0
            deleted = int(deleted_text) if deleted_text != "-" else 0
        except ValueError:
            continue
        files.append(
            ChangedFile(
                path=path,
                status=statuses.get(path, "?"),
                added=added,
                deleted=deleted,
            )
        )
    return ChangeSummary(files=tuple(files), source="git")


def _parse_name_status(output: str) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            statuses[parts[-1]] = parts[0][0]
    return statuses


def _git_summary(
    repo: Path,
    base_ref: str,
    compare_ref: str,
) -> ChangeSummary | None:
    numstat = subprocess.run(
        ["git", "diff", "--numstat", f"{base_ref}..{compare_ref}"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    name_status = subprocess.run(
        ["git", "diff", "--name-status", f"{base_ref}..{compare_ref}"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if numstat.returncode != 0 or name_status.returncode != 0:
        return None
    return _parse_numstat(numstat.stdout, _parse_name_status(name_status.stdout))


def _patch_summary(path: Path) -> ChangeSummary | None:
    if not path.is_file():
        return None
    files: dict[str, list[int]] = {}
    current: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^diff --git a/(.+?) b/(.+)$", line)
        if match:
            current = match.group(2)
            files.setdefault(current, [0, 0])
            continue
        if current is None:
            continue
        if line.startswith(("+++", "---")):
            continue
        if line.startswith("+"):
            files[current][0] += 1
        elif line.startswith("-"):
            files[current][1] += 1
    if not files:
        return None
    return ChangeSummary(
        files=tuple(
            ChangedFile(path=name, status="?", added=values[0], deleted=values[1])
            for name, values in files.items()
        ),
        source="patch",
    )


def _stored_summary(state: dict[str, Any]) -> ChangeSummary | None:
    stored = state.get("change_summary")
    if not isinstance(stored, dict):
        return None
    files = stored.get("files")
    if not isinstance(files, list):
        return None
    parsed: list[ChangedFile] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        parsed.append(
            ChangedFile(
                path=str(item.get("path", "")),
                status=str(item.get("status", "?")),
                added=int(item.get("added", 0)),
                deleted=int(item.get("deleted", 0)),
            )
        )
    return ChangeSummary(tuple(parsed), source="state")


def collect_change_summary(
    state: dict[str, Any],
    *,
    repo: Path,
    run_dir: Path,
) -> ChangeSummary | None:
    rounds = state.get("rounds", [])
    if not isinstance(rounds, list) or not rounds:
        return _stored_summary(state)

    base_ref = str(rounds[0].get("base_commit") or "HEAD")
    final_branch = state.get("final_branch")
    last_branch = rounds[-1].get("branch")
    compare_ref = str(final_branch or last_branch or "HEAD")
    summary = _git_summary(repo, base_ref, compare_ref)
    if summary is None:
        summary = _stored_summary(state)
    if summary is None:
        summary = _patch_summary(
            run_dir / f"round-{rounds[-1]['round']}" / "diff.patch"
        )
    return summary


def persist_change_summary(
    state: dict[str, Any],
    summary: ChangeSummary | None,
) -> None:
    if summary is None:
        return
    state["change_summary"] = {
        "source": summary.source,
        "files": [
            {
                "path": item.path,
                "status": item.status,
                "added": item.added,
                "deleted": item.deleted,
            }
            for item in summary.files
        ],
    }


def _changed_files_text(summary: ChangeSummary | None) -> str:
    if summary is None or not summary.files:
        return "(no changed files)"
    return "\n".join(
        f"{item.status}\t{item.path}\t+{item.added} / -{item.deleted}"
        for item in summary.files
    )


def render_report(
    task: TaskSpec,
    state: dict[str, Any],
    *,
    repo: Path,
    run_dir: Path | None = None,
) -> str:
    status = state.get("status", "unknown").upper()
    rounds = state.get("rounds", [])
    round_list = rounds if isinstance(rounds, list) else []
    final_branch = state.get("final_branch") or "(none)"
    base_commit = round_list[0].get("base_commit", "HEAD") if round_list else "HEAD"
    last_branch = round_list[-1].get("branch", final_branch) if round_list else "(none)"
    summary = (
        collect_change_summary(state, repo=repo, run_dir=run_dir)
        if run_dir is not None
        else _stored_summary(state)
    )

    lines = [
        (
            f"# {task.id}  {status}  {_duration(float(state.get('elapsed_seconds', 0)))}"
            f" / {len(round_list)} 轮 / {_cost_text(state)} / 双盲 {state.get('tier', '?')}"
        ),
        "",
        "## 结论",
        *_conclusion(state, summary),
        "",
        "## 每轮",
        *(_round_line(item) for item in round_list),
        "",
        "## 改了哪些文件",
        "```text",
        _changed_files_text(summary),
        "```",
        "",
        "## 你需要做的",
    ]
    if round_list and state.get("status") == "done":
        lines.extend(
            (
                f"git diff {base_commit}..{final_branch}",
                f"git merge --no-ff {final_branch}",
            )
        )
    elif round_list:
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
    summary = collect_change_summary(state, repo=repo, run_dir=run_dir)
    persist_change_summary(state, summary)
    write_state(run_dir, state)
    path = run_dir / "report.md"
    path.write_text(
        render_report(task, state, repo=repo, run_dir=run_dir),
        encoding="utf-8",
    )
    return path
