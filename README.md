# AgentGate

[![ci](https://github.com/MuXue927/agentgate/actions/workflows/ci.yml/badge.svg)](https://github.com/MuXue927/agentgate/actions/workflows/ci.yml)

`ag` 是一个单一用途的无人值守执行/验收 CLI：Codex 执行、另一个模型在
`-s read-only` 沙箱里只读验收；不过就把 defect 回灌给 Codex，最多跑到
`max_rounds`，最后留下状态标记、一页纸报告和待合并分支。

> 第一次使用请先读 [如何写 task.yaml](docs/task-yaml-guide.md)——九个字段、
> `checks` 的写法准则、五类模板与反模式都在里面。

## 命令速查

| 命令 | 用途 |
|---|---|
| `ag doctor` | 探测后端，打印当前双盲档位 |
| `ag run <task.yaml> [--dry-run] [--rounds N] [--allow-self]` | 跑完整轮次循环 |
| `ag verify <task-id> [--force]` | 只验收当前未验收轮次，不回灌 |
| `ag resume <task-id> [--force]` | 从下一个未完成轮次续跑 |
| `ag clean [<task-id>\|--all] [--force]` | 清 AgentGate worktree 与 `ag/*` 分支 |
| `ag report <task-id>` | 从已有 `state.json` 和工件重生成 `report.md`，不调模型 |

当前 `uv run ag --help` 实测输出：

```text
 Usage: ag [OPTIONS] COMMAND [ARGS]...

 Single-purpose Codex execution and read-only verification loop.

┌─ Options ───────────────────────────────────────────────────────────────────┐
│ --help          Show this message and exit.                                 │
└─────────────────────────────────────────────────────────────────────────────┘
┌─ Commands ──────────────────────────────────────────────────────────────────┐
│ doctor   Probe execution and verification backends.                         │
│ version  Print the AgentGate version.                                       │
│ run      Execute, verify, and iterate until a terminal state or the round   │
│          limit.                                                             │
│ clean    Remove AgentGate worktrees and ag/* branches safely.               │
│ verify   Verify an executed round once, without defect feedback or retry    │
│          rounds.                                                            │
│ resume   Resume from the next unfinished round.                             │
│ report   Regenerate report.md from an existing run without model calls.     │
└─────────────────────────────────────────────────────────────────────────────┘
```

## task.yaml

九个顶层字段全部必填，没有静默默认值：

`id / repo / goal / executor / verifier / constraints / checks / budget /
human_approval`

`templates/add-feature.task.yaml` 全文：

```yaml
id: agentgate-demo-add-feature
repo: D:/Projects/<your-repo>  # 改成你要跑的目标仓库
goal: |
  Add one small feature while preserving the existing public behavior.
  Update its tests and keep the change limited to the requested scope.
executor: {harness: codex, model: deepseek-v4-flash}
verifier: {harness: codex, model: nvidia/nemotron-3.5-lightning:free}
constraints:
  - Do not change unrelated files.
  - Do not push or merge.
checks:
  - name: tests
    run: uv run pytest -q
    expect: {exit: 0, stdout_not_contains: failed}
  - name: diff-check
    run: git diff --check
    expect: {exit: 0}
budget: {max_rounds: 3, max_minutes_per_round: 25, max_total_minutes: 90}
human_approval: {before_execute: false, before_merge: true}
```

每条 `checks` 都必须是 `{name, run, expect}`。`expect` 至少包含一项：

- `exit`: 期望退出码
- `stdout_contains`: stdout 必须包含的文本
- `stdout_not_contains`: stdout 禁止出现的文本
- `stdout_regex`: stdout 必须匹配的正则表达式

## 三条铁律

1. 执行与验收必须使用不同模型族；同族模型会被拒绝。默认执行模型是
   `deepseek-v4-flash`，默认验收模型是
   `nvidia/nemotron-3.5-lightning:free`，当前档位为 ★★。
2. 验收方必须在 `-s read-only` 沙箱中运行。验收前后会比较
   `git status --porcelain`；worktree 发生变化时本次验收直接 `BLOCKED`。
3. `ag` 永不自动合并。PASS 只留下 `DONE`、`report.md` 和待合并分支，
   合并命令由人复制执行；也不会 push。

## 常见坑

- **Windows 上 Codex 的沙箱是坏的（当前最重要的坑）**：在 `-s read-only` 和
  `-s workspace-write` 下，模型发起的**任何** shell 命令都会被拒绝，报
  `Failed to create unified exec process: helper_unknown_error` 或
  `rejected: blocked by policy`。执行方因此读不到仓库、改不了文件，
  最终表现为 `BLOCKED: no changes detected`。
  实测 Codex CLI 0.162.1 与 0.164.0 均可复现（上游 issue #43633 / #43881 / #54746）。
  **对策**：在 `task.yaml` 里显式给两端设置 `sandbox: danger-full-access`：
  ```yaml
  executor: {harness: codex, model: deepseek-v4-flash, sandbox: danger-full-access}
  verifier: {harness: codex, model: nvidia/nemotron-3.5-lightning:free, sandbox: danger-full-access}
  ```
  **代价**：`danger-full-access` 不再由沙箱强制只读，验收方的"只读"退化为
  「提示词约束 + 验收前后 `git status --porcelain` 复核」。复核只能看 worktree
  内部，管不到 worktree 之外——这种环境下跑任务请务必先提交工作树，
  事后用 `git status` 核对。
  `ag doctor` 的 `Codex (tool execution, danger-full-access)` 一行专门探这个：
  它 FAIL 说明 Codex 完全跑不了命令。
- **沙箱无网络**：执行代理不能自己 `uv add` / `uv sync`。需要新依赖时，
  在报告里写清楚并由人代跑。
- **worktree 必须在项目内部**：固定在目标仓库的 `wt/<task>-r<N>`。
  使用 `../` 会被 Codex `workspace-write` 拒绝。
- **不要直接跑模板**：`repo` 是占位符。若目标就是 AgentGate 自己，
  必须显式传 `--allow-self`。
- **免费档会拥堵**：命令异常卡住时先跑 `ag doctor`，不要自动切换模型或
  无限重试。
- **同一缺陷连续两轮**：按归一后的 `(where, why)` 指纹识别，提前
  `FAILED`，避免空转。
- **中断恢复**：末轮已执行但未验收时，`ag resume` 只补验收，不重做该轮。
- **`resume --force` 会先删旧档案**：它在验证新 run 能否启动之前就 `rmtree`
  了旧 run 目录，失败时旧记录不可恢复（已知缺陷，待修）。

## 最小上手

```powershell
cd D:/Projects/agentgate; uv sync
uv run ag doctor
Copy-Item templates/fix-bug.task.yaml D:/Projects/my-task.yaml
# 编辑 repo / goal / checks 后：
uv run ag run D:/Projects/my-task.yaml
```

完成后查看：

```powershell
Get-Content runs/<task-id>/report.md
git merge --no-ff ag/<task-id>-r<N>
```

## 文件结构

| 目录 | 用途 |
|---|---|
| `src/` | `ag` CLI 的 Python 实现，包含规格加载、worktree、执行、验收、轮次编排与报告模块。 |
| `tests/` | 单元测试、CLI 集成测试与失败路径夹具；不存放运行时数据。 |
| `schemas/` | `task.yaml` 与验收方 `verdict.json` 的 JSON Schema。 |
| `templates/` | 可复制的四类任务模板，使用前必须填写真实仓库与验收检查。 |
| `taskfiles/` | 开发和沙箱验证使用过的任务样例，记录真实轮次与中断恢复场景。 |
| `tasks/` | 可以公开的任务定义。含公司名、内部链接或个人数据的任务放仓库外（见 `tasks/README.md`）。 |
| `docs/` | 设计、验收记录、索引和逐步实现证据；入口是 `docs/index.md`。 |
| `adapters/` | 顶层兼容适配器包，保留给历史导入路径；实际实现位于 `src/agentgate/adapters/`。 |
| `runs/` | Git 忽略的运行时目录，保存每轮工件、状态、verdict 与最终报告。 |
| `wt/` | Git 忽略的运行时目录，保存目标仓库内部的隔离 worktree。 |

## 继续阅读

**写第一个任务之前先读 [如何写 task.yaml](docs/task-yaml-guide.md)**：九个字段
逐一说明、`checks` 的写法准则、五类任务模板、反模式清单与失败诊断表，末尾
附 `tasks/agentgate-docs.task.yaml` 的逐段解析。

文档入口为 [docs/index.md](docs/index.md)。最终验收记录见
[docs/acceptance.md](docs/acceptance.md)，完整设计与实现摘要分别见
[docs/design.md](docs/design.md) 和
[AgentGate v0.2 实现报告](docs/AgentGate-v0.2-实现报告.md)。逐步实现和
验证据见 [STEP 5 报告](docs/steps/step-5-loop.md)。

## 受以下项目启发

AgentGate 的机制不是凭空来的。以下项目在"执行 / 验收 / 重试"上做过公开的
探索，本项目的取舍参考了它们的文档与设计记录。**只借鉴机制与问题意识，
没有复制任何代码、提示词、字段命名或目录结构**；逐条差异见
[docs/borrowings.md](docs/borrowings.md)，调研笔记见
[docs/landscape.md](docs/landscape.md)。

- [the-open-engine/zeroshot](https://github.com/the-open-engine/zeroshot)：
  盲验收（validator 看不到 worker 上下文）、崩溃可恢复的状态。
- [mateaix/loope](https://github.com/mateaix/loope)：implement → review →
  verify 的迭代上限，以及"验证命令以退出码为硬门"。
- [andromarces/agent-loops](https://github.com/andromarces/agent-loops)：
  运行时在评审沙箱之外执行测试、结果作为不可信证据；reviewer 前后做工作树
  变更检测。
- [github/spec-kit](https://github.com/github/spec-kit)：规格即可执行契约，
  以及"同一发现连续出现说明文档之间互相矛盾"。
- [sxivansx/loop-engineering](https://github.com/sxivansx/loop-engineering)：
  循环六原语与"无净进展即停机"。
- [formin/spec-kit-loop](https://github.com/formin/spec-kit-loop)：maker
  不能自评、不可判定要单独成态。
- [sangrokjung/claude-forge · review-loop](https://github.com/sangrokjung/claude-forge/blob/main/skills/review-loop/SKILL.md)：
  判决必须绑定当前版本，rechecker 只谈"改了什么、对应哪条 finding"。

以上项目均为 MIT 或公开发布的文档/技能；本项目未包含其任何代码。若你发现
有遗漏的出处，请开 issue——标注比"看起来像原创"重要。
