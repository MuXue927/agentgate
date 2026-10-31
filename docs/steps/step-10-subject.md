# STEP 10：P0-2 判决绑定证据身份

上游：STEP 9（P1-2 证据包，`bf5134d` / `0c26e6d`，已推 CI 绿）
commit：代码 `83df219`（本报告随文档提交）

## 1. 范围

只做 P0-2。不做 P1-3、P1-5；不新增 `ag reverify`；不改 `--force` 的语义
（它仍然只重问冻结证据），只把这条语义写进文案。

这一批**改 schema**（`verdict.schema.json` 增必填 `subject`），与上一批
"不改 schema"的边界不同——那条边界是为 P0-1 定的（当时要说清"判决自洽不需要新字段"），
本批是维护者明确要求的字段，不冲突。

要修的缺口：判决是一句孤立的话，没有绑定它判的是哪份证据。导致两件事：

1. `ag verify --force` 在 worktree 已经变过之后仍能给出 PASS（STEP 9 §7.1 的实测）；
2. 验收期间 / 之后有人 `git commit`，porcelain 是空的，现有检测看不见。

## 2. 改动文件

| 文件 | 改动 |
|---|---|
| `src/agentgate/subject.py`（新增） | `Subject` / `compute_subject` / `parse_changed_paths` / `subject_mismatch`，纯函数、无 IO 之外的依赖 |
| `schemas/verdict.schema.json` | 顶层 `required` 增 `subject`，并定义四个子字段（`additionalProperties: false`） |
| `src/agentgate/collector.py` | `render_verifier_input` 增 `subject` 参数，`## Subject` 段排在 `## Goal` 之前 |
| `src/agentgate/expectations.py` | `build_mechanical_verdict` 接收 `subject`，机检判决也带绑定 |
| `src/agentgate/verifier.py` | 写 `subject.json`；`## Subject` 注入卷宗；提示词要求原样回抄；回抄比对（不一致重试）；验收前后比 `HEAD`；比对冻结工件哈希；`_record_protected_block` 抽出 `_record_blocked_round` |
| `src/agentgate/report.py` | 结论段首行「判决绑定：base · head · diff（N 个文件）」 |
| `src/agentgate/cli.py` | `--force` 文案与 `verify` docstring 说清"不重新收集" |
| `tests/test_subject.py`（新增） | 14 个用例（解析 / 哈希 / 比对 / 报告行） |
| `tests/test_verifier.py` | 新增 8 个门禁用例；`pass_json`/`fail_json` 改为按需回抄 subject |

## 3. 五个实现决定

### 3.1 `head_sha` 取实测 `git rev-parse HEAD`，不取轮次快照

轮次自己的 `state.json` 是收集时的快照，早于 checkpoint commit（STEP 9 §3.1 同款坑）。
所以 `subject.head_sha` 在**验收开始时**实测，`base_commit` 取 run state，`diff_sha256`
是 `diff.patch` 的字节哈希，`changed_files` 从冻结的 status 段解析。

配套加一道**先于模型**的门：若 run state 里记着 `head_commit` 且与实测不同，
直接 `BLOCKED: stale-subject`，一次模型调用都不发。

> 这就是 STEP 9 §7.1 那条实测的正式结论：`--force` 不是"重新验收当前工作区"，
> 它是"拿同一份冻结证据再问一次法官"。树变了就拒绝，而不是拿旧证据判新树。

### 3.2 回抄要求写进 schema，截断容忍但归一化

`subject` 是 `required`，模型不给就 schema 不过。但两条宽容必须留：

- **比对用前缀匹配**（≥7 位）。40 位哈希被截断是抄写失误，不是另一个 subject；
  真不同的 commit 不会共享 7 位前缀。
- **回抄不一致重试一次**，两次不一致才 `BLOCKED: subject mismatch twice`。
  理由：小模型抄错一次很常见，直接 BLOCKED 会把抄写失误当成判决失败。

回抄通过后把 `subject` **归一化**成计算出来的完整对象再落盘，所以
`verdict.json` 里永远是 40/64 位全量（有测试盯这条）。

**这一条再次印证 STEP 9 的结论：约定要配示例，只写"你应该懂"不够。** 提示词里
没有说"照抄这个对象"，只说"顶层字段包括 subject"时，模型会自己重新推导一个
（`changed_files` 写成 diff 里的路径前缀、`head_sha` 写成 base）。所以这次的措辞是
`Copy it from the verification input unchanged, byte for byte, including every
file name in changed_files`——"包括每一个文件名"这句是被上一批的 `where` 前缀教出来的。
凡是要模型回填的结构化字段，都得在提示词里说清"从哪抄、抄到什么程度"。

### 3.3 三道时间窗检查，覆盖三种"证据变了"

