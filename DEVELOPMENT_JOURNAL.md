# Development Journal

## 2026-04-03

- Added automated engineering logs under each book project: `99_engineering/DECISIONS.md`, `ISSUES.md`, `CHANGES.md`, `RUN_LOG.md`.
- Implemented degraded-mode operation when LLM endpoint is unavailable:
  - Planner/title generation uses structured fallback output.
  - Chapter pipeline can continue with rule-based review.
- Improved planner fallback to generate domain-specific long-serial planning for programmer-rebirth/创业题材, including 100+ chapter compatible volume layout.
- Enforced per-chapter minimum length via reviewer (`min_words`, default `2000`), configurable from CLI.
- Expanded offline writer fallback to produce long chapters near target length and include职场/情感双线 elements.
- Fixed anchor-check false positive for length-related anchors.
- Reduced fallback repeated marker artifacts to improve repetition score.

- 2026-04-03: 发现并行执行多次 run-chapter 会触发 index.json 竞争写风险，后续统一使用串行章节生成。
- 2026-04-03: Doubao 客户端新增可选流式优先能力（stream->non-stream自动回退），用于降低长文本生成超时风险。
- 2026-04-03: 新增 RecapAgent（前情概要角色），每章发布后自动更新全书压缩摘要/近期主线/下一章重点，并注入后续写作上下文。
- 2026-04-03: 新增 planner->writer 的“硬性一致性简报”（主角名/城市/时代/前情延续点），并在 reviewer 规则层新增城市/时代/前情衔接检查，防止章节断裂。
- 2026-04-03: 按用户要求回退默认调用路径为非流式（timeout模式）；流式改为可选开关（默认关闭），用于规避调用模式不稳定风险。
- 2026-04-03: 根据用户反馈，章节发布稿去除真实地名（上海等），改为虚构城市“临江市”；同时重写第2章为单主线紧凑叙事，并同步更新 story_facts 连续性配置。
- 2026-04-07: 调整 reviewer 重复短语门禁：repetition 从 mid 降为 low，扣分从 10 调整到 5，降低专有名词重复导致的误拦截。
- 2026-04-07: 按“失败即停止”门禁串行完成第3-10章生成；在第3/4/7/8章遇到门禁或超时时中断后续、定向修复并重跑；最终发布目录补齐0001-0010连续章节。
- 2026-04-07: 进行发布稿阅读流畅度精修：拆分第7章过长说明段、压缩第9章解释腔句子，保持事件不变，仅优化可读性。
- 2026-04-07: 连续性修复（1-21章）：统一禁用称呼“王经理”->“王堔”；修正第6/7/11/12/13/14/15/17章时间词回跳；补充第7章GPT-8配额口径从绝对值切换到百分比的过渡说明。
- 2026-04-07: 防复发加固：writer 增加时间线只前进规则；reviewer 新增 chapter_no>=10 的早期时间词回跳检测（mid）；续写脚本增加禁止回跳时间词约束。
- 2026-04-09: 修复Reviewer误拦截：对固定人名白名单（如林溪）触发“无铺垫新角色”类LLM误判时，自动降噪并移除对应must_fix，避免误杀通过门禁。
- 2026-04-09: 续写脚本容错升级：默认请求超时提升到420s，CLI内部重试=2，外层自动重试=4；网络/质量可恢复失败自动续跑，仅在高严重度剧情一致性问题时停机等待人工确认。
- 2026-04-10: 发布前定点优化（未触碰0001-0022）：修复0053与0065字数不足至>2000；修复0054中“上海牌手表”为“老机械表”，清除真实地名命中。
- 2026-04-15: 发布态清洗（23-100）：修复0023与0048中的系统批注体文本（【...】）为小说叙述/短信直引语；复检23-100无批注残留，格式与字数门槛通过。
- 2026-04-15: 章节标题去重改造：将重复标题章节批量改为口语化且唯一的新标题（共37章，覆盖24/25/36-45/56-65/76-85/96-100），并同步文件名与正文标题。
- 2026-04-15: 防复发：续写脚本按全局标题唯一性去重；orchestrator新增标题唯一化兜底，run-chapter遇重复标题会自动改为唯一标题后发布。
- 2026-04-16: 标题策略优化：发布阶段强制由写作角色基于正文起名；标题生成失败改为停机告警，不再回退外部模板标题。
- 2026-04-16: 标题规范优化：保留口语表达，弱化截断与固定后缀；去重后缀改为自然续写样式。
- 2026-04-16: 规则口径统一：reviewer字数校验改为去空白字符计数，与发布验收口径一致。
- 2026-04-16: 生成脚本改造：run脚本章节标题统一传AUTO_TITLE，命名完全交由写作角色。
- 2026-04-20: 策划/写作/检查链路强化：新增“更有趣、少重复、明显换地图、避免打圈”硬约束；并刷新reborn_coder_120 recap_state用于续写。
- 2026-04-20: 修复writer重试回灌实现中的f-string语法问题，现已通过py_compile。
