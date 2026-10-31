# STEP 11：小锈（doctor 阈值 + clean 文案）+ P1-3 无净进展

上游：STEP 10（P0-2 判决绑定，`83df219` / `0c84276`，已推，CI `41217386943` 绿）
commit：`5a5beef`（阶段 A）、`fe8de2d`（P1-3 第一版判据，已被本批重写）、
本批唯一提交 `fix: P1-3 判据改为机检失败集合（原字节哈希判据不可达）`

> 本文件在批次 G 被整体重写。第一版判据（比两轮 `diff_sha256`）被判定**不可达**，
> 代码与用例已删除，理由与证据见 §4；只保留"阶段 A"的结论（那部分已验收通过）。

## 0. 直接回答

| 问题 | 答案 |
|---|---|
| 旧判据怎么处置 | 选 **(a) 删除**。`_previous_diff_sha()` 与它的 4 条 `verify_round` 用例、
`test_report` / `test_cli` 里的对应文案全部删掉，没有留"兜底"。理由见 §5.1 |
| 新判据在代码哪一行 | 判定 `verifier.py:864-866`（`_previous_mechanical_failures(...)` / 集合相等）；
读上一轮机检失败的函数 `:589`；记录函数 `_record_no_progress` `:642` |
| 相对 protected 与机检的先后 | subject `:814` → HEAD 前向门 `:817` → **protected `:839`** →
**机检 `:857`** → **无净进展 `:864`** → 证据包 `:897` → 验收方 `:923` |
| resume 误判风险 | **当前不存在**：轮号永不重用（`orchestrator.py:354` + `ensure_run_slot`），
BLOCKED 只能 `--force` 重开新 run。详见 §6 |
| 真跑两轮的机检失败集合 | R1 `{ci-runner, net-reachable}`；R2 **完全相同** → `FAILED: no-progress`，
R2 的 `verifier-attempt-*` 文件数 **0**。三轮实测见 §8 |
| passed | **153 → 157** |

## 1. 范围

阶段 A（doctor 探针阈值、clean 文案，已验收，不改）+ 阶段 B 判据重做。
不改 `--force` 语义；不加新命令、不加新开关；不 push；不动
`wt/tmp-evidence-probe-r1` 残骸。

## 2. 改动文件

| 文件 | 改动 |
|---|---|
| `src/agentgate/verifier.py` | 删 `_previous_diff_sha`；新增 `_previous_mechanical_failures`；`_record_no_progress` 改写并返回比较详情；无净进展门从 protected 之前挪到机检之后；`VerificationOutcome.no_progress` |
| `src/agentgate/orchestrator.py` | `verdict is None` 分支把 `verification.no_progress` 抄进本轮状态（否则被内存态覆盖） |
| `src/agentgate/report.py` | 结论段改成"机检失败集合与 R<N-1> 完全相同（check 名）" |
| `tests/test_verifier.py` | P1-3 段落重写：7 条用例 + 双 check 夹具（`add_second_check` / `set_exit` / `counting`） |
| `tests/test_report.py` | 文案与 `no_progress` 字段跟着改 |
| `tests/test_cli.py` | `blocked_reason` 文案跟着改（断言不变：exit 1） |
| `tests/test_orchestrator.py` | **新增 1 条**：编排器必须把 `no_progress` 详情留在 state 里 |
| `docs/borrowings.md` | P1-3 行与说明改写（判据换层，来源与独立性不变） |

阶段 A 涉及的文件（`doctor.py`、`workspace.py`、`test_doctor.py`、`test_clean.py`）
本批一字未动。

## 3. 阶段 A（已验收，摘要）

- `doctor.py:231` 附近的默认验收方探针：`timeout_s` 45 → **75**，且只给
  `verifier_codex` 加 `attempts=2`（最坏 150s）。`executor_codex` 仍 `attempts=1`，
  有测试盯这条。
- `workspace.py` 的 `_permission_hint()`：删除失败且 stderr 含
  `directory not empty` / `permission denied` / `access is denied` 时，追加
  `takeown` / `icacls` / `rd` **三条独立命令**。不写自动清理。
- 维护者验收：A1/A2 **PASS**，并确认"三条独立命令行"比草稿里挤成一串更对。

## 4. 旧判据为什么不可达

