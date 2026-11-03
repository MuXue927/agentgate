# AgentGate v0.2 验收记录

v0.2.0 / 2026-11-03

> **边界声明（先读这一条）**：AgentGate 保证的是"判决绑定到具体证据、机检由代码判定、
> 执行方不能改测试通关"。它**不**保证"任务规格写对了"——`goal` 与 `checks` 写歪时，
> 它可以稳定地交出错误结果。

验收日期：2026-10-30（v0.2.0 定稿于 2026-11-03）

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

## 双盲档位

档位不是常量：它取决于可选验收后端此刻是否可用。下面两段都是**带时间戳的快照**，
引用时必须带上时间。

### v0.2 验收时（2026-10-30）：★★

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

### v0.2.0 定稿时（2026-11-03）：★★★

```text
ag doctor 于 2026-11-03 14:20:46 实测：★★★（Pi 一路 PASS，14.91s；DSH 仍 FAIL —— 需 DEEPSEEK_API_KEY）
复测于 2026-11-03 14:21:54：★★★（Pi 2.47s）—— 同一档位连测两次成立，不是单次抖动
```

从 ★★ 到 ★★★ 的原因是可选后端变了，不是判据变了：Pi + `bigmodel/glm-4.7-flash`
这一次有可用凭证并通过探针，凑齐了"不同脚手架 + 不同模型"。**这条也不可外推**：
★★★ 依赖第三方免费档当场可用，`DSH` 至今仍因缺 key 常驻 FAIL。文档里任何写死的
档位（包括上表和 README 的表述）都只代表写下它的那一刻，真实档位只有当场
跑 `ag doctor` 才知道。

档位只影响"谁来验收"，不影响任何判据：★★ 与 ★★★ 下跑的是同一套门顺序和
同一批测试。v0.2 不实现自动降级、通知、成本治理或 GUI 工具适配。

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

## v0.2 后续硬化（2026-11-01 ~ 11-02）

v0.2 验收通过之后，按 [landscape.md](landscape.md) §4 的清单继续修了十项。
本节只记"落地了什么、判据在哪、对应哪份 STEP、边界还剩什么"，不重复各
STEP 报告里的实现细节。

| 项 | 落地内容 | 判据（可执行） | STEP |
|---|---|---|---|
| P0-1 机检硬门槛 | `expect` 由代码逐键判定；机检失败不调模型，直接合成 FAIL 判决 | `tests/test_expectations.py`（`calls == []`） | [6](steps/step-6-expect-gate.md) |
| P0-4 超时树杀 | `run_bounded` 超时杀整棵进程树，退出码 124 计为机检 FAIL | `tests/test_bounded_proc.py` | [7](steps/step-7-timeout.md) |
| P0-3 受保护路径 | `protected` 拦 M/D/R，放行 A；先于机检执行 | `tests/test_protected.py` | [8](steps/step-8-protected.md) |
| P1-2 证据包 | 改动文件全文从 round commit 读，单文件 32KiB / 总 128KiB，截断三处露面 | `tests/test_evidence.py` | [9](steps/step-9-evidence-pack.md) |
| P0-2 判决绑定 | `subject`（base/head/diff 哈希）必填，回抄不一致即 BLOCKED | `tests/test_subject.py`、`tests/test_verifier.py` | [10](steps/step-10-subject.md) |
| P1-3 无净进展 | 第 N(≥2) 轮机检失败集合与 N-1 完全相同 → `FAILED: no-progress` | `tests/test_verifier.py`、`tests/test_orchestrator.py` | [11](steps/step-11-no-progress.md) |
| P1-1 留出判据 | 可选 `holdout_checks`（1–3 条），不进执行提示词；失败即终态、不回灌 | `tests/test_holdout.py` | [12](steps/step-12-holdout.md) |
| P1-4 状态写入 | `write_state` 原子替换、`update_state` 单一写入者、`ag run` 按轮次 delta 合并、读取缺键报错不重建 | `tests/test_status.py` | [13](steps/step-13-state-writes.md) |
| P2-2 BLOCKED 原因码 | `BlockedCode` 十种；`state.json` 轮次写 `blocked_code`、run 级写 `terminal_code`，报告与 `ag verify` 输出 `BLOCKED [code]: reason` | `tests/test_blocked.py`（6 条） | [14](steps/step-14-blocked-code.md) |
| P1-5 假后端 | `tests/fakes.py`：只替换 `runner.run` 与验收方 `command_runner`，其余全真 | `tests/test_fake_loop.py`（3 条，均 < 3s） | [15](steps/step-15-fake-backends.md) |

累计：`176 → 185 passed`（2026-11-02 全量），两条 ruff 绿。

### 当前能力边界

这一节存在的理由：把"以为已经解决"和"确实没解决"分开。

1. **holdout 的输出尾部可能泄露期望物**。报告只写序号与输出尾部，不回灌给执行方；
   因为失败即终态，执行方永远看不到它。**若将来有人把 holdout 改成可回灌，这条立刻变成真漏洞。**
2. **`ag verify --force` 是冻结工件复验**：它不会重新收集 worktree，只在 `diff.patch`
   与 `subject` 仍一致时重问验收方；HEAD 动过就是 `stale-subject`。
3. **`danger-full-access` 下的"只读"靠提示词 + `git status --porcelain` 复核**，
   管不到 worktree 之外。要真只读得等上游沙箱修好。
4. **`ag run` 的状态合并是"按 key、内存值胜"**：它能保护编排器从未见过的键，
   但不解决"同一键两个写入者都写、内存那份更旧"。引入并发写入者时这条必须重开。
5. **`no-progress` 只比机检失败集合**：拿掉同一批 check 再换一种错法，不算无净进展。
6. **含 holdout 的任务文件必须放仓库外**，否则执行方在 worktree 里能读到它。
7. **`unreadable-state` 无法把 code 写进它读不出来的那份状态**：code 只到
   outcome、终端输出和 `BLOCKED` marker，`state.json` 保持原样（这是故意的，
   修复要猜轮次）。`ag verify` 在这种输入上的退出码也从 1 变成 2。

### 本批跳过的一条

P2-1「报告证据索引」（结论段每条指向对应工件路径）**未做**。原因：它要改
`report.md` 的结论段结构，而现有快照测试与"一行主因"的约定都依赖当前措辞；
在没有真实任务验证"索引真的更好读"之前改格式，风险大于收益。记在
[landscape.md](landscape.md) §P2 里，不删。
