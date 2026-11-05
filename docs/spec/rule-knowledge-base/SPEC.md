# 市场规则知识库与规则变更口径（V1.0）

> 上游票：地图「湖北省售电公司交易员训战一体化平台（按方案 V1.0）」子票「规格：市场规则知识库与规则变更口径」（#54）。
> 依赖：统一数据契约（#33，D01 规则文件与 D01 条款子表）、规则有效期研究（#53，`docs/research/rule-validity-check.md`）、统一时间栅格（#26）。
> 执行依据：本目录 `rule-kb.schema.json` 是唯一可执行口径；`reference_rulekb.py` 是参考实现与自检；`samples.json` 是 8 份真实/合成文件的样例；`verification.txt` 是 E1–E8 的实测输出。

## 1. 问题与边界

方案 M01「湖北市场规则中心」要回答三件事：规则原文在哪里、哪一条还在生效、改了以后影响谁。本规格把它们拆成三张互相约束的表：文件（doc）、条款（clause）、差异与提醒（diff/alert），外加问答留痕（qa_log）。

边界：本规格**只定结构与口径**，不建设全文检索引擎、不引入向量库、不做 PDF 版面解析。规则包由客户提供（Q01），缺口显式标注，不靠推断补条款。

## 2. 第一性原理

1. **规则是带有效期的文本，不是一篇文档。** 同一文号会有多个版本，同一句话在不同时点效力不同；因此身份必须落到「版本」而不是「文件名」。
2. **可追溯 = 每个结论都能回到一段原文。** 模型只能引用，不能改写；引用必须逐字、可校验、带坐标。
3. **变更是可计算的集合差。** 新增/修改/废止不是人工判断，而是新旧两版条款集合按坐标与文本做匹配后的结果。
4. **不知道的要显式空着。** 文号未查到、原文未录入、生效日待确认，都必须成为可查询的缺口，而不是填一个像样的默认值。
5. **规则变化的风险在遗漏，不在误报。** 到期、生效、被替代必须提前提醒；同一件事只提醒一次，但 CRITICAL 在确认前不能消失。

## 3. 公理

- **R-A1 版本不可变**：一个 `doc_id` 对应一个版本；任何字段变化产生新行，旧行永久保留。
- **R-A2 状态由时间计算**：库里存的 `status` 只是最近一次核验值；对外一律用 `resolve_status(doc, as_of)`。
- **R-A3 引用闭包**：一条引用只能指向 `text_status=VERBATIM` 的条款；待补条款可以被检索到「存在」，但不能被引用为答案。
- **R-A4 坐标唯一**：`clause_uid = doc_id#clause_key`；`clause_key` 在文件内唯一，跨文件不要求一致。
- **R-A5 文本先规范再比较**：一切哈希、相似度、逐字校验都基于 `text_norm`（NFKC、去零宽、空白折叠、去首尾空白）。
- **R-A6 提醒幂等**：同一 (trigger, doc, window, 稳定载荷) 只产生一条提醒。

## 4. 规范

### V1 文件身份与版本链

- `doc_id` 格式沿用 #33：`RULE-<机构码4>-<年4>-<序号3>`，如 `RULE-4800-2026-001`。
- 同一文号的新版本用新 `doc_id`，通过 `supersedes_doc_id` 指向前一版；`repeals` 记录被整份废止的其他文件。
- `doc_type` 四类：NATIONAL / REGIONAL / PROVINCIAL / COMPANY；来源与适用层级由它决定，不由文件名猜。

### V2 状态是算出来的

`resolve_status` 的优先级：DRAFT（as_of < effective_from）→ SUPERSEDED（`superseded_by` 已生效）→ EXPIRED（as_of > effective_to）→ ACTIVE。

- `effective_to` 是**最后有效日（含当日）**；2026-11-10 到期，则 11-10 仍 ACTIVE，11-11 起 EXPIRED。
- 未设 `effective_to` 不等于永久有效，只表示「未核验到终点」，必须在 `notes` 写明。
- 所有对外查询（问答、差异、提醒）都带 `as_of`，禁止用「今天」隐式替代。

### V3 证据三态与缺口

