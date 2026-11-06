# 四态判定、证据锚点与不符合项生成规则（S2 规格）

> 对应 GitHub Issue #9（规格 S2）。上游：#2（规格 S1，审核点库 schema 与标准画像注册表，`audit-point-library/`）。
> 下游消费：#4（S3 流程与状态机）、#10（S4 输出格式）、#12（实现 I2 判定引擎）、#11（实现 I 照片取证链路）、#6（实现 I7 命令封装）。
> 规则依据（一手核验，来源 `docs/research/multi-system-audit-research.md`）：CNCA-QMS-01:2025 / CNCA-EMS-01:2026 / CNCA-OHSMS-01:2026（同构 12 章 + 附录 ABC）。
> 本文件只定义**判定语义与数据结构**，不写数值计算（归 #21）、不做节点编排（归 #4）、不定义文件格式（归 #10）。

---

## 0. 第一性原理

一条审核结论要成立，必须同时回答四个问题：

1. **要求是什么** —— 条款号 + 条款版本 + 该条款的具体要求（requirement）；
2. **适用吗** —— 受审核方的过程、场所、产品是否落入该条款适用范围（applicability）；
3. **事实是什么** —— 观察到什么、可定位到哪（evidence + anchor）；
4. **要求与事实差多少** —— 差距是否已被证实（verdict）。

四个问题里任何一环缺失，结论都不可复现。因此判定**不是一段自由文本**，而是一个纯函数：

```
verdict = judge(clause, requirement, applicability, evidence_bundle, confirmed_anchors, basis_status)
```

输入全部来自结构化记录（锚点表、条款库、依据库状态），输出是四态之一 + 机器可读的原因码 + 锚点引用清单。
同一输入必须得到同一输出 —— 这是本规格唯一不可让步的验收线（见 §13 V12）。

**三条红线**：

- 红线一：**判定不可自由书写**。任何判定语句若不能回指到锚点表内一个 `confirmed` 锚点，判定无效，条款自动落「证据不足」。
- 红线二：**不适用不是垃圾桶**。「未知」「未提供」「未实施」一律不得改标为「不适用」；「不适用」必须有适用性依据 + 依据锚点。
- 红线三：**工具不代作决定**。严重不符合定级、认证决定、撤回/补证要求由有权限的人作出；工具只产出建议与证据清单。

---

## 1. 状态维度总表（防止多套口径并存）

本项目有 5 组状态，**值域互不相同、不可互相映射**，但有明确的映射表（§5、§9）。任何实现里出现第二套同义状态，视为实现缺陷。

| 组 | 名称 | 取值 | 语义 | 谁定 |
|---|---|---|---|---|
| A | 判定四态 | `conform` 符合 / `nonconform` 不符合 / `insufficient` 证据不足 / `na` 不适用 | 本条款的审核结论 | 工具产出建议，人复核 |
| B | 事实标记四态 | `user_stated` 用户陈述 / `material_supported` 材料支持 / `verified` 已核验 / `conflict` 存在冲突 | 单个事实的证据强度（与 A 正交） | 工具判定，人确认 |
| C | 辅助判断 | A 的四态 ∪ `pending` | 审核员在流程中间态给出的暂定结论 | 人 |
| D | 成果态 | `waiting` 等待版 / `review` 复核版 / `final` 完成版 / `terminated` 终止版 | 文件产物的成熟度（归 #10） | 工具 + 人 |
| E | 问题七分类 | `missing` 缺件 / `unreadable` 不可读 / `pending_check` 待核 / `conflict` 冲突 / `suspected_nc` 疑似不符合 / `formal_nc` 正式不符合 / `suggestion` 建议 | 一条待处理事项的性质 | 工具分类，人处置 |

> 强制：**不得**把 D（成果态）、E（问题分类）当作判定；判定只从 A 出。

---

## 2. 证据八要素与锚点

### 2.1 证据八要素

每条审核点的记录必须能填满这 8 个要素；缺项即为「证据不足」，不进入判定推理：

| # | 要素 | 字段 | 填写要求 |
|---|---|---|---|
| 1 | 具体要求及版本 | `requirement.text` / `requirement.source_ref` | 必须带标准号+版本+条款号；source_ref 指向依据快照（§7） |
| 2 | 适用理由 | `applicability.reason` | 为什么这条要求对该过程/场所适用 |
| 3 | 企业过程或场所 | `applicability.process` / `applicability.site` | 受审核方的过程、场所标识（多场所须标明抽样范围） |
| 4 | 材料版本及定位 | `evidence.fingerprint` / `evidence.locator` | 文件哈希或 size+mtime；页/段/表行/单元格/时间戳定位 |
| 5 | 观察事实 | `evidence.observed` | 只写看到了什么，不写推断了什么 |
| 6 | 辅助判断 | `aux_judgement` | 审核员的暂定看法，须与观察事实分离 |
| 7 | 实际确认 | `confirmation` | 谁、何时、用什么方式确认（现场反查/第二来源/签署人） |
| 8 | 整改验证 | `remediation` | 仅不符合项需要：原因分析、纠正措施、有效性验证记录（§10） |

### 2.2 锚点（anchor）

锚点是**证据定位单元**，是报告与判定之间唯一的连接件。

