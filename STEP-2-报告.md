# STEP 2 报告：规格、schema、worktree、dry-run

状态：**PASS（功能与离线验收通过；`uv run ruff` 的精确命令需人补 dev 依赖）**

## 做了什么

- 修复 P1：模型判据从字符串比较改为显式模型族比较。
- 修复 P2：doctor 默认只输出可选后端的一行失败摘要，`ag doctor -v` 才输出原文。
- 建立 Git 基线并分步提交，不 push。
- 新增 task / verdict JSON Schema。
- 新增四种 task 模板。
- 新增 task 加载与字段级 schema 校验。
- 新增内部 worktree 创建、失败清理和幂等清理。
- 新增 `ag run --dry-run`；它只创建 worktree 并打印命令，不启动任何模型后端。

## 改了哪些文件

- `pyproject.toml`
- `src/agentgate/__main__.py`
- `src/agentgate/cli.py`
- `src/agentgate/doctor.py`
- `src/agentgate/validation.py`
- `src/agentgate/spec.py`
- `src/agentgate/workspace.py`
- `src/agentgate/dryrun.py`
- `schemas/task.schema.json`
- `schemas/verdict.schema.json`
- `templates/add-feature.task.yaml`
- `templates/fix-bug.task.yaml`
- `templates/write-tests.task.yaml`
- `templates/update-docs.task.yaml`
- `tests/test_cli.py`
- `tests/test_doctor.py`
- `tests/test_validation.py`
- `tests/test_spec.py`
- `tests/test_dryrun.py`
- `tests/fixtures/invalid-missing-budget.task.yaml`
- `tests/fixtures/invalid-same-family.task.yaml`
- `tests/fixtures/invalid-verdict-pass-with-defect.json`

## P1：模型族判据

`KNOWN_FAMILIES` 用显式前缀表覆盖：

- `deepseek`
- `nemotron`
- `gemini`
- `claude`
- `gpt`

`ModelPair` 同时记录 `executor_family` / `verifier_family`。同一 family 会在创建
worktree 之前被拒绝，档位只按 scaffold 差异决定 ★★/★★★。

未知模型使用完整归一化名称作为独立 family，而不是把所有未知模型并入一个公共桶。

## P2：doctor 降噪

默认输出：

```text
Probe failures:
[DSH (optional verifier)] FAIL — 需 DEEPSEEK_API_KEY
[Pi (optional verifier)] FAIL — 需智谱 key（ZHIPU_API_KEY）
```

`ag doctor -v` 才展开 stdout/stderr。required 后端失败仍默认输出完整证据。

## 第 2 步实现要点

- `task.yaml` 的所有顶层字段均为必填，不提供默认值。
- `executor.model` 与 `verifier.model` 均为必填。
- 每条 check 必须有 `name` / `run` / `expect`。
- `expect` 支持 `exit` / `stdout_contains` / `stdout_not_contains` /
  `stdout_regex`。
- `verdict: PASS` 同时要求 defects 为空、所有 checks 为 PASS。
- worktree 路径固定为目标仓库内部的 `wt/<task>-r<N>`。
- `wt/` 已加入根 `.gitignore`。
- dry-run 失败清理是幂等的；测试连续清理两次并检查路径和分支都消失。
- `pytest` 配置 `testpaths = ["tests"]`，避免内部 worktree 中的测试文件被递归收集。

## 验收命令与真实输出

### 1. dry-run 成功

```powershell
uv run ag run --dry-run templates/add-feature.task.yaml
```

退出码：`0`

```text
Task: agentgate-demo-add-feature
Model pair: ★★ (deepseek -> nemotron)
Worktree: D:\Projects\agentgate\wt\agentgate-demo-add-feature-r1
Branch: ag/agentgate-demo-add-feature-r1

Executor command:
codex exec --skip-git-repo-check -C D:\Projects\agentgate\wt\agentgate-demo-add-feature-r1 -m deepseek-v4-flash 'You are the implementation executor. Work only inside this worktree:
D:\Projects\agentgate\wt\agentgate-demo-add-feature-r1

Task id: agentgate-demo-add-feature
Goal:
Add one small feature while preserving the existing public behavior.
Update its tests and keep the change limited to the requested scope.

Constraints:
- Do not change unrelated files.
- Do not push or merge.

Acceptance checks (the verifier will run these independently):
- tests: uv run pytest -q
- diff-check: git diff --check

Do not push or merge. Do not change files outside the task scope.'

Verifier command:
codex exec --skip-git-repo-check -C D:\Projects\agentgate\wt\agentgate-demo-add-feature-r1 -s read-only -m nvidia/nemotron-3.5-lightning:free 'Act only as a read-only verifier. Do not modify any file.
Judge the supplied artifacts, never an executor narrative.
Task id: agentgate-demo-add-feature
Goal:
Add one small feature while preserving the existing public behavior.
Update its tests and keep the change limited to the requested scope.

Acceptance checks:
- tests: uv run pytest -q
- diff-check: git diff --check

Return JSON conforming to schemas/verdict.schema.json. Every defect must include a reproducible command.'

No model API process was started (dry-run).
exit=0
```