三条代码事实（维护者复核时给的三条，我逐条核对过）：

```text
orchestrator.py:396   base_ref = previous["branch"]        # 每轮 diff 相对上一轮分支
collector.py:179      raise NoChangesError("no changes detected")
execution.py:140      except NoChangesError as exc:        # 执行阶段就 BLOCKED
```

推论：

1. **"树一点没变"到不了 `verify_round`**。执行完收集时 `git diff HEAD` 为空且
   porcelain 为空 → `NoChangesError` → `collect_blocked` → 该轮 BLOCKED，state
   里连轮次条目都不会追加（`runs/p13-checks` 就是活例：只有 R1）。
2. **"两轮相对 diff 字节相同"要求同一补丁打在两个不同基线上还逐字节一致**。
   剩下的只有巧合。
3. 我自己的真跑印证过：`7e9260d3…` vs `e19703b3…`（见 `runs/p13-signal`）。

所以第一版那 4 条 `verify_round` 用例只证明了"夹具能触发"，等于给一条永远走不到
的分支发合格证——这正是项目自己"不为不可能发生的场景写错误处理"的反面。

## 5. 新判据：比机检失败集合

### 5.1 为什么删（选 a）而不是留兜底

保留一段"现实不可达"的判定，还要同时保留能证明它的夹具用例，下一个人读到时
无法区分"这是防线"还是"这是没人删的旧码"。删掉以后，判据只有一处、只有一种
说法，`--force` 的语义也没变。**(b) 兜底** 要额外维护一条永远不生效的分支，
**(c)** 我拿不出可复现的真实路径（§4 已说明为什么），所以选 (a)。

### 5.2 规则

第 N（N≥2）轮的 `mechanical.failed` 的 **check 名集合**，与第 N-1 轮完全相同
→ 无净进展 → 终态 **FAILED**，`failure_reason = "no-progress"`。

用集合而不是 diff 的理由：它是 P0-1 已经算好的**确定性**结果，不含 LLM 措辞；
执行方大改代码但同样的 check 还挂着，正是最常见的白烧轮次；而"换个说法描述同一
缺陷"骗不过它（这本来就是 P1-3 要解决的问题）。

四种情况**不判**（代码里逐条对应）：

| 情况 | 代码位置 | 为什么 |
|---|---|---|
| 只对 N≥2 | `_previous_mechanical_failures` 首行 `if round_no < 2: return None` | 第一轮没有基线 |
| 上一轮没有 `mechanical` 记录 | `entry.get("mechanical")` 不是 dict → `None` | 旧 run，或上轮被 protected/BLOCKED 拦住，没有可比的东西 |
| 上一轮 `verified_fail` 但机检全绿 | `failed` 为空 → `()`，`if previous_failed and ...` 短路 | 失败来自验收官，与 check 集合无关 |
| 两轮集合都空 | 同上（`previous_failed` 为 `()` 即假） | 都通过了，走正常流程 |

另外还要求上一轮 `status == "verified_fail"`：只有"被判过的失败"才是基线，
一个没拿到判决的轮次（blocked / 还在等）不能当对照。

### 5.3 门顺序变了：从"机检之前"挪到"机检之后"

第一版把门放在 protected 之前，是为了"diff 没变就什么都不用跑"。新判据需要**本轮
自己的机检结果**，所以顺序变成：

```text
:814  subject = _frozen_subject(...) / _write_subject
:817  HEAD 前向门                      # 树动过 → stale-subject
:839  protected = _protected_gate(...)  # 命中 → BLOCKED
:857  mechanical = evaluate_checks(...)
:864  previous_failed = _previous_mechanical_failures(...)
:866  if previous_failed and previous_failed == current_failed: → FAILED
:897  collect_evidence(...) / regenerate_verifier_input(...)
:923  验收方
```

代价是 protected 与机检现在一定会跑（它们本来就是确定性的本地命令，不花模型钱）；
省掉的是**证据包收集**与**验收方调用**——命中时 `evidence.json`、
`verifier_input.md`、`verifier-attempt-*` 都不产生（真跑里 R2 的这三样确实没有）。

机检已挂时仍不回模型（P0-1 的原规则），所以命中无净进展的轮次"模型 0 次"是双重
保证：门在模型之前，且机检失败本身也会短路模型。

### 5.4 状态与报告