```yaml
anchor:
  anchor_id: A-0001              # 全局唯一，形如 A-<4位序号>
  type: file | image             # file_locator_kinds / image_region_kinds 见 shared/rules.yaml
  source_ref: <依据/材料标识>     # 文件路径或依据快照 id
  locator:                      # file: {kind: page|section|table_row|cell|paragraph|timestamp, value}
                               # image: {kind: bbox|region_desc|tag, value}
  fingerprint: {hash: <sha256>} # 或 {size, mtime}
  fact_state: user_stated|material_supported|verified|conflict
  confirmation: pending|confirmed|rejected
  confirmed_by: <审核员标识>
  confirmed_at: <时间戳>
  visible_state_only: true      # image 锚点必须为 true
```

**锚点硬规则**（与 `shared/rules.yaml` 的 `anchor_rules.hard_rules` 一一对应）：

1. 报告正文只允许引用 `confirmation: confirmed` 的锚点。
2. 锚点缺失 → 判定自动落 `insufficient`，工具不得自动补齐。
3. `file` 锚点必须带指纹（hash 或 size+mtime），用于事后验证材料未被替换。
4. `image` 锚点必须声明 `visible_state_only: true`；不得据照片推断时间序列、访谈内容或文件完整性。
5. 锚点必须指向**具体位置**，禁止只写文件名或整页引用（整页引用须写明所引段落范围）。

### 2.3 锚点 ID 与引用

- `anchor_id` 全局单调递增，不复用、不回收；删除的锚点保留编号空位。
- 引用写法固定为 `[A-0007]`；正文任何一处引用都能在锚点表里查到同号行（#10 的验收线）。
- 锚点表是**唯一事实源**：报告正文、对照表、审查表都由锚点表投影生成，不允许先写正文再补锚点。

---

## 3. 判定四态

### 3.1 定义与前置条件

| 判定 | 含义 | 前置条件（全部满足） | 禁止情形 |
|---|---|---|---|
| `conform` 符合 | 要求已实现且被证据支持 | ① 适用性已确认；② 必核证据项均有 `confirmed` 锚点；③ 无 `conflict` 事实；④ 事实标记至少一条为 `verified` 或 `material_supported` 且覆盖必核项 | 有 `conflict` 事实未解 |
| `nonconform` 不符合 | 要求未被满足，差距已被证实 | ① 适用性已确认；② 至少 1 个 `confirmed` 锚点支撑差距事实；③ 有条款号；④ 有事实陈述；⑤ 有建议类别（最终定级由人作出） | 仅有 `user_stated` 事实支撑 |
| `insufficient` 证据不足 | 无法给出符合/不符合的结论 | 下列任一：必核证据项缺锚点 / 锚点未 `confirmed` / 存在未解 `conflict` / 依据不可核验（§7）/ 适用性未确认 | 反向禁止：不得用它掩盖「已确认差距」 |
| `na` 不适用 | 该条款对本次受审核范围不适用 | ① 有适用性依据说明（为什么该过程/场所不落入范围）；② 有支撑该依据的 `confirmed` 锚点；③ 有具名责任人确认 | 「未知」「未提供」「未实施」 |

### 3.2 判定表达式与四态映射

判定记录必须携带一个**判定表达式**（机器可解析），由「事实标记 + 适用性 + 依据状态」三部分构成：

```
expression := applicability_expr && basis_expr && fact_expr
applicability_expr := applicable | not_applicable | unknown
basis_expr          := basis_current | basis_unverifiable | basis_missing
fact_expr           := verified_ok | gap_confirmed | pending | conflicting
```

映射表（**唯一映射，不得另立**）：

| 适用性 | 依据 | 事实 | → 判定 |
|---|---|---|---|
| applicable | basis_current | verified_ok | `conform` |
| applicable | basis_current | gap_confirmed | `nonconform` |
| applicable | basis_current | pending | `insufficient` |
| applicable | basis_current | conflicting | `insufficient`（保留冲突子标记与两侧证据） |
| applicable | basis_unverifiable / basis_missing | 任意 | `insufficient`（不得凭标准文本补证） |
| unknown | 任意 | 任意 | `insufficient` |
| not_applicable | basis_current | 任意（须有依据锚点） | `na` |
| not_applicable | basis_unverifiable / basis_missing | 任意 | `insufficient` |

> 「符合且证据支持 → 符合；已确认差距 / 不满足 → 不符合；待核 → 证据不足；冲突 → 证据不足；不适用且已确认 → 不适用」即上表前三行，展开为可执行形式。

### 3.3 判定算法（确定性步骤）

```
judge(record):
  1. 若 record.requirement.source_ref 缺失            → insufficient(reason=BASIS_MISSING)
  2. 若 依据库对该 source_ref 判定不可核验(#20 退出码 3) → insufficient(reason=BASIS_UNVERIFIABLE)
  3. 若 record.applicability 未填                      → insufficient(reason=APPLICABILITY_UNKNOWN)
  4. 若 record.applicability == not_applicable:
       若 有依据锚点且已 confirmed                        → na
       否则                                            → insufficient(reason=NA_WITHOUT_BASIS)
  5. 取本条款「必核证据项」集合 = evidence_items[priority=core] ∪ 条款的 written_evidence/on_site_evidence
  6. 若 必核项存在 无锚点 / 锚点 confirmation != confirmed → insufficient(reason=EVIDENCE_MISSING)
  7. 若 存在 fact_state == conflict 且未标记 resolved     → insufficient(reason=EVIDENCE_CONFLICT)
  8. 若 存在 confirmed 锚点支撑的差距事实（与 requirement 逐条比对不满足）
       且 该事实的 fact_state ∈ {material_supported, verified} → nonconform(reason=GAP_CONFIRMED)
  9. 否则（全部必核项已被覆盖且无差距）                    → conform
```