| 窗口 | 检查 | 命中 |
|---|---|---|
| 开始前 | run state 的 `head_commit` == 实测 HEAD | `stale-subject: ... recorded commit` |
| 每次 attempt 后 | `git status --porcelain` 未变 | `verifier modified the worktree` |
| 每次 attempt 后 | 实测 HEAD 未变 | `stale-subject: ... HEAD moved during verification` |
| 每次 attempt 后 | `diff.patch` 哈希 + 文件清单未变 | `stale-subject: ... frozen artifacts changed` |

`HEAD` 那条是这次补的洞：干净树加一个 commit，porcelain 一个字节都不变。

### 3.4 机检判决也带 subject

`build_mechanical_verdict` 原来注释写"形状匹配 schema，不需要改 schema"——这句话
在本批之后就是错的，一并改掉。机检 FAIL 也是关于某一轮的判决，报告要能同样绑定。

### 3.5 `## Subject` 排在最前

理由和 STEP 9 把 `## Constraints` 放在 Diff 之前一致：规则先于证据。Subject 是
所有证据的身份，"先看到身份、再看到内容"。

## 4. 测试

```text
$ uv run pytest -q tests/test_subject.py
14 passed in 0.57s

$ uv run pytest -q tests/test_verifier.py
21 passed in 20.53s

$ uv run pytest -q
144 passed, 2 subtests passed in 62.25s
```

新增 8 个门禁用例（`tests/test_verifier.py` 末尾）：

| 用例 | 断言的是 |
|---|---|
| `test_subject_json_freezes_the_evidence` | `diff_sha256` == `diff.patch` 的 sha256；`head_sha` == `git rev-parse HEAD`；`verdict.subject` == `subject.json` |
| `test_verdict_without_subject_is_never_persisted` | 两次都不给 → 2 次调用、`verdict.json` 不存在、reason 含 `subject` |
| `test_wrong_subject_is_blocked_as_mismatch_not_parse_error` | reason 以 `subject mismatch twice` 开头，不是"解析失败" |
| `test_truncated_echo_is_accepted_and_normalised` | 7 位 / 12 位截断 → PASS，落盘仍是 40/64 位 |
| `test_worktree_head_behind_the_round_blocks_before_the_model` | 模型调用 0 次，`BLOCKED` 文件存在 |
| `test_head_moving_during_verification_blocks` | 空 commit → `HEAD moved during verification`，且 `before_status == after_status` |
| `test_frozen_patch_changing_during_verification_blocks` | 改 `diff.patch` → reason 含 `diff_sha256` |
| `test_mechanical_verdict_carries_the_subject_too` | 机检 FAIL 也带同样的 `subject`，`where` 仍是 `mechanical:` |

**改了既有测试的夹具，没改既有断言**：`pass_json()` / `fail_json()` / `model_pass()` /
`model_verdict()` 现在需要回抄 subject（schema 必填后的必然结果）。断言一行未动。
`test_one_invalid_then_valid_retries_once` 里 `pass_json` 原来在测试准备阶段求值
（那时 `subject.json` 还不存在），改成在 runner 内按需构造。

## 5. 真实运行（模型真调用）

任务 `p02-subject`：在仓库根建 `p02-probe.txt` 内含 `subject-ok`，只许改这个文件。

### 5.1 `ag run` → PASS，一次调用拿到绑定

```text
$ uv run ag run "…/ag-p02.task.yaml" --allow-self
Round 1: PROTECTED: none declared
Round 1: PASS → DONE
PROTECTED: none declared
机检: 1/1 通过
Verdict: PASS（验收官）— p02-probe.txt created with 'subject-ok', only file
modified, all constraints satisfied
DONE + 1 轮 + 97.2s
Branch: ag/p02-subject-r1
```

`subject.json`（`runs/p02-subject/round-1/subject.json`）：

```json
{
  "base_commit": "83df219a8d5aa9054dc298f70af2812ecd7fa698",
  "head_sha": "b20a7e461bafcd31ecd9fb9458e63f31f75c6ca3",
  "diff_sha256": "0fa355665537f27bbe02637bf757e38890bb91774e43979df2ed77beb63bbdf4",
  "changed_files": ["p02-probe.txt"]
}
```

三处交叉核对（真输出，不是抄的）：

```text
$ git -C wt/p02-subject-r1 rev-parse HEAD
b20a7e461bafcd31ecd9fb9458e63f31f75c6ca3

$ sha256sum runs/p02-subject/round-1/diff.patch
0fa355665537f27bbe02637bf757e38890bb91774e43979df2ed77beb63bbdf4 *…diff.patch

$ cat runs/p02-subject/round-1/verifier-attempt-1.exit.txt
0
```

`verifier-attempt-1.exit.txt` 是 `0` 且有 `verdict.json` 却没有 `verifier-attempt-2`：
**模型第一次就把 subject 原样抄回来了**。`verdict.json` 的 `subject` 与
`subject.json` 逐字节相同。

