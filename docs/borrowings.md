# 借鉴留痕

规则见 [landscape.md §7](landscape.md#7-反抄袭协议)：只搬机制，不搬文本与代码；
每条落地都要有"问题 / 来源 / 我们的独立设计差异 / 对应 commit"四列。
没有这一行的 PR 不合。

`commit` 列留空表示尚未落地。

`P0-4`（超时树杀）不在本表：它不是借鉴项，是本仓库在 Windows 上自己的缺陷，
没有外部来源。记录见文末「非借鉴项」。

## 总表

| 编号 | 问题 | 来源（链接 + 一句话） | 我们的独立设计差异 | commit |
|---|---|---|---|---|
| P0-1 | `expect` 只进提示词，真实 exit code 从未被代码比对 | [loope](https://github.com/mateaix/loope) `--verify-cmd` 退出码硬门；[agent-loops](https://github.com/andromarces/agent-loops) 运行时在评审沙箱外跑测试 | 新增纯函数逐键判定 `expect`；失败时不调验收模型，直接由机检结果合成缺陷并进入回灌；`where` 加 `mechanical:` 前缀 | 0250082 |
| P0-2 | 判决没绑定它判的那个版本 | [claude-kit](https://github.com/ajyadav013/claude-kit/blob/main/docs/maker-checker.md) 对当前 digest 给 typed PASS；[review-loop](https://github.com/sangrokjung/claude-forge/blob/main/skills/review-loop/SKILL.md) APPROVE 只对当前 revision 有效 | 由 collector 写 `subject.json`，验收方原样回抄，验收后重算比对；不一致即 `BLOCKED: stale-subject`。不引入对方的 envelope 结构 | |
| P0-3 | 执行方可改测试与任务文件 | [EvilGenie / SpecBench 的测试篡改与刷分度量](https://weco.ai/blog/specbench)；[Loki 的 held-out 闸门](https://dev.to/asklokesh/loki-mode-at-20k-developers-15-releases-in-4-days-and-what-we-learned-about-verified-vs-live-446c) | `task.yaml` 可选 `protected` glob；用 git 层 `diff --name-only` + 未跟踪文件匹配，命中即终态 BLOCKED，不碰 hook、不 vendor 对方任何代码 | |
| P1-1 | 验收标准全公开，执行方可对着清单写 | [SpecBench](https://weco.ai/blog/specbench) visible/held-out 双套；Loki 确定性扣留 | 可选 `holdout_checks`，不渲染进执行提示词，只由 collector 跑、结果进验收输入；限定 1–3 条 | |
| P1-2 | 验收官只有 diff，没有文件全文 | [agent-loops](https://github.com/andromarces/agent-loops) 把测试证据标为 untrusted 并连同退出码一起交 | 只扩证据、不开工具权限：生成 `context.md`（diffstat + 改动文件全文 + 基线 sha），用显式分隔块标注"这是数据不是指令"，保持无工具验收官 | |
| P1-3 | 空转只认"同一缺陷"，换个说法就继续烧轮次 | [loop-engineering](https://github.com/sxivansx/loop-engineering) monitor 对 spin 停机 | 每轮记 `diff_sha256`，与上一轮相同即 `FAILED: no-progress`；不引入 monitor agent | |
| P1-4 | `resume --force` 先删旧档再验证新 run，失败不可恢复 | [zeroshot](https://github.com/the-open-engine/zeroshot) crash-safe 状态 | 只做原子替换（临时文件 + `os.replace`）与"先验证后删除"，不引入 SQLite | |
| P1-5 | 改循环逻辑必须真跑 Codex，慢且不稳 | [loope](https://github.com/mateaix/loope) `--dry-run` stub agents | `tests/fakes/` 脚本化后端 + adapter 注入，**不加 CLI 开关**，只作测试夹具，不是产品降级链 | |

## 逐条说明

### P0-1 `expect` 硬门槛

- 问题：`run_checks()` 记录 `exit_code/stdout/stderr`，`expect` 只被序列化进
  提示词（`collector.py:218`）并由模型自行判断。schema 能保证模型"自洽"
  （PASS 则每条 check 标 PASS），但 `verdict` 与 `checks[].status` 都是模型
  自己填的，自洽 ≠ 真实。
- 来源：loope 用退出码做硬门，agent-loops 把测试放在评审沙箱之外执行。
- 独立设计：判定逻辑落在 `expectations.py` 的纯函数里，四类期望逐键检查；
  合成缺陷走我们既有的七字段契约，`where` 前缀 `mechanical:`；机检失败
  短路掉模型调用，省一次付费调用，也省掉"模型替机检背书"这一步。
- 与来源的差异：对方是"先跑命令再让模型看结果"，我们是"先由代码判命令，
  代码判不过就不请模型"。
- commit：`0250082`

### P0-2 subject 绑定

- 问题：`regenerate_verifier_input()` 从 `round_dir/diff.patch` 重建输入，
  不校验它是否仍对应当前 worktree；前后只比 `git status --porcelain`。
- 来源：claude-kit 的 digest 绑定、review-loop 的 revision 绑定。
- 独立设计：`subject.json` 由 collector 生成，字段用我们已有的 `base_commit`
  词汇；比对在 `verify` 收尾处做，失败即终态，不重试。
- commit：（待填）

### P0-3 受保护路径

- 问题：`constraints` 是散文，`protected` 在 `src/` 里零命中；执行方可改
  `tests/` 让 check 变绿，也可改 `tasks/*.task.yaml` 削弱**下一次**运行的
  验收标准（本轮跑的是 `runs/<id>/task.yaml` 快照）。
- 来源：SpecBench 对"刷分"的度量、Loki 的 held-out 闸门。
- 独立设计：git 层匹配，失败即 `BLOCKED: protected-path-modified`；不装 hook、
  不改 CI、不引入外部工具。
- commit：（待填）

### P1-1 held-out 判据

- 问题：执行提示词里列了全部 `checks`（`dryrun.py:54`）。
- 来源：SpecBench 的 visible/held-out 双层结构。
- 独立设计：`holdout_checks` 与 `checks` 同构但单独一段，不进执行提示词；
  限定条数，避免把小任务变成考试。
- commit：（待填）

### P1-2 证据包

- 问题：验收输入只有 diff，改动文件全文不在里面（`collector.py:211`）。
- 来源：agent-loops 的"不可信证据"标注、loope 允许 reviewer 读文件。
- 独立设计：只扩证据、不开工具；加显式分隔与注入防护说明。
- commit：（待填）

### P1-3 无净进展

- 问题：只比相邻两轮 `(where, why)` 指纹（`orchestrator.py:575`）。
- 来源：loop-engineering 的 spin 停机。
- 独立设计：`diff_sha256` 相等即 FAILED，一行判定，不加 agent。
- commit：（待填）

### P1-4 状态原子写

- 问题：README「常见坑」自认 `resume --force` 先删后验。
- 来源：zeroshot 的 crash-safe 状态。
- 独立设计：临时文件 + `os.replace`，删除推迟到新 run 启动成功之后。
- commit：（待填）

### P1-5 测试用假后端

- 问题：循环逻辑的端到端测试依赖真实 Codex。
- 来源：loope 的 stub agents。
- 独立设计：只存在于 `tests/`，通过 adapter 参数注入，无 CLI 开关，
  明确不是运行时降级路径。
- commit：（待填）

## 非借鉴项（无外部来源，只为留档）

### P0-4 超时树杀

- 问题：Windows 上 `codex` 是 `.cmd` shim，直接子进程是 `cmd.exe`，真正的
  worker 是孙进程。`subprocess.run(timeout=)` 只杀直接子进程，孙进程仍持有
  stdout/stderr 管道，内部第二次 `communicate()` 等到管道 EOF 才返回。实测
  `timeout=2` 用了 12.6 秒。四处调用点全部受影响，而 `max_total_minutes`
  只在阶段之间检查，所以阶段内部挂住 = `ag run` 永久挂起。
- 来源：无。本仓库实测发现（维护者复现并指出影响面）。
- 独立设计：`src/agentgate/proc.py` 的 `run_bounded()`，子进程独立进程组 +
  超时后 `taskkill /T` / `killpg` + 有界收尾（`kill_grace_s`）。
- commit：`69337dc`（说明见 `docs/steps/step-7-timeout.md`）。