- 步骤 8 的「差距事实」不是模型自由文本，而是**审核点条目与锚点的比对结果**；比对项来自审核点库（`audit_question` / `written_evidence` / `on_site_evidence` / `typical_nc`），由 #2 的库提供。
- 步骤 9 的「无差距」意味着：每个必核证据项都有 confirmed 锚点，且锚点内容与该条要求逐项吻合。**无法逐项吻合时不得判符合**，落回步骤 6/8。
- 算法输出不含自由文本断言；人读描述由模板拼装（§3.4），模板缺字段则输出「描述不可生成」而非自由补写。

### 3.4 判定记录（机器可读）

```yaml
judgement:
  clause_no: "8.5.2"
  point_key: "8.5.2#1"
  expression: "applicable && basis_current && verified_ok"
  verdict: conform
  reason_codes: []
  anchors: [A-0011, A-0012]
  applicability:
    state: applicable
    reason: "标识与可追溯性要求覆盖该注塑工序的原料批次追溯"
    process: "P-注塑-01"
    site: "S-华东-02"
    basis_anchor: A-0013
  fact_states: [verified, material_supported]
  conflicts: []
  basis:
    source_ref: "BASIS-CNCA-QMS-01-2025#5.10.1"
    status: current
    snapshot_sha256: "<sha256>"
  sampling: {population: "<总体说明>", method: "<抽样方法>", limitation: "<覆盖限制>"}
  decided_by: tool
  reviewed_by: "<审核员标识>"
  note: ""
```

- `decided_by: tool` 表示这是工具产出**建议**；`reviewed_by` 有值才表示人已复核，复核后 `verdict` 可改，但必须留改动轨迹（§13 V14）。
- `reason_codes` 取值见 §13；正文描述由模板拼装，不允许自由文本。

### 3.5 迁移规则

- `insufficient` → `conform`/`nonconform`：补齐锚点并确认后重算，**不得**改标为 `na`。
- `conform` → `nonconform`：允许，但必须说明是「复核发现差距」，并保留原判定轨迹。
- 任意 → `na`：**禁止**，除非当初就不适用且已被确认（§3.2 第四行）。
- 已开正式不符合项的条款不得直接改判 `conform`：须先关闭不符合项并记录整改验证（§10.4）。

---

## 4. 问题七分类（E 组）与处置

E 组不是判定，是**待处理事项的性质**。一条事项先分类，再决定它是否影响判定。

| 分类 | 判定依据（唯一） | 是否影响 A 组判定 | 处置动作 | 关闭条件 |
|---|---|---|---|---|
| `missing` 缺件 | 应索取的证据项未提交任何材料 | 是 → 落 `insufficient` | 记入缺件清单，向委托人发出书面补件请求 | 材料提交并完成核验 |
| `unreadable` 不可读 | 材料已提交但无法辨认（模糊、不可打开、加密、扫描失真） | 是 → 落 `insufficient` | 退回并要求提供可读副本 | 可读副本提交并完成核验 |
| `pending_check` 待核 | 材料已提交、尚未完成核对 | 是 → 落 `insufficient` | 进入待核队列，保留截止时间 | 核验完成，产出 `fact_state` |
| `conflict` 冲突 | 两个及以上来源对同一事实的陈述互斥 | 是 → 落 `insufficient`（保留冲突子标记与两侧证据） | 由审核员现场反查或第二来源澄清 | 冲突消解，或双方均不可核验（此时仍落 `insufficient`） |
| `suspected_nc` 疑似不符合 | 观察到与要求不符的迹象，但未完成确认 | **否**（不产生正式判定） | 转审核员复核，复核确认后方可转 `formal_nc` | 复核确认（转正式）或 排除（回 `pending_check`/`missing`） |
| `formal_nc` 正式不符合 | 已按 §8 模板由人确认并开具 | 是 → 该条款落 `nonconform` | 进入不符合项处理流程（§10） | 纠正措施有效性验证通过 |
| `suggestion` 建议 | 无明确不符合，但有改进空间 | 否 | 写入报告「改进机会」栏 | 无需关闭，人工标记已阅 |

**判定与分类的顺序**（不可颠倒）：

```
1. 先对每个证据项产生 E 类事项（若有）
2. 再按 §3.3 judge() 计算 A 组判定
3. E 类事项与 A 组判定的绑定关系：missing/unreadable/pending_check/conflict → insufficient；
   suspected_nc → 不改变判定，只在报告中提示；formal_nc → nonconform（并写明锚点）
4. 同一条款可同时存在多条 E 类事项（不限于一条）
```

> 反向禁止：`suggestion` 不得被写成 `formal_nc`；`suspected_nc` 不得直接进报告「不符合项」栏；`missing` 不得被改判为「不适用」。

---

## 5. 辅助判断（C 组）与判定四态（A 组）的映射

