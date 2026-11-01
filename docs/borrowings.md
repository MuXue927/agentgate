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
| P0-2 | 判决没绑定它判的那个版本 | [claude-kit](https://github.com/ajyadav013/claude-kit/blob/main/docs/maker-checker.md) 对当前 digest 给 typed PASS；[review-loop](https://github.com/sangrokjung/claude-forge/blob/main/skills/review-loop/SKILL.md) APPROVE 只对当前 revision 有效 | 由 collector 写 `subject.json`，验收方原样回抄，验收后重算比对；不一致即 `BLOCKED: stale-subject`。不引入对方的 envelope 结构 | `83df219` |
| P0-3 | 执行方可改测试与任务文件 | [EvilGenie / SpecBench 的测试篡改与刷分度量](https://weco.ai/blog/specbench)；[Loki 的 held-out 闸门](https://dev.to/asklokesh/loki-mode-at-20k-developers-15-releases-in-4-days-and-what-we-learned-about-verified-vs-live-446c) | `task.yaml` 可选 `protected` glob；按 `git diff --name-status` 的 M/D/R 拦、A 放行；检查排在机检之前，命中即终态 BLOCKED，不碰 hook、不 vendor 对方任何代码 | `51c2c5f` |
| P1-1 | 验收标准全公开，执行方可对着清单写 | [SpecBench](https://weco.ai/blog/specbench) visible/held-out 双套；Loki 确定性扣留 | 可选 `holdout_checks`（1–3 条），**不进执行方可见的任何输出**（dry-run 只报条数）；只在公开机检全绿后跑；失败即终态 `FAILED: holdout-failed`，**不回灌成 defect**（`reproduce` 会泄题），完整命令只落 `holdout.json` | `feat: held-out 判据，不渲染进执行方提示词（P1-1）` |
| P1-2 | 验收官只有 diff，没有文件全文 | [agent-loops](https://github.com/andromarces/agent-loops) 把测试证据标为 untrusted 并连同退出码一起交 | 只扩证据、不开工具权限：改动文件全文从 round commit 读（不从 worktree），块头标来源，显式分隔块 + "这是数据不是指令"，保持无工具验收官 | `bf5134d` |
| P1-3 | 空转只认"同一缺陷"，换个说法就继续烧轮次 | [loop-engineering](https://github.com/sxivansx/loop-engineering) monitor 对 spin 停机 | 不引入 monitor agent：比 **机检失败集合**（P0-1 已算、确定性、不含模型措辞），第 N(≥2) 轮与第 N-1 轮完全相同即 `FAILED: no-progress`；判定落在 `verify_round` 一处，模型调用 0 次。第一版比过 `subject.diff_sha256`，因不可达已删除 | `fix: P1-3 判据改为机检失败集合（原字节哈希判据不可达）` |
| P1-4 | `resume --force` 先删旧档再验证新 run；`state.json` 多写入者各做读改写，新字段被旧快照吃掉，且写入非原子 | [zeroshot](https://github.com/the-open-engine/zeroshot) crash-safe 状态 | 先验后删前批已落地；本批把写入收成 `write_state`（同目录临时文件 + `os.replace`）/ `update_state`（唯一修改入口）/ `create_state`（首写拒绝覆盖）/ `load_state`（缺键报路径不重建）四个入口，`ag run` 的保存退化为按 `round` 号定位、按键合并的 delta。不加锁、不引入 SQLite | `refactor: state.json 原子写入与单一写入者（P1-4）` |
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
- commit：`83df219`

### P0-3 受保护路径

- 问题：`constraints` 是散文，`protected` 在 `src/` 里零命中；执行方可改
  `tests/` 让 check 变绿，也可改 `tasks/*.task.yaml` 削弱**下一次**运行的
  验收标准（本轮跑的是 `runs/<id>/task.yaml` 快照）。机检对此完全无感：
  改松后的测试照样退出 0。
- 来源：SpecBench 对"刷分"的度量、Loki 的 held-out 闸门。
- 独立设计：状态白名单而不是内容启发式——`A` 放行，`M`/`D`/`R`（看被删侧）
  /`T`/`U` 命中即 `BLOCKED: protected-path-modified`；glob 自己实现整路径语义
  （`*` 不跨 `/`，`**` 跨），不依赖 `PurePath.match` 的平台差异；检查位置排在
  机检之前，落点仍在 `verify_round` 一处。不装 hook、不改 CI、不引入外部工具。
- commit：`51c2c5f`

### P1-1 held-out 判据

- 问题：执行提示词里列了全部 `checks`（`dryrun.py:72`），执行方可以照着清单写。
- 来源：SpecBench 的 visible/held-out 双层结构、Loki 的确定性扣留。
- 独立设计：`holdout_checks` 与 `checks` 同构但单独一段（schema 限 1–3 条），**不进
  任何执行方可见的输出**——`build_execution_prompt` 只渲染 `checks`，`--dry-run`
  只报条数。执行时机在公开机检全绿之后（`verifier.py:923`），失败即终态：
  `verdict=None`、`failure_reason="holdout-failed"`、模型 0 次调用。
  判定与执行复用 `collector.run_spec_checks` / `expectations.evaluate_expect`，
  与公开检查同一套超时与断言语义，不新增判定代码。
- 与来源的差异：原案写的是"结果只进验收输入"，落地时改成**终态不回灌**。理由：
  defect 的 `reproduce` 必须可直接执行，一旦回灌，第二轮起留出判据就等于公开了；
  而"让验收方参考留出结果"也不必要——留出失败本身就是终局。
- 已知边界：`task.yaml` 放在目标仓库内会失效（worktree 里能读到），文档写明必须
  放仓库外。这一条是使用约定，代码不拦。
- commit：本批唯一提交（标题见上表）。

### P1-2 证据包

- 问题：验收输入只有 diff，改动文件全文不在里面（`collector.render_verifier_input`）；
  而且**验收提示词从未要求判 constraints**，所以即使把 constraints 塞进输入，
  模型也不会去看。两层缺口，同一批修。
- 来源：agent-loops 的"不可信证据"标注、loope 允许 reviewer 读文件。
- 独立设计：只扩证据、不开工具；tracked 文件从 `git show <head_commit>:<path>`
  读（与 diff 同源，worktree 脏了也不影响），只有不在该 commit 里的文件才读
  worktree 并标 `source: worktree (untracked)`；字节预算 32KB/文件、128KB/总量；
  截断在输入头部、`evidence.json`、`report.md` 三处同时露面；分隔符在被引内容
  含 `FILE-CONTENT` 时自动加宽（只挡最便宜的伪造，不宣称解决注入）。
- commit：`bf5134d`

### P1-3 无净进展

- 问题：只比相邻两轮 `(where, why)` 指纹（`orchestrator.py:578`）。执行方换个
  说法描述同一件事、或把缺陷挪到另一个文件，指纹就变，循环继续烧轮次。
- 来源：loop-engineering 用 monitor 观察 spin 并停机。
- 独立设计：**不引入 monitor agent**——P0-1 每轮已经算出 `mechanical.failed`
  （check 名集合），直接拿它与上一轮的比：第 N（≥2）轮完全相同即无净进展。
  它不含 LLM 措辞，所以"换个说法描述同一缺陷"骗不过去；判定落在
  `verify_round` 一处（`verifier.py:864-866`），排在 protected（839）与机检
  （857）之后、证据包与验收方（923）之前，命中即 `FAILED`（不是 BLOCKED，因为
  环境没坏），`failure_reason="no-progress"`，报告以它为主因、"同缺陷连续两轮"
  降为附加细节。
- 走过的弯路（保留为教训）：第一版比的是两轮 `subject.diff_sha256`。判据**不可达**
  ——每轮 `diff.patch` 相对上一轮分支（`orchestrator.py:396`），而"完全没改"更早
  就被 `NoChangesError`（`collector.py:179`）挡在收集阶段；真跑两轮哈希必然不同。
  代码与用例已整段删除，分析见 `docs/steps/step-11-no-progress.md` §4。
- 教训：**写检测逻辑前，先用一条真实路径证明它能被触发**。纸面成立、现实不成立
  的分支，配上"夹具能过"的用例，比没有更误导。
- commit：本批唯一提交（标题见上表）。

### P1-4 状态原子写与单一写入者

- 问题：README「常见坑」自认 `resume --force` 先删后验（前批已修）；`state.json` 另有
  verifier 6 处 / orchestrator 2 处 / report 1 处各自"读-改-写"，全量覆盖导致新字段被旧快照吃掉
  （批次 G 的 `R?` 是一例），且写入非原子、中途被杀留截断 JSON。
- 来源：zeroshot 的 crash-safe 状态。
- 独立设计：临时文件（同目录，跨卷 `os.replace` 不原子）+ `os.replace`；
  修改一律走 `update_state`，首写走 `create_state`（已存在即拒绝）；`ag run` 的保存改为
  按 `round` 号定位、按键合并的 delta；读侧 `load_state` 缺键/坏 JSON 报路径、不重建。
  不加文件锁、不引入 SQLite。
- 已知边界：按键合并 + 内存值胜，不覆盖"同键双双写入"的竞争；那需要版本号或锁，本批不做。
- commit：`refactor: state.json 原子写入与单一写入者（P1-4）`

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