```json
{"status": "failed", "failure_reason": "no-progress",
 "no_progress": {"previous_round": 1,
                 "failing_checks": ["ci-runner", "net-reachable"],
                 "previous_failing_checks": ["ci-runner", "net-reachable"]},
 "subject": {...}, "mechanical": {"passed": [...], "failed": [...]}}
```

保留 `subject` 与 `mechanical`（它们是这条结论的证据），清掉 `verdict` 与
`defect_fingerprints`。注意 `ag run` 的内存态会在保存时把 `verdict` 写成 `null`、
`defect_fingerprints` 写成 `[]`——不是残留的旧判决，§8.2 有实测。

报告结论段第一行：

```text
无净进展：R2 的机检失败集合与 R1 完全相同（ci-runner、net-reachable），未调用验收方。
```

换成旧的"同缺陷指纹"仍可能同时成立，那时它是紧随其后的 `附加：…` 一行，主因只有
一个。每轮行写 `- R2: no-progress`，每轮事实写 `R2 无净进展`。

## 6. resume 误判排查（维护者指出的风险）

三个问题逐一回答，依据是代码，不是推测：

**① `resume` 在 BLOCKED 状态下会重做该轮吗？——不会。**
`blocked` 属于 `TERMINAL_STATUSES`（`orchestrator.py:28`）。`resume` 无 `--force`
直接报 `Run is already terminal (blocked); use --force to rerun.`（`:718`）；
带 `--force` 则**归档整个 run 目录**（移进 `runs/.trash/`）后调 `start_task`
重新开始（`:722` 起）——新 run 的 state 里没有旧轮次，不存在"与上一轮比较"。

**② 重做时 `base_ref` 与 `round_no` 怎么取？**
`_drive` 恒有 `round_no = len(rounds) + 1`（`:354`），而 `ensure_run_slot()`
在 `round-<N>` 目录已存在时直接抛 `CollectorError`。所以**轮号不会被重用**。
`base_ref = previous["branch"]`（`:396`）只在"上一轮 `verified_fail`、要开新一轮"
时取，永远指向上一轮分支，不会两次以同一基线开同一个轮号。

唯一会被"重做"的是**执行中途崩掉**的那一轮：state 里还没追加条目（`rounds.append`
在 `:480`，执行之后），轮号会重复用一次；但那一轮没有留下任何工件，也从未被判过，
所以它和新判据比较的仍是"上一轮真实的机检结果"，不是自己。

**③ 新判据在这条路径上会误判吗？——不会，不需要额外守卫。**
误判的原始形态是"同一基线 + 相同补丁 → 被判无进展"，那是**字节哈希判据**才有的
问题（它比的是"改动是否重复"）。新判据比的是"这批 check 是否还是同一批失败"，
与基线、与补丁字节都无关：重做一轮时执行方若真的推进了，check 集合就会变。

唯一保留的守卫是 §5.2 那条 `status == "verified_fail"`：万一将来出现"重跑同一轮号"
的新路径，只要那个轮次没有判决，就不会被当作基线。

## 7. 测试

```text
$ uv run pytest -v …（7 条）
tests/test_verifier.py::test_first_round_is_never_no_progress PASSED      [ 14%]
tests/test_verifier.py::test_same_failing_check_set_is_no_progress PASSED [ 28%]
tests/test_verifier.py::test_different_failing_set_is_progress PASSED    [ 42%]
tests/test_verifier.py::test_all_checks_passing_is_not_no_progress PASSED [ 57%]
tests/test_verifier.py::test_previous_mechanical_missing_is_not_no_progress PASSED [ 71%]
tests/test_verifier.py::test_previous_was_verifier_fail_is_not_no_progress PASSED [ 85%]
tests/test_verifier.py::test_no_progress_artifact_has_no_stale_verdict PASSED [100%]
7 passed in 12.50s
```

入口都是真实的 `verify_round` / `verify_run`；"模型不许被调用"的桩一旦被调用就
`raise AssertionError`，断言写成 `calls == []`（不是只看状态）。