辅助判断是**人在流程中间态**给出的暂定结论，用于推进工作，不构成最终判定。

| C 组取值 | 允许携带的 A 组终值 | 允许携带的 B 组事实标记 | 是否可写入报告正文 | 流转要求 |
|---|---|---|---|---|
| `pending` | 无（未产生判定） | 任意 | 否 | 必须补齐锚点或适用性后重算 |
| `insufficient` | `insufficient` | 任意 | 是（作为「证据不足」说明） | 补齐后重算并保留原轨迹 |
| `conform` | `conform` | 至少一条 `material_supported` 或 `verified` | 是 | 复核人签署后升为终值 |
| `nonconform` | `nonconform` | 至少一条 `material_supported` 或 `verified` | 是 | 按 §8 模板由人确认后方可开具 |
| `na` | `na` | 任意（须有依据锚点） | 是 | 须有具名责任人确认 |

**硬规则**：

1. C 组取值只能单向流向同名的 A 组取值；`pending` 不得直接跳到 `conform`/`nonconform`（必须先有终值判定产生）。
2. C 组与 A 组取值**不一致**时（除 `pending` 外），以 A 组为准，C 组记为「已被取代」，不得回写。
3. C 组不得包含 A 组没有的取值；出现第五种取值即实现缺陷（与 §1「值域互不相同」互为约束）。
4. C 组状态写入判定记录时必须带 `decided_by: human` 与 `decided_at`，否则视为工具输出并被拒绝。

---

## 6. 抽样与覆盖规则

抽样是审核结论成立性的前提条件之一；**抽样信息缺失的判定一律落 `insufficient`**。

### 6.1 抽样记录必填字段

```yaml
sampling:
  population: "<总体说明：范围/场所/产品服务/过程清单及数量>"
  method: "<抽样方法：依据什么选出来、如何选的>"
  limitation: "<覆盖限制：本次未覆盖的部分及其风险影响>"
  risk_basis: "<抽样的风险依据：依据哪次风险评价得出>"
  selected: [<被抽中项 id>...]
  excluded: [<明确未抽中项 id>...]
```

> 「抽样基于与委托人活动或过程性质相关的本体系风险评价」（CNCA 5.4.3.2）——`risk_basis` 缺此项即抽样无效。

### 6.2 多场所抽样数量（CNCA 5.4.3.3 / 5.4.3.4）

| 审核类型 | 相似场所抽样数 Y | 依据 |
|---|---|---|
| 初次认证 | Y = X（全部场所） | 5.4.3.3 |
| 监督审核 | Y = 0.6 X（向上取整） | 5.4.3.3 |
| 再认证审核 | Y = 0.8 X（向上取整） | 5.4.3.3 |

- **非相似场所不抽样**：初次与再认证应逐一在每个场所实施审核；监督审核应抽取不少于 30% 的场所，且每次须包括中心职能部门（5.4.3.4）。
- 第二次监督审核选取的场所通常不同于第一次（5.4.3.4）。
- 现场审核时间不得少于所确定现场审核时间的 50%（5.4.3.5）。

### 6.3 判定与抽样的耦合

- 抽样数不足 → 结论不成立：该场所的判定落 `insufficient`（原因码 `SAMPLING_INSUFFICIENT`），并在报告中明确写入覆盖限制。
- 抽样充分但覆盖限制已在报告中声明 → 判定有效，声明义务在报告侧（§11 第 18 项）。
- 抽样的总体、方法、限制三者缺一，`sampling` 视为未填，判定落 `insufficient`。

---

## 7. 依据库、依据快照与可核验性

「要求是什么」的答案不在模型里，在**依据库**里。

### 7.1 依据快照

每条判定引用的是某个依据快照，而不是「现行标准」的模糊概念。快照字段：

```yaml
basis_snapshot:
  source_ref: "BASIS-CNCA-QMS-01-2025#5.10.1"
  source_id: CNCA-QMS-01:2025
  version: "2025"
  locator: "5.10.1"
  snapshot_sha256: "<sha256>"
  retrieved_at: "<时间戳>"
  status: current | superseded | unverifiable
```

### 7.2 三态语义

| 状态 | 含义 | 对判定的影响 |
|---|---|---|
| `current` | 快照可核验且对本次审核适用 | 正常参与判定 |
| `superseded` | 快照存在但已被更新版本取代 | **不得**用它下结论；须换用新版本快照，否则落 `insufficient` |
| `unverifiable` | 依据库无法核验该依据（文件缺失、哈希不符、版本未知） | 直接落 `insufficient`（原因码 `BASIS_UNVERIFIABLE`） |

### 7.3 与 #20 依据库的联动

- 判定前调用依据库核验；**核验返回退出码 3（无法判定）时**，该条款只能落 `insufficient`，且不得凭标准文本或模型记忆补证（§0 红线一）。
- 依据库核验通过（退出码 0/1/2）时，把快照的 `snapshot_sha256` 写进判定记录，用于事后复核「当时依据的是哪一版」。
- 依据引用必须精确到条款号；引用整份文件（如「依据 CNCA-QMS-01:2025」）不构成 `source_ref`，视为依据缺失。

### 7.4 条款要求与审核点库的绑定

审核点库（`audit-point-library/`）提供**条款要求条目**与**审核问法/证据项**；本规格提供**判定与引用语义**。两者通过 `point_key` 绑定：