零 API 证明：`tests/test_dryrun.py::test_dry_run_builds_internal_worktree_and_only_calls_git`
拦截 dry-run 期间的每个 `subprocess.run`，断言第一个程序只能是 `git`。真实输出：

```text
.                                                                        [100%]
1 passed in 2.03s
```

### 2. worktree 位于仓库内部

`git worktree list --porcelain` 的真实输出：

```text
worktree D:/Projects/agentgate
HEAD ce16d4c9aab0e195ca4a441b8c9f7f318c10ac8b
branch refs/heads/master

worktree D:/Projects/agentgate/wt/agentgate-demo-add-feature-r1
HEAD ce16d4c9aab0e195ca4a441b8c9f7f318c10ac8b
branch refs/heads/ag/agentgate-demo-add-feature-r1
```

四个模板逐个真实 dry-run：

```text
templates/add-feature.task.yaml exit=0
templates/fix-bug.task.yaml exit=0
templates/write-tests.task.yaml exit=0
templates/update-docs.task.yaml exit=0
```

### 3. 非法 task.yaml 指向具体字段

命令：

```powershell
uv run ag run --dry-run tests/fixtures/invalid-missing-budget.task.yaml
```

```text
Invalid task.yaml: D:\Projects\agentgate\tests\fixtures\invalid-missing-budget.task.yaml:
- budget.max_rounds: required field is missing
exit=1
```

### 4. 同族模型被拒

命令：

```powershell
uv run ag run --dry-run tests/fixtures/invalid-same-family.task.yaml
```

```text
Executor and verifier are in the same model family ('deepseek')；同一模型族禁止自证。Execution='deepseek-v4-pro', verification='deepseek-flash'.
exit=1
```

两个别名组合的真实检查：

```text
deepseek-flash vs deepseek-v4-flash: REJECT: Executor and verifier are in the same model family ('deepseek')；同一模型族禁止自证。Execution='deepseek-flash', verification='deepseek-v4-flash'.
deepseek-v4-pro vs deepseek-flash: REJECT: Executor and verifier are in the same model family ('deepseek')；同一模型族禁止自证。Execution='deepseek-v4-pro', verification='deepseek-flash'.
exit=0
```

### 5. verdict schema 挡住矛盾

命令：

```powershell
uv run python -c "from pathlib import Path; from agentgate.spec import load_verdict; load_verdict(Path('tests/fixtures/invalid-verdict-pass-with-defect.json'))"
```

```text
Traceback (most recent call last):
  File "<string>", line 1, in <module>
  File "D:\Projects\agentgate\src\agentgate\spec.py", line 197, in load_verdict
    raise SpecError(
agentgate.spec.SpecError: Invalid verdict.json:
- verdict.defects: PASS requires defects to be empty
exit=1
```

### 6. pytest / ruff

pytest：

```text
........................                                               [100%]
24 passed, 2 subtests passed in 7.74s
```

ruff lint：

```text
All checks passed!
```

ruff format：

```text
13 files already formatted
```

## doctor 降噪实测

`uv run ag doctor` 的关键输出：

```text
Codex (executor)                    | PASS | 7.80s
Codex (default verifier, read-only) | PASS | 9.05s
DSH (optional verifier)             | FAIL | 5.67s
Pi (optional verifier)              | FAIL | 15.55s

Probe failures:
[DSH (optional verifier)] FAIL — 需 DEEPSEEK_API_KEY
[Pi (optional verifier)] FAIL — 需智谱 key（ZHIPU_API_KEY）

Double-blind tier: ★★
```

## Git 提交

已建立两个前置提交：

```text
ce16d4c fix: 按模型族拒绝自证别名并降低 doctor 噪音（第 1 步补丁）
e56d937 chore: AgentGate v0.2 骨架 + ag doctor（第 1 步）
```

第 2 步提交在本报告完成后创建，message 为
`feat: schema、模板与内部 worktree dry-run（第 2 步）`。没有 push。

## wt 清理是否幂等

是。所有模板 worktree 统一清理两遍：

```text
first_cleanup_remaining []
second_cleanup_remaining []
worktree D:/Projects/agentgate
HEAD ce16d4c9aab0e195ca4a441b8c9f7f318c10ac8b
branch refs/heads/master

branches_remaining=0
```

清理函数只接受目标仓库 `wt/` 下的路径，调用两次不会报错。

## 偏离说明

1. 未知模型 family 使用完整归一化名称，而不是取第一个分隔段。这样两个不同的
   未知模型不会被误判为同族；已知模型仍严格使用显式前缀表。
2. 项目没有声明 ruff 依赖。本轮按“沙箱无网络，不自行新增依赖”的规则，使用
   uv 离线缓存中的临时 ruff：
   `uv run --offline --with ruff ruff ...`。
   因此 lint 与 format 已真实通过，但验收清单里的精确命令 `uv run ruff ...`
   仍需人补依赖后执行。

## 需要人代跑的联网操作

如需让精确命令 `uv run ruff ...` 可用，请由人执行：

```powershell
uv add --dev ruff
uv sync
```

本轮没有联网，没有执行 `uv add` / `uv sync`，没有 push。