- `text_status`：VERBATIM（原文已录入，必须有 `page_ref`）/ PENDING_TRANSCRIPTION（只有条款号与主题，正文空串）/ OMITTED（已知存在但未取得）。
- `evidence_status`：VERIFIED / PARTIAL / UNVERIFIED；`doc_no` 为空时强制 PARTIAL，并以固定文案写明「文号未查到/待补，待客户确认（Q01）」。
- `evidence_gaps()` 输出所有缺口（文号、页锚、待补条款、来源 URL），供规则中心首页与客户澄清清单直接消费。

### V4 正文规范化与哈希

`text_norm = NFKC(text_raw)` → 删除零宽字符 → 连续空白折叠为单空格 → 去首尾空白；`text_hash = sha256(text_norm)`。逐字校验、跨版本匹配、引用完整性都用这一套，不做同义词替换。

### V5 条款坐标

- 坐标三元组：`chapter_path`（如「第2章 日前市场」）、`clause_no`（原文编号，如 `2.7.1.1`）、`clause_key`（文件内唯一键，默认等于 `clause_no`）。
- 改序号时 `clause_key` 会变，但 `text_hash` 不变；这正是 V8 识别 RENUMBERED 的依据。
- 附件、附表单独建条款行，`clause_no` 用「附件6」这类原文写法，不改写成数字。

### V6 结构化索引

每条条款至少打三个标签：`clause_topic`（20 个主题之一）、`applicable_subject`（6 类主体）、`impact_area`（5 个业务域）。标签是检索与影响分析的入口，不是全文摘要；无法判定时填 OTHER 并在 `notes` 说明。

### V7 可追溯问答

- 返回五要素：文件名（title）、条款号（clause_no）、版本（version_label）、生效日期（effective_from）、原文片段（quote）。
- `quote` 必须逐字出现在 `clause.text_raw` 中；校验失败即整条作废（E6 的篡改用例）。
- 无匹配返回 `INSUFFICIENT_EVIDENCE`，不得给出条款号或文号；问题超出规则库主题返回 `OUT_OF_SCOPE`。
- 命中待补/草案/已失效条款时不作 ANSWERED：待补降 PARTIAL 并提示缺口，草案与失效只在历史模式返回。
- 模型输出只能是「引用拼接」或明确标注的「摘要」，两者分栏展示；摘要不得冒充原文。

### V8 版本对比

匹配顺序（与 `diff_contract.match_order` 一致）：

1. 同键同文 → 未变化；
2. 跨键同文 → MODIFIED/RENUMBERED（改序号、搬章节）；人工等价表 `d01_rule_clause_equiv` 优先；
3. 同键但文本不同 → 相似度 ≥0.45 判 MODIFIED/TEXT_EDIT，否则不配对；
4. 剩余旧条款 → REPEALED（整份废止时 reason=DOC_REPEALED，否则 OMITTED）；剩余新条款 → ADDED。

条款号变化本身也算变更：即使正文一字未动，下游引用必须跟着改。每条变更加 `impact_area`，按 `impact_map` 落到交易、结算、零售、信用、披露五个业务域。

### V9 变更提醒

- 到期提醒 EXPIRING_SOON、生效提醒 EFFECTIVE_SOON：T30（8–30 天，WARN/INFO）、T7（2–7 天，CRITICAL/WARN）、T1（0–1 天，CRITICAL/WARN）。
- 新文件生效 NEW_ACTIVE_DOC（INFO）、状态变化 STATUS_CHANGED（WARN）、差异影响 DIFF_IMPACT（WARN）。
- 去重指纹不含 `days_left`：同一天重跑不得重复投递；CRITICAL 在人工确认前不得静默消失。
- 落点：规则中心收件箱 + 交易主管/风控/结算三类角色；不直接推送到交易终端。

### V10 规则包缺口（Q01）

客户规则包未到时：允许建「骨架行」（doc_no 空、text_status=PENDING_TRANSCRIPTION），但必须 `evidence_status=PARTIAL`、写明 Q01，且不得进入任何引用。缺口清单由 `evidence_gaps()` 统一产出，作为阶段一第 1 周的客户澄清输入。

### V11 只读边界

知识库是只读参考系：不写交易、不写结算、不触发申报。检索与问答只读 `d01_rule_*`；任何「按规则自动改参数」的需求一律拒绝，改为输出「影响提示 + 人工确认」。