- `point_key = "<clause_no>#<序号>`，序号与审核点库中该条款下的条目顺序一致。
- 判定记录里的 `clause_no` 必须能在对应标准画像的条款树中找到；找不到 → 落 `insufficient`（原因码 `CLAUSE_NOT_IN_TREE`），不得凭模型知识补条款。
- 条款树的版本与审核点库的 `verification_status` 参与判定：`unverified`（中文条款标题未经正版核定）时，中文条款标题只能作定位参考，判定依据一律引 ISO/国标条款号原文。

---

## 8. 不符合项的生成

### 8.1 生成方式

不符合项**不由模型直接写出**。生成链路固定为：

```
锚点表 → 差距比对结果 → 事实陈述（只含观察到的事实）→ 模板拼装 → 人工确认 → 开具
```

### 8.2 不符合项记录模板

```yaml
nonconformity:
  id: NC-2026-0007
  clause_no: "8.5.2"
  clause_text_ref: "<条款原文引用，指向依据快照>"
  category: null            # 严重 | 轻微 —— 必须由人填写，工具不得预填
  fact_statement: "<观察事实，只写看到了什么；不得含推断了什么>"
  anchors: [A-0021, A-0022] # 支撑事实的锚点，缺一不可
  judgement_basis: "<判定依据：要求条款 + 与要求的差距描述>"
  remediation_required: "<纠正与纠正措施要求：原因分析 + 纠正措施>"
  due_date: null           # 由人按 §10.3 规则填
  status: open | submitted | verified | closed
  confirmed_by: null
  confirmed_at: null
```

### 8.3 字段规则

1. `category` 工具**不得自动预填**，也不得给出「建议：严重」字样；分级由有权限的人依据 CNCA 第 12 章术语作出（严重不符合 = 影响体系实现预期结果的能力；轻微不符合 = 不影响该能力）。
2. `fact_statement` 中每一条陈述都必须能被某个 `anchors` 中的锚点直接支撑；无法被锚点支撑的句子必须删除。
3. `fact_statement` 不得出现「意识淡薄」「管理不到位」「投入不足」等主观评价；只写现象。
4. `anchors` 为空 → 不符合项不予生成，退回 `insufficient`。
5. `clause_text_ref` 必须指向依据快照的精确条款，不得只引标准名称。

### 8.4 轻微不符合与严重不符合的处理差异（CNCA 5.10）

- 轻微不符合：可制定纠正措施计划，由认证机构在**下次审核时**验证（5.10.2）。
- 严重不符合：须在规定时限内完成原因分析与纠正措施并由认证机构验证有效性（5.10.1/5.10.2），时限见 §10.3。
- 未在规定时限内完成采取措施的，**不应作出授予、保持或更新认证的决定**（5.10.5）——该结论由认证决定环节使用，工具只提供时限与状态。

---

## 9. 流程节点与判定要求映射

不同审核类型对证据强度与判定粒度的要求不同；同一份材料在初鉴一阶段与二阶段的可下结论范围不同。

| 节点 | 审核类型 | 允许的判定粒度 | 强制证据 | 不得出现 |
|---|---|---|---|---|
| N1 | 资格审核 / 申请评审 | 仅对「是否具备接受现场审核的条件」作结论 | 体系文件与实际活动相吻合的证明；内审与管理评审已实施的证据 | 对过程有效性下符合性判定 |
| N2 | 第一阶段（文件与现场） | 可对文件层面的符合性下判定；过程有效性**不下结论** | 文件与现场一致、体系运行时间、内审与管理评审有效性、范围与重要审核点识别 | 「运行有效」「持续满足要求」类结论 |
| N3 | 第二阶段（现场实施） | 全量条款判定，含有效性与符合性 | 关键过程的实际控制证据、绩效监视与目标达成记录 | 以一阶段文件结论代替现场证据 |
| N4 | 监督审核 | 全量条款判定，但重点覆盖上次结论与变化 | 上次不符合项整改验证、体系重大变更、绩效趋势 | 免于核验历史不符合项 |
| N5 | 再认证审核 | 全量条款判定 | 全周期符合性证据、有效性与持续改进 | 仅抽样复核上次结论 |
| N6 | 特殊审核（投诉/扩范围/追踪） | 只对本次范围下结论 | 与投诉或扩范围事项直接相关的证据 | 顺带给出全体系符合性结论 |

**关键约束**：

- 初次认证两阶段的间隔最短不少于 5 日、最长不超过 6 个月；需要更长间隔的应重新实施第一阶段（CNCA 5.6.1）——间隔超期时第一阶段结论作废，N2 须重做。
- 第二阶段须在现场实施（5.6.3.2）；一阶段的文件审核结论不得作为二阶段的证据替代品。
- 监督审核应要求获证组织**最高管理者参与审核访谈**（5.7.1）——该访谈未发生，属 N4 节点未完成，而非证据不足。
- 监督审核时间不少于附录 B 初鉴审核时间的 1/3（5.7.4）；再认证不少于 2/3（5.8.4）；再认证须在证书到期前完成且在现场进行（5.8.2）。
- 现场审核时间不得少于所确定审核时间的 80%（5.4.2.3）。
- 至少在现场审核实施前 3 日将审核计划上网或上报（9.2）——属流程合规项，由 #4 状态机管控，不进入条款判定。

