# AgentGate v0.2 验收记录

验收日期：2026-10-30

本记录汇总 AgentGate v0.2 的最终交付判据。结论为 **PASS**：Codex 执行、
独立只读验收、FAIL 缺陷回灌、最多三轮循环、一页纸报告和待合并分支均已
完成真实运行验证。本页只引用已落盘的报告、终端输出和运行工件，不以执行方
自然语言总结作为验收证据。

## 六条交付判据

| 判据 | 验收结论 | 验证方式与真实证据 |
|---|---|---|
| 一条命令跑通 | PASS | 执行 `uv run ag run D:\Projects\ag-sandbox-two-round.task.yaml`，终端输出 `Round 1: FAIL (1 defect) → continue`、`Round 2: PASS → DONE`，退出码为 0。 |
| 中途不碰键盘 | PASS | 上述两轮任务从启动到 `DONE` 全自动完成；另做中断续跑验证，R1 已落盘后终止进程，`uv run ag resume ag-resume-pass` 从 R2 继续并以 `DONE` 结束，R1 HEAD 前后均为 `f78fc30785fb3a599aa5004369c1de54e68c210c`。 |
| 一页纸报告 | PASS | `runs/ag-sandbox-two-round/report.md` 首行包含任务 id、`DONE`、`2.0min / 2 轮 / $0.0060 / 双盲 ★★`，结论段为两行，并给出 diff 与 merge 命令。 |
| 可合并分支 | PASS | 两轮任务最终保留本地分支 `ag/ag-sandbox-two-round-r2`；报告中记录基线 `0a56a0494c916c3b77b63d25c18a85bda73c75af` 和 `git merge --no-ff ag/ag-sandbox-two-round-r2`。工具没有执行合并。 |
| doctor ★★ | PASS | `uv run ag doctor` 输出 Codex 执行方 PASS、Codex 默认只读验收方 PASS，并打印 `Double-blind tier: ★★`；DSH 与 Pi 为可选后端，分别因缺少 `DEEPSEEK_API_KEY` 和 `ZHIPU_API_KEY` 显示 FAIL。 |
| 全程未 push | PASS | 实现期间只创建本地提交、worktree 和 `ag/*` 分支；没有执行 push，也没有创建远端提交或自动合并。 |

## 佐证命令与结果

两轮闭环的核心终端结果：

```text
Round 1: FAIL (1 defect) → continue
Round 2: PASS → DONE
DONE + 2 轮 + 118.6s
Branch: ag/ag-sandbox-two-round-r2
Merge: git merge --no-ff ag/ag-sandbox-two-round-r2
exit=0
```

收尾回归在实现提交 `c82dae0` 之后复跑：

```text
71 passed, 2 subtests passed in 54.67s
All checks passed!
29 files already formatted
```

严格的轮次上限和连续同缺陷早停也有真实运行证据：

```text
Round 1: FAIL (1 defect) → FAILED
FAILED + 1 轮 + 52.2s
exit=1
```

连续两轮出现同一缺陷时，工具在第二轮后停止，终止原因为
`same defect persisted across consecutive rounds`，指纹为
`2436278c0b38`。这两项验证的是失败路径，不把 `FAILED` 改写成成功。

## 当前双盲档位

当前档位为 **★★**：

- 执行方：Codex + `deepseek-v4-flash`。
- 验收方：Codex + `nvidia/nemotron-3.5-lightning:free`。
- 两者脚手架相同、模型族不同，因此满足“不同模型不得自证”的硬规则，
  但未达到“不同脚手架 + 不同模型”的 ★★★。

`ag doctor` 的实际关键输出如下：

```text
Codex (executor)                    | required | PASS | 7.73s
Codex (default verifier, read-only) | required | PASS | 7.73s
DSH (optional verifier)             | optional | FAIL | 4.80s
Pi (optional verifier)              | optional | FAIL | 15.48s

[DSH (optional verifier)] FAIL — 需 DEEPSEEK_API_KEY
[Pi (optional verifier)] FAIL — 需智谱 key（ZHIPU_API_KEY）

Double-blind tier: ★★
```

只有当可选验收后端具备凭证并稳定通过探测时，才可能在后续版本升到 ★★★；
v0.2 不实现自动降级、通知、成本治理或 GUI 工具适配。

## 保留证据

逐步实现、失败路径和终端原文见
[step-1-doctor.md](steps/step-1-doctor.md)、
[step-2-schema.md](steps/step-2-schema.md)、
[step-3-executor.md](steps/step-3-executor.md)、
[step-4-verifier.md](steps/step-4-verifier.md) 与
[step-5-loop.md](steps/step-5-loop.md)。设计边界以
[design.md](design.md) 为准，完整实现摘要见
[AgentGate-v0.2-实现报告.md](AgentGate-v0.2-实现报告.md)。

本验收记录本身只整理现有证据，不重新执行模型调用。`DONE` 仍不等于自动合并：
合并前必须由人查看报告与 diff，并显式执行 merge 命令。

## P0-1 落地后的补充验收（2026-10-30）

v0.2 验收时，`checks[].expect` 只被序列化进提示词，由验收模型自行判断；
`verdict` 与 `checks[].status` 都是模型填的，schema 只能保证"自洽"，不能保证
"自洽即真实"。P0-1 把判定移到代码侧（`src/agentgate/expectations.py`）：
任一 check 真实输出不满足 `expect` 时，验收模型不被调用，判决由机检合成，
`where` 带 `mechanical:` 前缀。

### 判据与证据

| 判据 | 证据 |
|---|---|
| 机检失败时不调用验收模型 | `tests/test_expectations.py::test_mechanical_fail_overrides_model_pass`（`calls == []`） |
| 机检失败优先于模型 PASS | 同上，最终 `verdict == FAIL` |
| 机检全绿仍须模型 PASS | `test_all_checks_pass_still_requires_model_pass` |
| 超时（124）是机检 FAIL，不是 BLOCKED | `test_command_timeout_is_mechanical_fail`（无 `BLOCKED` 标记） |
| 合成缺陷可与模型缺陷区分 | `test_mechanical_verdict_is_distinguishable`（`where == "mechanical:hello-content"`） |
| 四类 `expect` 逐键判定、不短路 | `test_every_declared_key_is_evaluated`（4 组参数） |

命令与真实输出：

```text
$ uv run pytest -q
94 passed, 2 subtests passed in 37.73s

$ uv run ruff check
All checks passed!
```

（P0-1 之前为 86 passed；新增 8 个用例。）

### 与 v0.2 验收的关系

本补丁不改变"执行与验收必须异族模型""验收方只读""不 push 不自动合并"
三条铁律，也不改 `task.yaml` 与 `verdict` 的 schema。它只把一条原本由模型
代劳的判断收回给代码。逐条借鉴来源见
[borrowings.md](borrowings.md) P0-1。