### V12 与既有规格的衔接

- 时间：所有日期按 #26 的 Asia/Shanghai 自然日；as-of 查询用 `trade_date` 语义，不用时间戳。
- 契约：#33 的 D01 两表原样保留；本规格新增 `d01_rule_clause_equiv`、`d01_rule_diff`、`d01_rule_alert`、`d01_rule_qa_log`、`d01_rule_index` 五张辅助表，字段扩展不破坏原 D01。
- 权限：规则库读权限对六角色开放；写权限仅系统管理员；提醒确认动作归交易主管与风控（见认证与权限矩阵规格）。

## 5. 数据表

| 表 | 作用 | 关键约束 |
| --- | --- | --- |
| d01_rule_doc | 文件版本 | doc_id 唯一；supersedes 无环；status 为核验值 |
| d01_rule_clause | 条款 | clause_uid 唯一；VERBATIM 必须有 text 与 page_ref |
| d01_rule_clause_equiv | 人工等价 | 用于改序号/合并拆分的人工确认 |
| d01_rule_diff | 差异结果 | change_type 三值；reason 七值；带 impact_area |
| d01_rule_alert | 提醒 | fingerprint 唯一；带 window 与 severity |
| d01_rule_qa_log | 问答留痕 | 记录问题、答案、引用、as_of、拒答原因 |
| d01_rule_index | 检索索引 | clause_uid + topic + subject + 关键词，可重建 |

## 6. 验收对照（#54 四条）

| 票面验收 | 落点 | 证据 |
| --- | --- | --- |
| 规则元数据与条款索引写成可执行数据字典 | `rule-kb.schema.json` 7 表 + 枚举 + 12 条不变量 | E1：8 文件 / 16 条款校验无错 |
| 任取一条规则问答，返回文件、条款号、版本、生效日期、原文片段五要素 | `answer_question` + `validate_answer` | E2 往返一致；E6 逐字校验与篡改拦截 |
| 版本对比能区分新增/修改/废止三类 | `diff_docs` | E4：added=1 / modified=2 / repealed=1；E5 识别改序号 |
| 客户规则包缺口标注「待客户确认」，不凭推断补写条款 | `evidence_gaps` + PENDING 条款不可引用 | E8：文号缺口命中，待补条款被引用数=0 |

补充实测：E3 as-of 状态（11-10 仍 ACTIVE、11-11 EXPIRED）；E7 提醒窗口与同日去重（T30 首跑有、同日重跑 0 条、T7/T1 均 CRITICAL）。

## 7. 下游接口

- **规则问答**：`answer_question(library, question, as_of) -> {answer_status, citations[], gaps[]}`；调用方只渲染，不二次加工引文。
- **版本对比**：`diff_docs(library, old_doc_id, new_doc_id) -> {summary, changes[], clause_map}`；变更项带 `impact_area`，供结算/零售/风控票判断是否重算。
- **提醒流**：`build_alerts(library, as_of, existing=fingerprints) -> alerts[]`；调用方按 fingerprint 落库，不自行去重。
- **缺口清单**：`evidence_gaps(library) -> gaps[]`；作为 Q01 客户澄清与规则中心首页的数据源。

## 8. 尚未明确（fog）

- 规则包实际交付格式（PDF 扫描件/Word/网页）未定，影响 `page_ref` 的粒度与 OCR 是否必需；等 Q01 样本。
- 同一文号在「修订征求意见稿 → 正式稿」之间的过渡期如何展示（并列还是覆盖），需要客户确认阅读习惯。
- 规则条款与交易/结算参数的映射粒度（条款级还是条款内某一段），要等结算复现票落地后回看。
- 提醒的确认动作是否需要留痕到审计链（当前只记 qa_log 与 alert 状态），待认证与审计口径定稿。
- 湖北省内各地市细则是否单独建 doc（doc_type=PROVINCIAL 之下再分层），等规则包实际层级。

## 9. 变更记录

- 2026-11-05 v1.0：首版。7 表 schema、V1–V12、问答/差异/提醒三个契约、E1–E8 自检全通过。合成样例对 RULE-9000-2025-001 / RULE-9000-2026-001 仅用于验证差异算法，`is_synthetic=true`，不得进入生产库。