---

## 10. 整改与有效性验证

### 10.1 状态流转

```
open（已开具，委托人待整改）
  → submitted（委托人提交原因分析与纠正措施）
    → verified（认证机构验证有效性：符合）
      → closed
    → rejected（验证不通过，退回整改，保留两次记录）
```

- `open → submitted` 需委托人提交：原因分析、纠正措施、责任人与完成日期。
- `submitted → verified` 必须由验证人记录**验证方式**（现场复核 / 记录复核 / 抽样复核 / 第二来源）与**验证证据锚点**；无锚点不得置 `verified`。
- `verified → closed` 只在验证完成后由人执行。

### 10.2 轻微不符合

轻微不符合可制定纠正措施计划，由认证机构在**下次审核时**验证（CNCA 5.10.2）。此时：
- 状态保持 `submitted` 但标记 `deferred_verification: next_audit`；
- **不得**计入本节点的严重不符合时限计算；
- 在下次审核（N4/N5）的抽样计划中必须列入该条款。

### 10.3 严重不符合的验证时限（CNCA 5.10.3 QMS / 5.10.4 EMS、OHSMS）

| 审核类型 | 时限起算 | 完成时限 |
|---|---|---|
| 初次认证 | 第二阶段审核结束之日 | 6 个月内 |
| 监督审核 | 审核结束之日 | 3 个月内 |
| 再认证 | 原认证证书到期前 | 到期前完成 |

- 到期未完成 → 该项标 `overdue`；认证决定环节据此**不应作出授予、保持或更新认证的决定**（5.10.5）。该结论由人作出，工具只输出状态与时限。
- 未有效验证纠正与纠正措施的，不应予以再认证，也不应延长原证书有效期（5.12.4）。

### 10.4 关闭与改判的关系

- 不符合项未 `closed` 前，该条款不得改判 `conform`（与 §3.5 一致）。
- 不符合项 `closed` 后，条款重新计算判定；此时 `insufficient` 是合法结果（整改尚未反映到证据中），但**不得**直接沿用关闭前的 `conform`。
- `rejected` 的验证记录必须保留，不得覆盖：同一不符合项可有任意多条验证记录（`verification_history`）。

### 10.5 纠正措施有效性的判定标准

有效性验证的判定仍走 §3.3 的 `judge()`，只不过被判定对象是「纠正措施是否消除了根因并防止再发」：
- 根因分析是否指向体系原因（而非「培训不足」这类表面原因）→ 不充分即 `insufficient`；
- 纠正措施是否有实施证据（锚点）→ 无锚点即 `insufficient`；
- 是否有证据表明同类问题未在其他过程复现 → 无证据即 `insufficient`；
- 全部满足且证据互相印证 → `verified`。

> 该判定结论是**建议**，最终验证与关闭由有权限的人作出。

---

## 11. 审核报告内容要求

### 11.1 交付要求（CNCA 5.11.1、5.11.2、5.11.4、5.11.5）

- 每次审核应向委托人提供**书面**审核报告；**审核组长**对报告内容负责。
- 报告内容应准确、简明、清晰，反映体系真实状况，描述对照标准的符合性与有效性的客观证据信息，以及对认证结论的推荐意见。
- 认证机构应**保留用于证实审核报告中相关信息的审核证据**（5.11.4）——即锚点表须与报告一同留存且可回溯。
- **终止审核**：审核组应将终止原因及已开展的工作情况形成报告提交给委托人（5.11.5）。

### 11.2 报告至少应包括或引用的 19 项内容（CNCA 5.11.3）

报告生成器必须逐项产出，缺项即报告不成立（原因码 `REPORT_ITEM_MISSING`）：

| # | 内容 | 本规格的产出来源 |
|---|---|---|
| 1 | 认证机构名称 | 机构档案（#20 依据库/机构信息） |
| 2 | 委托人名称、地址及其代表 | 委托人档案 + 申请评审记录 |
| 3 | 审核类型 | 审核方案（#4 状态机 N1～N6） |
| 4 | 结合/联合/一体化审核情况（适用时） | 审核方案；若做了结合审核，须同时给出结合审核总审核时间不少于各单独体系之和 80% 的说明（5.4.2.4） |
| 5 | 审核准则 | 依据库中的标准 + 规则快照（标准号、版本、条款树） |
| 6 | 审核目的及其是否达到的确认 | 审核方案 + 判定汇总 |
| 7 | 审核范围（组织、职能单元或过程，及审核时间） | 审核方案 + 抽样记录（§6） |
| 8 | 任何偏离审核计划的情况及其理由 | 状态机变更日志 |
| 9 | 任何影响审核方案的重要事项 | 状态机变更日志 |
| 10 | 审核组成员姓名、身份及同行人员 | 审核组信息（须满足 5.4.4 组成要求） |
| 11 | 审核活动的实施日期和地点（现场/非现场、永久/临时场所） | 审核方案 + 记录 |
| 12 | 审核发现、审核证据（或其引用）及审核结论；重点反映主要产品和服务提供过程与控制情况、内部审核和管理评审的过程、所取得的绩效、实际情况与预期质量目标之间的差距和改进机会 | **锚点表 + 判定表投影**；改进机会取 E 组 `suggestion` |
| 13 | 行政监管部门在质量方面抽查的不合格情况及相关原因分析和整改措施有效性（适用时） | 外部检查记录（无则标注不适用并给出理由） |
| 14 | 上次审核后发生的影响体系的重大变更（适用时） | 变更记录 + 责任人确认 |
| 15 | 获证组织对证书和标志使用的控制情况（适用时） | 条款判定结果（8.5 体系内相关条款） |
| 16 | 对以前不符合采取的纠正措施有效性的验证情况（适用时） | 不符合项台账（§10） |
| 17 | 已识别出的任何未解决的问题 | 判定表中 `insufficient` 条款 + E 组未关闭事项 |
| 18 | 说明审核基于对可获得信息的**抽样过程的免责声明** | 抽样记录（§6.1）自动生成 |
| 19 | 审核组的推荐意见以及对申请的认证范围适宜性的结论 | **人写**（工具只提供依据摘要，不得代作） |

