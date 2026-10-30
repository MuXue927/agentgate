# STEP 6：机检硬门槛（P0-1）

## 1. 范围

把 `task.yaml` 的 `checks[].expect` 从"写给模型看的文字"变成"代码执行的判定"。
任一 check 的真实输出不满足 `expect` 时，验收方（模型）**不被调用**，判决由
机检结果合成，`where` 带 `mechanical:` 前缀，随后走既有回灌路径。

不在本步：P0-2（subject 绑定）、P0-3（受保护路径）、P1-x；不改
`schemas/verdict.schema.json`；不加依赖。

## 2. 改动文件

| 文件 | 改动 |
|---|---|
| `src/agentgate/expectations.py` | 新增。`evaluate_expect` 逐键判定，`evaluate_checks` 汇总，`build_mechanical_verdict` 合成判决。纯函数，无 IO |
| `src/agentgate/verifier.py` | `verify_round` 在模型调用前跑机检；失败直接合成判决并返回，`attempts=()`；`VerificationOutcome.mechanical`；写 `round_dir/mechanical.json`；`state.json` 轮次条目加 `mechanical` |
| `src/agentgate/orchestrator.py` | 把 `mechanical` 同步进内存中的运行状态（否则会被 `_save` 覆盖），机检失败时输出一行事件 |
| `src/agentgate/cli.py` | `ag verify` 与 `ag run` 输出机检结论；`Verdict:` 行标注来源（机检 / 验收官） |
| `src/agentgate/report.py` | `## 结论` 首行输出 `机检：N/N 通过` 或 `机检未通过：<check 名>` |
| `tests/test_expectations.py` | 新增 4 条回归测试 + 1 条逐键表驱动测试 |

## 3. 关键决定与偏离

### 3.1 接入点放在 `verify_round`，不放在 `_drive`

`landscape.md` §4 写的是"`orchestrator` 在收集后先跑它"。实现放在
`verify_round`，原因：`ag run` 与 `ag verify` 都会经过它，若只在 `_drive`
加门，`ag verify` 仍可能对一棵机检已失败的树调用模型并打印 PASS——正是
§2.7 ③ 要修的误导。`orchestrator` 保留的职责是：把 `mechanical` 同步进
运行状态、输出事件。行为与要求一致（都在"调用验收方之前"），但只有一份判定
逻辑。

### 3.2 CLI 标签保留 `Verdict:` 前缀

原计划输出 `验收官: PASS — <summary>`。既有测试
`test_verify_fail_is_exit_zero_and_prints_defect` 断言 `"Verdict: FAIL" in output`，
按"不改既有测试断言"的约束，改为：

```text
机检: 1/1 通过
Verdict: FAIL（验收官）— The file content does not match.
```

机检失败时：

```text
机检未通过：hello-content（exit expected 0 actual 1）
Verdict: FAIL（机检，未调用验收官）
```

来源标注在括号里，机检与验收官仍是两行，不共享一行结论。

### 3.3 额外产物

`round_dir/mechanical.json`（与 `verdict.json` 同目录）保存
`{passed, failed:[{name, reason}]}`，便于事后核对"这轮到底机检了什么"，
也是 `state.json` 里同名字段的原始来源。

## 4. 验收与真实输出

### 4.1 全量测试

```text
$ uv run pytest -q
...................................................................... [ 74%]
........................                                                 [100%]
94 passed, 2 subtests passed in 37.73s
```

（基线 86 passed + 2 subtests；本步新增 8 个用例：4 条回归 + 4 条表驱动。）

### 4.2 静态检查

```text
$ uv run ruff check
All checks passed!
```

### 4.3 合成判决与指纹稳定性

```text
$ uv run python -c "..."   # 构造 exit=1、stdout 含 failed 的 CheckOutput
{
  "verdict": "FAIL",
  "summary": "mechanical checks failed: tests",
  "checks": [
    {
      "name": "tests",
      "status": "FAIL",
      "evidence": "exit expected 0 actual 1; stdout_not_contains forbidden \"failed\" appeared"
    }
  ],
  "defects": [
    {
      "id": "M1",
      "severity": "blocking",
      "where": "mechanical:tests",
      "why": "exit expected 0 actual 1; stdout_not_contains forbidden \"failed\" appeared",
      "reproduce": "uv run pytest -q",
      "expect": "{\"exit\": 0, \"stdout_not_contains\": \"failed\"}",
      "actual": "exit=1; stdout_tail=\"2 failed, 8 passed in 1.2s\"; stderr_tail=\"\""
    }
  ]
}
--- 指纹稳定性（同一 check、不同实际输出）---
R1 fingerprint: d4ed34f5412d
R2 fingerprint: d4ed34f5412d
```

同一 check、同一类失败，即使实际输出完全不同（`2 failed, 8 passed` vs
`5 failed, 3 passed`），指纹一致——`why` 只含期望值，实际输出只进 `actual`，
而 `defect_fingerprint` 只用 `(where, why)`。

### 4.4 后端档位（本次未取得新证据）

```text
$ PYTHONUNBUFFERED=1 timeout -k 5 300 uv run ag doctor
exit=124
```

`ag doctor` 在本次会话内 8 分钟未返回，被外部超时终止，且因为它在所有探测
结束后才一次性打印，没有留下任何部分输出。因此"档位仍为 ★★"**没有本次运行
的新证据**，不能写成"已验证"。

可核对的替代事实：本次提交没有触碰 `src/agentgate/doctor.py`——

```text
$ git diff --stat 0250082^ 0250082 -- src/agentgate/doctor.py
（无输出）
```

档位判定 `_tier()` 与探测逻辑均未变化。疑似挂起原因：探测通过 `codex` 的
Windows shim 启动，`subprocess.run(timeout=...)` 杀掉直接子进程后，孙进程仍
持有 stdout 管道，导致外层等不到 EOF。这与 P0-1 无关，需要单独排查，本步
不处理。

## 5. 已知边界

- 机检只判 `expect` 声明的四个键，不判"目标是否达成"。目标达成仍由验收
  模型判断，两侧都 PASS 才是 PASS（`test_all_checks_pass_still_requires_model_pass`）。
- 机检失败会消耗一轮 `max_rounds`（与模型判 FAIL 一致），只是不消耗模型调用。
- `checks[].evidence` 在合成判决里复用 `why` 文本；schema 要求该字段非空，
  没有第二个信息源可填。
- 若 `round_dir/artifacts` 缺失，`_stored_checks` 仍按原逻辑抛
  `VerificationError` → BLOCKED，不会被误判成机检 FAIL。