| 用例 | 断言的是 |
|---|---|
| `test_first_round_is_never_no_progress` | R1 两个 check 都挂 → 普通机检 FAIL，`failure_reason == ""`，模型 0 次 |
| `test_same_failing_check_set_is_no_progress` | R1 `{hello-content, notes-file}` → R2 同集合：`calls == []`、`verdict is None`、`attempts == ()`、`FAILED` 在 / `BLOCKED` 不在、state 轮次 `failed`、`no_progress` 三个字段齐、`terminal_reason` 前缀 |
| `test_different_failing_set_is_progress` | R2 只剩 `{notes-file}`（R1 修好一个）→ 不是 no-progress，走正常机检结论；state 里连 `failure_reason` 都不写 |
| `test_all_checks_passing_is_not_no_progress` | 两轮机检全绿 → 轮到验收方（1 次调用），PASS |
| `test_previous_mechanical_missing_is_not_no_progress` | 抹掉 R1 的 `mechanical` → 不判，本轮出普通机检 FAIL，模型 0 次 |
| `test_previous_was_verifier_fail_is_not_no_progress` | R1 机检全绿但验收官判 FAIL → R2 不被判无进展，模型 1 次 |
| `test_no_progress_artifact_has_no_stale_verdict` | 预置旧 `verdict.json` 与一份空的 `mechanical.json` → `verdict.json` 消失；`mechanical.json` 是**本轮重算的**（两条失败），不是旧内容 |

另加 1 条编排器用例（本批发现的问题，见 §8.2）：
`tests/test_orchestrator.py::test_no_progress_detail_survives_the_orchestrator_state`
——断言 state 里 `no_progress.failing_checks` 还在，且 `report.md` 里没有 `R?`。

### 7.1 夹具

`prepare_run()` 只有一个 check。为了做出 `{A,B}` 与 `{A}` 的差别，新增
`add_second_check()`：往 run 的 `task.yaml` 的 `checks:` 末尾插一条
`notes-file`，并补 `02-notes-file.*` 三个工件；`set_exit()` 改某条 check 的退出码。

`write_run_state()` 改成**保留已有条目**：旧版整份重写会把 R1 的 `mechanical`
抹掉，让第二轮变成"第一轮"，测的就不是判据了。这也说明夹具本身要跟判据一起演进。

## 8. 证据：真跑

### 8.1 第一次（`runs/p13-checks`）：被更早的门挡住

任务：goal 要求建 `p13b-probe.txt`（内容 `ok`）与 `p13b-notes.md`；三条 check 中
`probe-exact` 会过，`ci-runner`（等 `GITHUB_ACTIONS` 环境变量）与 `net-reachable`
（连黑洞地址）在本机**必然**失败。

```text
Round 1: 机检未通过：ci-runner、net-reachable
Round 1: FAIL (2 defect) → continue
BLOCKED: no changes detected
BLOCKED + 1 轮 + 160.8s
```

R2 执行方什么都没改 → `collector.py:179` 的 `NoChangesError` 在收集阶段就 BLOCKED，
`runs/p13-checks/round-2/` 里只有 `executor.log` 与 `executor_prompt.txt`，没有
`artifacts/`。**这正是 §4 第 1 条推论的现场证明**：这条路径根本走不到 `verify_round`。

### 8.2 第二次（`runs/p13-checks2`）：命中，但暴露一个真 bug

同任务加一条 goal 要求：**每次执行都往 `p13b-runlog.md` 末尾追加一行**（不重写旧行）。
这样 R2 必然留下改动，同时三条 check 照样挂着。

```text
Round 1: 机检未通过：ci-runner、net-reachable
Round 1: FAIL (2 defect) → continue
Round 2: 机检未通过：ci-runner、net-reachable
Round 2: no-progress → FAILED
FAILED + 2 轮 + 179.2s
```

两轮的**机检失败集合完全一样**：

```text
R1 failed = ["ci-runner", "net-reachable"]   passed = ["probe-exact"]
R2 failed = ["ci-runner", "net-reachable"]   passed = ["probe-exact"]
```

两轮的 diff 哈希**不同**（这条最要紧，说明旧判据在这里不会触发、新判据触发了）：

```text
R1 diff_sha256 72591aeabb2586ec… files = [p13b-notes.md, p13b-probe.txt, p13b-runlog.md]
R2 diff_sha256 829c0b8ad95419cc… files = [p13b-runlog.md]
R2 diff:
+Round 2: appended this runlog entry; preserved the existing probe and notes.
```