### 11.3 报告的生成纪律

- 报告正文**只允许**引用锚点表内 `confirmed` 的锚点（§2.3）。第 12 项的「审核证据（或其引用）」即指锚点引用。
- 报告不得包含自由撰写的结论句；每条结论必须能追到判定记录。
- 第 19 项由人撰写；工具可生成「依据摘要」（各条款判定计数、不符合项状态、证据不足清单）供填写人使用。
- 「抽样免责声明」是**机器生成**的固定句式，绑定实际抽样记录，不得手工美化。

---

## 12. 认证决定与人工闸口

### 12.1 工具不做什么

- 不作认证决定（授予/保持/更新/扩大范围/暂停/撤销）。
- 不作严重不符合的最终分级。
- 不判定认证范围适宜性。
- 不代替审核组长签署报告。
- 不代替认证决定人员作出决定。

依据：CNCA 5.12.1 —— 认证决定人员应为认证机构的**专职认证人员**，**不得为审核组成员**，认证决定过程**不得外包**，且须由中华人民共和国境内的工作人员做出。

### 12.2 工具要做什么

向认证决定环节提供**决策所需的事实包**：

```yaml
decision_pack:
  audit_type: <N1..N6>
  verdict_counts: {conform: n, nonconform: n, insufficient: n, na: n}
  open_ncs: [{id, clause_no, category, status, due_date, overdue}]
  insufficient_clauses: [{clause_no, reason_codes, missing_items}]
  sampling_disclaimers: [...]
  unresolved_issues: [...]
  basis_snapshots: [{source_ref, snapshot_sha256}]
  reviewer_signatures: {audit_lead: ..., reviewer: ...}
  decision_ready: false
```

- `decision_ready: false` 是**默认且强制**的初值；只有在下列条件全部满足时工具才允许置 `true`：
  1. 全部条款均已出终值判定（无 `pending`）；
  2. 所有 `formal_nc` 均已 `confirmed`（有确认人 + 时间）；
  3. 严重不符合未超时限；
  4. 报告 19 项内容齐备；
  5. 审核组长已签署报告。
- 即使 `decision_ready: true`，该字段也只是「材料齐备」的信号，**不构成任何决定**；认证决定仍须由专职认证人员依 5.12.2 条件独立作出。

### 12.3 闸口清单

| 闸口 | 触发点 | 工具行为 | 放行条件（人工） |
|---|---|---|---|
| G1 适用性闸口 | 判定前 | 缺适用性即落 `insufficient` | 审核员补 `applicability.reason` + 依据锚点 |
| G2 证据闸口 | 判定前 | 必核项无 confirmed 锚点即落 `insufficient` | 现场取证/补件 |
| G3 冲突闸口 | 判定时 | 存在未解 conflict 即落 `insufficient` | 审核员澄清 |
| G4 分级闸口 | 不符合项开具时 | `category` 留空 | 分级人填严重/轻微 |
| G5 报告签署闸口 | 报告定稿前 | 缺第 19 项或缺审核组长签署 → 不出 final 版 | 审核组长签署 |
| G6 决定闸口 | 认证决定前 | 仅提供 decision_pack | 专职认证人员作决定 |

---

## 13. 原因码、验收线与可测试性

### 13.1 原因码全集

判定记录中 `reason_codes` 只允许取以下值（机器枚举，不得自造）：

| 原因码 | 含义 | 触发的判定 |
|---|---|---|
| `BASIS_MISSING` | 依据快照缺失 | insufficient |
| `BASIS_UNVERIFIABLE` | 依据库核验退出码 3 | insufficient |
| `BASIS_SUPERSEDED` | 依据已被新版本取代 | insufficient |
| `APPLICABILITY_UNKNOWN` | 适用性未填 | insufficient |
| `NA_WITHOUT_BASIS` | 主张不适用但无依据锚点 | insufficient |
| `EVIDENCE_MISSING` | 必核证据项缺锚点或未确认 | insufficient |
| `EVIDENCE_CONFLICT` | 存在未解冲突 | insufficient |
| `EVIDENCE_UNREADABLE` | 材料不可读 | insufficient |
| `SAMPLING_INSUFFICIENT` | 抽样数不足 | insufficient |
| `CLAUSE_NOT_IN_TREE` | 条款不在该画像条款树中 | insufficient |
| `GAP_CONFIRMED` | 差距已被 confirmed 锚点证实 | nonconform |
| `REPORT_ITEM_MISSING` | 报告 19 项缺项 | 报告不出 final 版 |
| `DECISION_BLOCKED_OVERDUE` | 严重不符合超时限 | decision_pack.decision_ready=false |