`report.md` 结论段第一行：

```text
## 结论
判决绑定：base 83df219 · head b20a7e4 · diff 0fa35566（1 个文件）。
PROTECTED: none declared
机检：1/1 通过。
R1 PASS。
```

### 5.2 在 worktree 里加一个空 commit → `--force` 被拒

```text
$ git -C wt/p02-subject-r1 commit --allow-empty -m "someone committed in the worktree after verification"
$ git -C wt/p02-subject-r1 log --oneline -2
098afbc someone committed in the worktree after verification
b20a7e4 ag: round 1

$ uv run ag verify p02-subject --force
PROTECTED: none declared
BLOCKED: stale-subject: worktree HEAD 098afbc is not the round's recorded commit
b20a7e4; collect the round again instead of re-verifying frozen artifacts
EXIT=2
```

**这正是 STEP 9 §7.1 缺失的那道门**：同一个操作在上一批得到 PASS，现在被拒绝，
且拒绝发生在模型之前（没有新增 `verifier-attempt-*`）。

副作用按设计清干净了：

```text
$ python -c "…state.json…"
status: blocked
terminal_reason: stale-subject: worktree HEAD 098afbc is not the round's recorded commit …
round status: blocked
has verdict key: False

$ ls runs/p02-subject/round-1/
artifacts  changed_files.txt  diff.patch  executor.log  executor_prompt.txt
protected.json  state.json  subject.json  verifier_input.md
```

`verdict.json` / `mechanical.json` / `evidence.json` 都被清掉（否则报告会同时说
"R1 PASS"和 BLOCKED），`subject.json` 保留——它记录的是"被拒绝的那份身份"。

### 5.3 把 HEAD 恢复后再 `--force` → PASS，绑定不变

```text
$ git -C wt/p02-subject-r1 reset --hard b20a7e4
b20a7e4

$ uv run ag verify p02-subject --force
PROTECTED: none declared
机检: 1/1 通过
Verdict: PASS（验收官）— p02-probe.txt contains 'subject-ok' as required, no
other files modified
EXIT=0
```

第二次判决的 `subject` 与第一次逐字段相同，`diff_sha256` 仍是
`0fa35566…`：**`--force` 的语义现在是可验证的**——同一份冻结证据，重问一次法官。

## 6. 顺带发现（不在本批范围，未做）

1. **dry-run 里没有 `## Subject` 段。** 干跑时还没有轮次工件，编一个假 subject
   比不显示更糟。`dryrun` 的验收提示词预览也没提这个字段——真验收时模型会从
   卷宗里看到，不影响。如果以后要"干跑和真跑逐字一致"，这条要一起改。
2. **`ag reverify` 仍然不存在。** 现在"重新收集 + 验收"只能靠 `ag run` 或手工装配。
   维护者已说过这是新功能，不塞进 `--force`。
3. **老 run 没有 `head_commit` 时无法检测漂移**（`prepare_run` 这类手装轮次）。
   此时 `subject.head_sha` 仍是实测值，但"录的 commit ≠ 现在"这道门跳过。已在
   `_round_head_commit` 的 docstring 里写明"返回 None 是安全的"。
4. **`wt/tmp-evidence-probe-r1` 残骸仍在**（STEP 9 §7.2）。`wt/` 在 .gitignore 里，
   不影响 `git status`/CI/后续 run；清理需要管理员权限。

## 7. 与方案/原案的偏离

| 项 | 维护者要求 | 实际 | 理由 |
|---|---|---|---|
| 回抄不匹配 | 直接 BLOCKED | 重试一次，两次才 BLOCKED | 抄写失误 ≠ 判决失败；schema 错误本来就重试 |
| 哈希比对 | 重算比对 | 前缀容忍（≥7 位）| 小模型常截断；截断不掩盖真差异 |
| `changed_files` 顺序 | 未说 | 排序后比对 | 顺序不是身份 |
| 机检判决 | 未说 | 也带 `subject` | 同一份证据的判决应当同一种形状 |
| 报告 | 未要求 | 结论段加一行「判决绑定」 | 不写这一行，读者无法区分"判的是合并树"还是"判的旧树" |

## 8. 完成标志（真实输出，2026-10-31）

```text
$ uv run pytest -q
144 passed, 2 subtests passed in 62.25s

$ uv run ruff check
All checks passed!

$ uv run ruff format --check
42 files already formatted

$ git status --porcelain
(空)
```

CI：`0c26e6d`（含 `bf5134d`）在 GitHub runner 上 `ruff check` / `ruff format --check` /
`pytest` 全绿（run `41201709515`）。

**未 push**：`83df219` 与本报告所在提交留在本地，等维护者确认。