`round-1` 与 `round-2` 下的 `verifier-attempt-*` 文件数：**0 / 0**。

**bug**：这一跑的 `report.md` 第一行写成了
`无净进展：R2 的机检失败集合与 R? 完全相同（（未记录））`。原因不是渲染，是
**`ag run` 把 state 读在内存里**：verifier 先往磁盘写了 `no_progress`，
orchestrator 随后用自己那份内存状态 `_save()` 覆盖，细节被抹掉。
修法：`VerificationOutcome.no_progress` + `orchestrator.py:551` 抄回本轮状态；
回归用例见 §7。

修好后在同一条 run 上重跑了 `verify_round(round_no=2)`（**只读冻结工件，0 次模型
调用**）把状态与报告补齐，这属于**修补一条被 bug 污染的状态文件**，不是伪造运行：

```text
failure_reason: no-progress
attempts: 0
```

### 8.3 第三次（`runs/p13-checks3`）：修好后端到端重跑，未做任何手工修补

```text
Round 1: 机检未通过：ci-runner、net-reachable
Round 1: FAIL (2 defect) → continue
Round 2: 机检未通过：ci-runner、net-reachable
Round 2: no-progress → FAILED
FAILED + 2 轮 + 155.8s
```

```text
$ 状态
status: failed
terminal: no-progress: round 2 fails the same 2 check(s) as round 1 (ci-runner, net-reachable)
R1 verified_fail  failing=["ci-runner","net-reachable"]
R2 failed         failing=["ci-runner","net-reachable"]
R2.no_progress = {"previous_round": 1, "failing_checks": [...], "previous_failing_checks": [...]}

$ 两轮 diff（不同）
R1 84cec74ce217c5fe… files = [p13b-notes.md, p13b-probe.txt, p13b-runlog.md]
R2 2e612c315e051480… files = [p13b-notes.md, p13b-runlog.md]

$ 验收方调用
R1: 0 个 verifier-attempt 文件
R2: 0 个 verifier-attempt 文件

$ runs/p13-checks3/report.md（前 4 行）
# p13-checks3  FAILED  2.6min / 2 轮 / $0.0097 / 双盲 ★★
## 结论
无净进展：R2 的机检失败集合与 R1 完全相同（ci-runner、net-reachable），未调用验收方。
机检未通过：ci-runner、net-reachable（1/3 通过）。
```

任务本身是刻意的：两条 check 依赖环境（CI 变量、外网），本机永远不满足。这不是
"改工件凑证据"——工件是真的、模型是真的、`ag run` 全路径也是真的，只是把
"同一批 check 反复失败"这个条件做成了确定会发生，而不是指望模型碰巧失败两次。

## 9. 偏离

| 项 | 维护者要求 | 实际 | 理由 |
|---|---|---|---|
| 检测位置 | 第一版：protected 与机检之前 | 新判据落到**机检之后** | 判据要吃本轮机检结果；省下的仍是证据包与验收方调用 |
| 状态清理 | 清掉 `verdict` / `mechanical` | 保留 `mechanical`（本轮重算的），只清 `verdict` / 指纹 | `mechanical` 是这条结论的证据，清了报告就只能说"某两个 check" |
| `test_different_failing_set_is_progress` | "R2 {A} → 正常验收" | 断言"普通机检 FAIL、模型 0 次" | 集合非空时 P0-1 本来就不调模型；"正常验收"在这里的终点是机检结论 |
| 文档 commit | §7.2 要求更新 step-11 与 borrowings，§7.1 要求一个 commit | code + tests + docs **同一次提交** | 一个 commit 里无法引用自己的 hash，`borrowings.md` 的 commit 列写成提交标题 |
| 附带修复 | 未要求 | 修 `no_progress` 被内存态覆盖 + 1 条回归用例 | 真跑直接暴露，不修的话报告只能打 `R?` |

## 10. 完成标志（真实输出）

```text
$ uv run pytest -q
157 passed, 2 subtests passed in 72.90s (0:01:12)

$ uv run ruff check
All checks passed!

$ uv run ruff format --check
42 files already formatted

$ git status --porcelain
（提交后为空）
```

`ag doctor` 本批未改（阶段 A 已验收，实测 23:59:27→00:00:38，71s，★★，验收方探针
18.69s 一次通过）。未 push。