### 13.2 验收线（V1～V12）

| # | 验收线 | 判定方法 |
|---|---|---|
| V1 | 同一输入两次调用 `judge()` 输出逐字节相同 | 单元测试：固定输入两次调用，比对 JSON 序列化结果 |
| V2 | 判定记录不含任何未在锚点表中出现的引用 | 静态检查：抽取判定记录中全部锚点引用，与锚点表求差，必须为空 |
| V3 | 锚点缺失时判定必为 `insufficient` | 属性测试：随机删去任一必核锚点，判定不得为 `conform`/`nonconform` |
| V4 | 「不适用」必须有 `applicability.basis_anchor` 且该锚点 confirmed | 属性测试：清空 basis_anchor，判定不得为 `na` |
| V5 | `nonconform` 必须至少引用一个 `material_supported`/`verified` 锚点 | 静态检查 + 属性测试 |
| V6 | 未确认的差异只能落 `suspected_nc`，不得进报告不符合项栏 | 报告生成器单测 |
| V7 | 依据核验退出码 3 时判定只能为 `insufficient` | 桩测试：mock 依据库返回 3 |
| V8 | 不符合项的 `category` 不会被工具预填 | 断言输出模板中该字段为 null |
| V9 | 不符合项的每句事实陈述都有锚点支撑 | 逐句映射检查，缺一即失败 |
| V10 | 报告第 18 项（抽样免责声明）与抽样记录一致，且第 12/17 项不出现未确认锚点 | 报告生成器单测 |
| V11 | `decision_pack.decision_ready` 初始为 false，且五项条件不全时保持 false | 属性测试 |
| V12 | 完整流程（输入 → 判定 → 不符合项 → 报告 → decision_pack）不产生任何自由文本结论 | 端到端断言：产出物中的结论句全部由模板字段拼装 |

### 13.3 可测试性要求

- `judge()` 必须是**纯函数**：输入 = 结构化记录 + 锚点表 + 依据核验结果；输出 = 判定记录。无 IO、无随机、无时钟依赖（时间由输入传入）。
- 依据核验作为**输入**而非内部调用，保证可测（V1）。
- 所有枚举值（判定、事实标记、辅助判断、成果态、问题分类、原因码）集中在单一模块，实现不得各自定义。
- 判定记录须可序列化为 YAML/JSON 并稳定往返（字段顺序稳定）。

---

## 14. 版本、迁移与兼容

- 本规格版本 `v1`，与审核点库 schema（#2）、流程状态机（#4）、输出格式（#10）、依据库（#20）解耦：只依赖枚举值与锚点结构。
- 条款树换版（如 ISO 9001:2026 发布后）时：
  1. 新建条款树文件，`verification_status: toc_verified`；
  2. 审核点库按新树重新生成画像（`covers` 由树驱动重建）；
  3. 依据快照换版（`source_ref` 指向新版本，`status: current`；旧版本标 `superseded`）；
  4. 已有判定记录**不改判**——历史判定绑定当时的快照哈希；重新审核产出新记录。
- 判定枚举新增取值时，必须同时更新：§1 总表、§3.2 映射表、§13.1 原因码全集，且 `judge()` 的分支覆盖该取值；漏一处即实现缺陷。
- 已开具的不符合项在标准换版后不自动失效，仍按原条款判定，直至关闭。

---

## 15. 未解决问题与后续

| # | 问题 | 影响 | 处置 |
|---|---|---|---|
| Q1 | 三套标准的中文条款标题与正文措辞尚未用正版国标逐条核定（#5 I8） | 判定记录里「条款原文引用」只能引 ISO 原文 | 依据库按 ISO 原文存快照；中文标题作定位参考，核定后回填 `verified_by`/`verified_at` |
| Q2 | 标准换版时间线已查明（见 `docs/research/standard-version-watch.md`），但换版后的条款树与审核点尚未产出 | 2028-03-31 起仅可签发 2026 版证书 | 在 #16 下另开换版子票，产出 ISO 9001:2026 条款树 |
| Q3 | 照片类证据的「可取证条款白名单」尚未逐条确定（#11） | 决定哪些条款允许 `image` 锚点 | #11 与本规格共同维护白名单；白名单外的条款出现 `image` 锚点时落 `insufficient` |
| Q4 | modlens 视觉桥尚未跑通（doctor 未通过） | 图片锚点的自动生成不可用 | 人工标注兜底；不阻塞本规格落地 |
| Q5 | 认证机构表单族（12 类）的具体版式未定（#19） | 报告与审查表的落地格式 | #19 出表单清单后回填 §11 的产出映射 |
| Q6 | 「必核证据项」的粒度（条款级 vs 审核点级）尚未最终确认 | 影响 V3 的属性测试边界 | 取审核点级；条款树驱动，任一条款下全部审核点的必核项齐备才可判 `conform` |

> 本规格的落地由 #12（判定引擎）实现，#6（命令封装）提供入口；两者均以本规格的枚举与模板为唯一依据，不得另立口径。


