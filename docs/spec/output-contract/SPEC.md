# 输出契约 S5：预测与分析输出（点预测、区间、归因、人工修正、偏差风险）v1.0

> 对应 GitHub Issue #39（规格 S5）。上游：#28（归因边界与措辞 G2）、#35（零售口径 G3）、#45（组合负荷预测特征与口径）、#46（对外 API 契约与页面数据契约）、#52（缺失与异常 G4）、#26（时间栅格）、#31（朴素基线与滚动样本外回测）、#33（数据契约 D01–D16）、#29（六角色权限矩阵）。
> 下游：#38（原型：负荷预测结果页形态）、#49（实现：负荷预测 API 与预测页）、#61（规格：验收指标与阶段门禁）、#56（头寸与策略口径）、#58（培训考核与七维评分口径）、#59（零售套餐与客户经营口径）。
> 所属地图：#22（湖北省售电公司交易员训战一体化平台，按方案 V1.0）。
> 可执行产物：`output-contract.schema.json`（输出底稿与条目结构）、`prohibited-terms.json`（禁用词与固定句唯一来源）、`reference_output.py`（参考实现 + E1–E12 自检）、`samples/` + `samples.jsonl`（样例）、`verification.txt`（自检输出）。

---

## 0. 本文件定什么、不定什么

### 0.1 边界

本文件定：**对外输出结论的结构**——一个输出底稿（output document）由哪些部分组成、每个部分带哪些字段、每个数值如何携带证据、输出在什么状态下允许出现什么内容、页面／API／报告三处如何引用同一份输出、归因与建议文本允许怎么写。

本文件**不定**：
- 预测怎么算、特征怎么取（#45）、模型怎么训练与切换（#48）、基线怎么跑（#31）。
- 结算科目、公式与容差（#55）、头寸档位与策略口径（#56）、培训七维评分（#58）、零售套餐（#59）。
- API 信封、分页、错误码、Agent 工具契约（#46）——本文件只定义被它们承载的**输出载荷**。
- 页面角色裁剪规则（#29）、视觉形态（#38）。

### 0.2 与 #46 的分工（仓内既有分工原则，非本票新立）

#46 票面已定：「各业务中心自己的对外接口清单由对应规格票给出，本票只定跨中心通用部分。」本票沿用同一原则：

- **本票定跨域通用的输出骨架与不变式**：信封、溯源、状态、数值格式、措辞、指标、修正留痕、投影规则。
- **业务域自有字段不进本票**：结算复现报告的科目与容差属 #55，头寸策略建议的档位属 #56，培训评分的七维属 #58。域字段以 `payload` 承载，并强制声明 `payload_contract_ref`（指向该域的规格票与契约版本）。

因此「同一套结构约束结算复现报告、头寸策略建议与培训评分」这句话的准确含义是：**三者共用同一套骨架、不变式与措辞纪律**；三者各自的业务字段由各自的规格票定义。本票为三者提供**挂载规则**（§8）。

### 0.3 上游 grilling 结论的消费路径（避免漏读）

本票的四张阻塞方票，其结论落到本文件的哪一节：

| 上游票 | 结论要点 | 落到本文件 |
|---|---|---|
| #28 | 归因只到特征贡献层；禁用词；四项限定语；固定句；无法归因四条件；置信度默认分位区间 | §6.1、§6.5、§11 |
| #52 | 五类缺失／异常情形；宁可缺数据也不插值；覆盖率与误差并列 | §5.1、§5.2、§5.3 |
| #45 | 预测对象两级、字段台账、归因受 G2 约束、基线与指标口径 | §4.3、§6.1、§14 fog |
| #35 | 零售口径（目标变量=月度代理购电价；档位命中率优先于点值精度） | **不由本票消费**——见下 |

**关于 #35**：本票不定义电价预测输出类型。依据 #22 Q23，首期不设独立电价预测模块，电价走 M03 结算复现、M05 头寸与策略、M07 零售套餐；#35 的零售口径结论由 **#59（零售套餐与客户经营口径）** 消费，并在 #59 完成后以 `RETAIL_ANALYSIS` 形态挂到本契约骨架上（§8）。因此 #35 对本票的作用是**限定「本票不做什么」**，不是新增输出字段。

---

## 1. 第一性原理

从「对外输出到底是什么」出发，五个不可回避的前提：

1. **输出不是值，是断言。** 一个数值离开它的来源与核验状态就没有意义。→ 数值不得裸奔，必与证据绑定（S5-A2）。
2. **断言可能不成立、也可能不可得。** 「拿不到」与「是零」是两种完全不同的事实。→ 输出有唯一状态，且状态决定哪些值必须为 `null`；禁止 0 填充、禁止拿历史值冒充当前（S5-A4）。
3. **人看页面、机器调 API、事后查报告，但结论只能有一个。** → 单一底稿、三处投影；页面／API／报告引用同一个 `document_id`（S5-A1）。
4. **归因是解释，解释不是事实。** → 归因的证据强度可以低于预测本身，且必须显式标注；命中无法归因条件时不得给贡献排序（S5-A6）。
5. **人会改机器的结论，改必须留痕且可回溯。** → 修正以新版本追加，原输出保留可查（S5-A7）。

其余条文（指标并列、数值格式、只读无执行）是从这五条派生出来的约束，不是并列的首要目的。

---

## 2. 公理（S5-A1 ~ S5-A10）

| 编号 | 公理 | §1 前提 | 落地位置 | 自检 |
|---|---|---|---|---|
| S5-A1 | **单一底稿**：一个输出结论只有一个 `document_id`；页面、API、报告三处引用同一 `document_id`，任何一处不得自行重组字段。（#46 的 `OutputRef` 无 `contract_version` 字段，版本一致性由底稿自身与报告快照三元组保证，见 §9.1、§9.2。） | 前提 3 | §9 投影规则 | E1 |
| S5-A2 | **值必带证**：对外载荷中任何数值必须封装为 `output_value`（含 `value`/`unit`/`source_ref`/`verification_status`/`as_of`）；裸数值不得出现在 `items`/`metrics`/`risks` 中。 | 前提 1 | §4.2 | E2 |
| S5-A3 | **未核验不进判定**：`verification_status` 不为 `VERIFIED` 的值不得作为 `metrics` 的判定依据、不得决定 `risks[].severity`、不得进入 `actions`；只能作为显式标注的展示信息。 | 前提 1、3 | §4.2、§7 | E3 |
| S5-A4 | **状态优先**：输出有唯一 `status`；`status ≠ OK` 时与该状态冲突的数值必须为 `null`，并由 `status_reason` 说明原因；禁止 0 填充、禁止用历史值或前值冒充当前。 | 前提 2 | §5 | E4 |
| S5-A5 | **三类异常各有定路**：缺数据（`PARTIAL`／`DEGRADED`）、源不可达（`REFUSED`）、异常（`ANOMALY_EXCLUDED`／`OUT_OF_RANGE`）三类情形各有唯一确定的输出路径与必填字段集。 | 前提 2 | §5.2 | E5 |
| S5-A6 | **归因不越界**：归因只到特征贡献层，每条必带四项限定语与固定句，必须过禁用词检查；命中「无法归因」四条件之一时不得给贡献排序。 | 前提 4 | §6 | E6、E7 |
| S5-A7 | **修正留痕**：人工修正必带原因、版本、责任人、时间戳与前后值；修正产生新 `document_id`，原输出保留且可被 `supersedes` 链追溯。 | 前提 5 | §6.3 | E8 |
| S5-A8 | **指标并列**：覆盖率（coverage）与误差指标并列为一等指标；拒答率单列；禁止用插值或外推填补的数值计算任何误差指标或补齐缺口。 | 前提 1、2 | §5.3 | E9 |
| S5-A9 | **数值格式**：十进制数值一律以字符串承载并声明固定小数位（电量 3、价 4、金额 2、比例 4）；禁止浮点字面量；`null` 显式书写，不补零。 | 前提 1 | §4.2 | E10 |
| S5-A10 | **只读无执行**：输出中的 `actions` 只允许「查看／导出／申请复核／提交人工修正／切换模型／重训建议」类动作，不得包含任何写入真实交易系统的动作。 | 前提 3 | §10 | E11 |

---

## 3. 输出对象族

`output_type` 枚举——决定 `items[].item_type` 的允许取值与必填扩展位：

| output_type | 中文 | 产生方 | 特有必填部分 |
|---|---|---|---|
| `FORECAST` | 预测输出 | M04 负荷预测（#45/#48） | `items` 含点预测与区间；`attributions` 非空或标不可归因 |
| `RISK` | 偏差风险提示 | M04 负荷预测 | `risks` 非空 |
| `MANUAL_OVERRIDE` | 人工修正记录 | M04 负荷预测 | `overrides` 非空 |
| `DRIFT` | 漂移监测 | M04 模型运营（#48） | `drift_signals` 非空 |
| `SETTLEMENT_REPORT` | 结算复现报告 | M03 结算复现（#55） | `payload_contract_ref` 指向 #55 |
| `STRATEGY_ADVICE` | 头寸策略建议 | M05 头寸与策略（#56） | `payload_contract_ref` 指向 #56 |
| `TRAINING_SCORE` | 培训评分 | M08 培训考核（#58） | `payload_contract_ref` 指向 #58 |
| `RETAIL_ANALYSIS` | 零售套餐分析 | M07 零售套餐（#59） | `payload_contract_ref` 指向 #59 |

`FORECAST` 的区间口径：点预测为 `P50`；上下界为 `P10`／`P90`（**prediction interval，分位区间**）。不用概率表述，除非具备回测校准证据（校准区间 + 样本量），此时必须写明（#28、#45 §9）。

---

## 4. 骨架：输出底稿 `output_document`

### 4.1 顶层字段（字段级定义）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `document_id` | string | 是 | `sha256(output_type\|scope_digest\|as_of\|revision\|generated_at)` 前 16 位；同一结论唯一。`scope_digest` ＝ `sha256(规范化(scope))` 前 16 位（规范化：键排序、无空格、UTF-8）；公式只取底稿自身字段 |
| `output_type` | enum | 是 | 见 §3 |
| `contract_version` | string | 是 | 本契约版本，语义化 `1.0.0`；底稿内自洽，且与报告快照三元组中的版本一致（S5-A1、§9.2） |
| `payload_contract_ref` | object \| null | 是 | `{ticket, contract, version}`；业务域输出必填，纯通用输出可为 null |
| `scope` | object | 是 | `{subject_scope, entity_type, entity_id, period, timezone}`；见 §4.3 |
| `as_of` | datetime | 是 | 结论所依据的**信息截止时刻**（Asia/Shanghai），不得晚于 `generated_at` |
| `generated_at` | datetime | 是 | 生成时刻 |
| `revision` | int | 是 | 输出版本号，从 1 起单调递增；人工修正产生新版本（§6.3） |
| `status` | enum | 是 | 见 §5.1 |
| `status_reason` | object | 是 | `{code, message, detail}`；`code` 与 `status` 一一对应（§5.2） |
| `sources` | array | 是 | 数据来源与批次清单 `{data_source_id, batch_id, verified}`；见 §4.4 |
| `model` | object \| null | 是 | 模型元信息 `{model_id, version, feature_set_hash, training_window, origin_type, degraded_features, missing_features}`；`origin_type` 取 `P_CURVE`／`P_BID`／`P_REVIEW`（与 #45 §10 `forecast_run.origin_type` 同名同义，语义＝预测起点）；`degraded_features`＝影响精度的降级特征，`missing_features`＝取不到的外部特征（#45，两态不可合并）；无模型时为 null（非预测类输出） |
| `metrics` | object | 是 | 指标块，见 §5.3；**并列一等指标** |
| `items` | array | 是 | 输出条目列表，见 §4.5 |
| `attributions` | array | 是 | 归因列表，见 §6.1；非预测类为空数组 |
| `risks` | array | 是 | 偏差风险列表，见 §6.2 |
| `overrides` | array | 是 | 人工修正记录，见 §6.3 |
| `drift_signals` | array | 是 | 漂移信号，见 §6.4；非漂移类为空数组 |
| `notes` | array | 是 | 人读说明串，每条不得含禁用词 |
| `actions` | array | 是 | 可执行动作建议，见 §10 |
| `projections` | object | 是 | 三处投影登记，见 §9 |
| `supersedes` | string \| null | 是 | 被本版本取代的 `document_id`；首版为 null |
| `superseded_by` | string \| null | 是 | 取代本版本的 `document_id`；未被取代为 null |
| `content_hash` | string | 是 | `sha256(规范化后的载荷)` 前 16 位；载荷＝去掉 `content_hash` 自身后的顶层字段规范化 JSON（键排序、无空格、UTF-8）；用于报告复现校验 |

### 4.2 数值封装 `output_value`（S5-A2 / S5-A9）

所有对外数值一律用此结构，禁止裸数值：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `value` | string \| int \| null | 是 | 十进制用**字符串**承载；`null` 表示该值不可得，**不补零** |
| `unit` | string | 是 | 单位；无量纲写 `ratio` / `count` / `none` |
| `precision` | int | 是 | 固定小数位：电量 3、价 4、金额 2、比例 4 |
| `source_ref` | object \| null | 是 | `{data_source_id, batch_id, field}`；`verification_status = MISSING` 时可为 null |
| `verification_status` | enum | 是 | `VERIFIED`（已核验）／`UNVERIFIED`（未核验）／`MISSING`（缺失） |
| `as_of` | datetime | 是 | 该值的信息截止时刻 |
| `note` | string \| null | 是 | 补充说明；不得含禁用词 |

> 数值类型口径：字符串承载十进制是为了避免浮点误差；`precision` 是契约声明位宽，与承载格式无关。比例类统一 4 位，电量 3 位，价 4 位，金额 2 位。

> 文本型取值（`item_type = STATEMENT`）用普通字符串承载，不走十进制格式；schema 以 `anyOf` 区分「十进制字符串／整数／`null`／文本字符串」四种形态，文本字符串排除纯数字串，避免与十进制串混淆。

### 4.3 `scope`

`subject_scope` 取 `COMPANY`（单一售电公司口径）；`entity_type` 取 `USER`（单用户，F021）／`PORTFOLIO`（公司代理组合，F022）／`COMPANY`（公司级）；`entity_id` 为实体标识；`period` 依 #26：日曲线 24 个小时时段，自然月为月度周期，`timezone` 固定 `Asia/Shanghai`。

> `entity_type` 的 `USER`／`PORTFOLIO` 枚举字面与 #45 §10 一致（不得改写为小写或其它变体）；`COMPANY` 是本票在两级预测对象之上扩展的公司级口径。

> **口径提醒**：预测对象是**单用户次日 24 小时负荷**与**公司代理组合聚合负荷**，不是区域电网负荷（#22 Q24）。

### 4.4 `sources`

每个来源条目 `{data_source_id, batch_id, verified}`。`verified = false` 时，该来源支撑的数值其 `verification_status` 不得为 `VERIFIED`。来源清单为空且 `status = OK` 是矛盾的，`status` 必须降级。

### 4.5 输出条目 `items[]`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `item_ref` | string | 是 | 文档内稳定引用（`item:<序号>`），投影与修正据此定位 |
| `item_type` | enum | 是 | `POINT_VALUE`（单值）／`INTERVAL`（区间）／`SERIES`（序列）／`STATEMENT`（陈述）／`FIGURE`（图表）／`SET`（集合，见 §6） |
| `label` | string | 是 | 中文标签 |
| `values` | array | 是 | 该条目的数值集合，每项为 `output_value` |
| `payload` | object \| null | 是 | 业务域自有数据；须与 `payload_contract_ref` 对应 |
| `notes` | array | 是 | 该条目的补充说明 |
| `evidence_refs` | array | 是 | 指向 `sources` 的来源标识数组 |

`item_type = INTERVAL` 时 `values` 固定三个元素，顺序固定为 `P10`／`P50`／`P90`，角色由 `payload.roles`（长度 3 的数组，与 `values` 一一对应）标注。
`item_type = SERIES` 时 `values` 按时段升序排列，`payload.slots` 给出与 `values` 一一对应的时段号数组（1–24，#26）。
`item_type = STATEMENT` 时必须带 `values` 长度为 1 的文本项（该值的 `unit` 取 `none`），且该文本必过禁用词检查。

---

## 5. 状态与指标

### 5.1 `status` 枚举

| status | 中文 | 触发 | 点预测 | 区间 | 归因 |
|---|---|---|---|---|---|
| `OK` | 正常 | 数据齐全且已核验 | 出 | 出 | 可出 |
| `PARTIAL` | 部分缺失 | 部分时段或部分字段缺失 | **不出**（缺失时段） | 出（随缺口比例变宽） | 出但标缺口 |
| `DEGRADED` | 降级 | 关键外部特征缺失，影响精度 | 出 | 出（区间加宽） | 出但标降级影响 |
| `REFUSED` | 拒答 | 目标源整体不可达、缺整月、关键前提不成立 | **不出** | **不出** | **不出** |
| `OUT_OF_RANGE` | 超范围 | 样本落在训练分布外，或特征缺失占比超阈值 | **不出** | **不出** | 标「无法归因」，**不给排序** |
| `ANOMALY_EXCLUDED` | 异常剔除 | 入库校验拒收或事后检出脏数据 | 该时段不出 | 该时段不出 | 受影响范围标剔除 |
| `SUPERSEDED` | 已被取代 | 已被人工修正版取代 | 不再对外呈现 | 不再对外呈现 | 不再对外呈现 |

> `status ≠ OK` 时**不是**「输出为空」：必须有 `status_reason`，并在 `notes` 里写清可执行的下一步（补数据／换源／申请复核）。

**七态与 #52 五类情形的对应**（消除「多出两态」的歧义）：`PARTIAL`／`DEGRADED`／`REFUSED`／`OUT_OF_RANGE`／`ANOMALY_EXCLUDED` 五态覆盖 #52 的五类情形（逐条映射见 §5.2）；`OK` 是「五类情形均未触发」的正常态；`SUPERSEDED` 是由人工修正（S5-A7）派生的生命周期态，与 #52 的数据情形无关。因此**#52 五类情形全部有确定输出路径，无一类落在兜底之外**。

### 5.2 `status_reason.code` 与状态一一对应

| status | code | 必带 detail | 对应 #52 情形 |
|---|---|---|---|
| `OK` | `OK` | — | — |
| `PARTIAL` | `PARTIAL_GAP` | `{gap_slots, gap_ratio, last_available_at}` | 缺部分时段 |
| `DEGRADED` | `FEATURE_DEGRADED` | `{degraded_features, accuracy_impact}` | 外部特征缺失 |
| `REFUSED` | `SOURCE_UNREACHABLE` | `{reason: LOGIN_REQUIRED\|PAID\|NOT_PUBLISHED\|NETWORK_UNREACHABLE, target_ref}` | 源不可达／缺整月 |
| `REFUSED` | `PRECONDITION_FAILED` | `{precondition, detail}` | 关键前提不成立 |
| `OUT_OF_RANGE` | `OUT_OF_DISTRIBUTION` | `{feature_id, distance, train_range}` | 样本超训练分布 |
| `OUT_OF_RANGE` | `FEATURE_MISSING_RATIO` | `{missing_ratio, threshold, feature_id}` | 特征缺失占比超阈值 |
| `ANOMALY_EXCLUDED` | `REJECTED_ON_INGEST` | `{check_id, batch_id, report_ref}` | 入库前异常跳变 |
| `ANOMALY_EXCLUDED` | `DIRTY_EXCLUDED` | `{batch_id, excluded_count, affected_scope}` | 入库后检出脏数据 |
| `SUPERSEDED` | `OVERRIDDEN` | `{superseding_document_id}` | 人工修正 |

**规则**：缺数据／源不可达／异常三类情形各有且只有一条确定的输出路径（验收③）；`PARTIAL` 必须同时给「缺口标注 + 区间」；`REFUSED` 必须写明剔除原因，且**不得显示历史值冒充当前**。

### 5.3 指标块 `metrics`（S5-A8）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `coverage` | output_value | 是 | 覆盖率，一等指标；比例 4 位 |
| `accuracy` | array | 是 | 误差指标，每项 = **output_value 展开**（`value`／`unit`／`precision`／`source_ref`／`verification_status`／`as_of`）+ 指标专有字段 `name`／`sample_size`／`excluded_count`／`excluded_reason` |
| `refusal_rate` | output_value | 是 | 拒答率（拒答点数 ÷ 应答点数），比例 4 位 |
| `baseline_comparison` | object | 是 | `{baseline_id, baseline_value, window}`；`baseline_value` 为 `output_value` 或 `null`（基线未锁定时为 `null`） |
| `sampling_note` | string | 是 | 剔除与样本说明 |

**硬规则**（承接 #52、#45 L11、#22 Q28）：
- coverage 与误差指标**并列**，不得只报误差。
- 插值／外推填补的数值**禁止**进入 `accuracy` 的任何计算，也**禁止**用来补齐缺口。
- `MAPE` 计算须剔除 `|y| < ε`（ε 取该序列历史负荷 P1，写死），报告 `excluded_count` 与 `excluded_reason`；剔除比例 > 10% 时 `name` 标 `MAPE_NOT_APPLICABLE`。
- 区间指标报 `PICP(P10,P90)` 与平均区间宽度；无分位数时显式写「覆盖率不适用」，不留空。
- **回测侧口径**（承接 #52）：被跳过或被拒答的时段计入 `coverage` 的分母、分子不增——覆盖率＝有效输出点数 ÷ 应输出点数；拒答的点单独计入 `refusal_rate`，不得从分母中剔除。
- **精度数字不得预填**：基线未确认前 `baseline_value = null`、`accuracy` 各项 `value` 可为 `null` 并说明「待基线确认」（对齐 #61 A06）。

---

## 6. 四个专有集合

### 6.1 归因 `attributions[]`（S5-A6，承接 #28、#45 §9）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `feature_id` | string | 是 | 特征标识，取自 #45 特征台账 |
| `contribution` | output_value | 条件 | `attributable = true` 时必填 |
| `direction` | enum | 条件 | `UP`／`DOWN`／`NEUTRAL`；`attributable = true` 时必填 |
| `text` | string | 是 | 归因文本；**必须同时含四项限定语与固定句**，且过禁用词检查 |
| `verification_status` | enum | 是 | 继承自 #28：已核验／未核验／缺失 |
| `time_window` | object | 是 | `{start, end, label}`：基于哪段历史的统计关联 |
| `confidence` | object | 是 | `{kind: PREDICTION_INTERVAL\|LEVEL, p10, p90, level, calibration}`；`kind = PREDICTION_INTERVAL` 时给分位区间；给概率表述时 `calibration` 必填 `{interval, sample_size}` |
| `statement` | string | 是 | **常量** `基于历史统计关联，非因果判定` |
| `attributable` | bool | 是 | 是否可给贡献排序 |
| `not_attributable_reason` | enum \| null | 是 | `attributable = false` 时必填，取 `TARGET_SOURCE_MISSING`／`TARGET_SOURCE_UNVERIFIED`／`FEATURE_MISSING_RATIO`／`OUT_OF_DISTRIBUTION`／`UNSHIFTED_OPERATIONAL_DATA` |

**「无法归因」四条件**（命中任一 → `attributable = false`，不得给贡献排序，不得给 `text` 里的排序表述）：
目标源缺失或未核验／特征缺失占比超阈值／样本落在训练分布之外／回测中运行类数据未按制度 shift。

**四项限定语**（`text` 必须逐项出现，缺一拦截）：
① 数据来源与核验状态　② 时间窗　③ 置信度（分位区间或等级）　④ 固定句 `基于历史统计关联，非因果判定`。

**四项限定语的可判定形式**（参考实现 `reference_output.py` 按此判定，缺一即拦截）：
| 限定语 | 机器锚点 | 判定规则 |
|---|---|---|
| ① 数据来源与核验状态 | `attribution.verification_status` + `text` 中的核验状态词 | `text` 必须出现与 `verification_status` 对应的中文状态词（`VERIFIED`→`已核验`、`UNVERIFIED`→`未核验`、`MISSING`→`缺失`） |
| ② 时间窗 | `attribution.time_window.label` | `text` 必须出现 `time_window.label` 的原文 |
| ③ 置信度 | `attribution.confidence` | `kind = PREDICTION_INTERVAL` 时 `text` 必须出现 `P10` 与 `P90`；`kind = LEVEL` 时 `text` 必须出现 `HIGH`／`MEDIUM`／`LOW` 之一 |
| ④ 固定句 | 常量 | `text` 必须包含 `基于历史统计关联，非因果判定` |

> 四项限定语写进结构字段（`verification_status`／`time_window`／`confidence`／`statement`）是**必要条件**，`text` 内出现对应锚点是**附加要求**：机器可校验，且人读文本自带上下文。

**禁用词**见 §6.5（`prohibited-terms.json` 是唯一来源）。

### 6.2 偏差风险 `risks[]`（F026）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `risk_code` | enum | 是 | `DATA_GAP`／`OUT_OF_DISTRIBUTION`／`SOURCE_UNREACHABLE`／`DRIFT_DEGRADATION`／`MODEL_STALENESS` |
| `severity` | enum | 是 | `INFO`／`LOW`／`MEDIUM`／`HIGH` |
| `statement` | string | 是 | 风险陈述，过禁用词检查 |
| `target_ref` | string | 是 | 指向 `items[].item_ref` 或 `scope` |
| `verification_status` | enum | 是 | 支撑该风险的证据核验状态 |
| `recommendation` | enum | 是 | 仅允许 `NONE`／`RETRAIN_RECOMMENDED`／`DATA_COLLECTION_RECOMMENDED`／`MANUAL_REVIEW_RECOMMENDED`／`MODEL_SWITCH_RECOMMENDED`／`SOURCE_REPLACEMENT_RECOMMENDED` |

> `severity` 属自动判定，依据必须是 `VERIFIED` 的证据（S5-A3）；否则降级为 `INFO` 并在 `statement` 里注明证据未核验。

### 6.3 人工修正 `overrides[]`（F025，S5-A7）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `override_id` | string | 是 | 修正记录标识 |
| `target_document_id` | string | 是 | 被修正的原文档 |
| `revision` | int | 是 | 修正后版本号 |
| `reason_code` | enum | 是 | `DATA_ERROR`／`MODEL_LIMITATION`／`DOMAIN_KNOWLEDGE`／`CONTRACT_CURVE_ADJUST`／`OTHER` |
| `reason_text` | string | 是 | 修正原因说明（不得为空） |
| `actor` | object | 是 | `{role, user_id}`；`role` 取 #29 六角色之一 |
| `created_at` | datetime | 是 | 修正时刻 |
| `scope` | object | 是 | `{item_ref, field}`：改的是哪条目的哪个字段 |
| `before` | output_value \| null | 是 | 修正前的值（可为 null） |
| `after` | output_value \| null | 是 | 修正后的值 |
| `supersedes` | string \| null | 是 | 修正生成的文档标识（链回新 `document_id`） |
| `audit_ref` | string | 是 | 审计留痕引用，指向 #29 的审计记录 |

**修正语义**：修正不改写历史。修正后生成**新** `document_id`（`revision` 递增），原文档 `superseded_by` 指向新文档，新文档 `supersedes` 指向原文档；原文档状态置 `SUPERSEDED`。人工修正后的数值同样携带 `verification_status`（人工核验记 `VERIFIED`，来源 `MANUAL_OVERRIDE`）。

### 6.4 漂移信号 `drift_signals[]`（F027）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `signal_id` | string | 是 | 信号标识 |
| `window` | object | 是 | `{start, end}`：效果评估窗口 |
| `metric` | object | 是 | `{name, value, baseline_value}`；`value` 与 `baseline_value` 均为 `output_value` 或 `null`（基线未锁定时 `baseline_value` 为 `null`） |
| `degradation` | bool | 是 | 是否判定为效果衰减 |
| `magnitude` | output_value | 是 | 相对基线的变化量 |
| `signal_text` | string | 是 | 信号陈述，过禁用词检查 |
| `recommendation` | enum | 是 | 与 `risks[].recommendation` 同枚举（重训建议走这里） |
| `evidence_refs` | array | 是 | 证据引用（回测批次、基线记录） |

### 6.5 措辞禁用清单（唯一来源：`prohibited-terms.json`）

**禁用词**（出现即拦截；大小写、全半角均归一后比对）：
因为、因此、原因、导致、保证、必然、一定会、稳赚、建议买入、建议卖出、预测一定、肯定能、由于；
以及单独作连词的「因」——实现用正则 `(?<!原)因(?!果)` 兜住「因高温」这类写法（「非因果判定」固定句除外）。

> 该清单是 #28 的**超集**：#28 列了「因、因为、导致、原因是、保证、必然、一定会、稳赚、建议买入/卖出、预测一定、肯定能」，#45 §9 另补「因此、原因、由于」。本票取全集，#45 的 `check_attribution` 与本票的检查器共用同一份 JSON。

**固定句**（归因文本必含）：`基于历史统计关联，非因果判定`。

**校验范围**：`attributions[].text`、`items[item_type=STATEMENT].values[].value`、`risks[].statement`、`drift_signals[].signal_text`、`notes[]`、`overrides[].reason_text`、`actions[].label`。

---

## 7. 溯源与核验状态

`verification_status` 三态（#28 定，不得扩充）：

| 取值 | 中文 | 含义 | 可否进自动判定 |
|---|---|---|---|
| `VERIFIED` | 已核验 | 来源可达且已按制度核验 | 可 |
| `UNVERIFIED` | 未核验 | 来源可得但未核验，或核验证据不全 | **不可**（S5-A3） |
| `MISSING` | 缺失 | 来源不可得或字段缺失 | **不可** |

**溯源最小集**：任一输出可追到「数据来源 + 批次 + 字段 + 信息截止时刻 + 核验状态」（验收②）。`output_value.source_ref` + `as_of` + `verification_status` 三者齐全即满足。

---

## 8. 业务域挂载规则（结算／头寸／培训／零售）

业务域输出（`SETTLEMENT_REPORT`／`STRATEGY_ADVICE`／`TRAINING_SCORE`／`RETAIL_ANALYSIS`）遵守：

1. **必须声明 `payload_contract_ref`**：`{ticket, contract, version}`，指明域字段的唯一出处（如结算为 `{ticket: "55", contract: "settlement-replay", version: "1.0.0"}`）。
2. **域字段进 `items[].payload`**：`item_type = SET` 或 `POINT_VALUE`，不得另起顶层字段。
3. **骨架不变式照旧**：数值仍须经 `output_value` 封装（域自有数值同样受 S5-A2/A9 约束）；域字段也必须过禁用词检查；域输出也必须有 `status` 与 `status_reason`。
4. **域特有指标进 `metrics.accuracy`**（`name` 用域指标名），但 coverage 与 refusal_rate 仍为一等指标。
5. **域特有风险进 `risks[]`**，只能用 §6.2 的 `risk_code` 与 `recommendation` 枚举；若域需要新码，先改本契约再改域规格。
6. **域特有修正进 `overrides[]`**，沿用 F025 留痕字段。

> 结论：域规格票只需遵守骨架，不得重定义信封、状态语义、措辞纪律与指标并列规则。

---

## 9. 三处同源：页面／API／报告投影（S5-A1，验收①）

### 9.1 `projections` 登记

`projections` 是底稿自带的**引用登记表**：登记这份结论在页面／API／报告三处的落点，并让三处都能指回同一个 `document_id`。它是契约内部的索引，不是线上报文格式。

```json
"projections": {
  "page_blocks":  [{"page_id": "trader_home", "block_id": "forecast", "document_id": "…", "output_ref": {"output_id": "…", "contract": "#39", "status": "READY"}}],
  "api_endpoints": [{"method": "GET", "path": "/api/v1/outputs/{output_id}", "document_id": "…"}],
  "report_sections": [{"section_id": "forecast_result", "document_id": "…", "content_hash": "…"}]
}
```

- **三处同源的核对键是 `document_id`**：页面的 `output_ref.output_id`、API 的 `OutputDocument.output_id`、报告的 `document_id` 必须逐字相同。
- 页面登记里的 `output_ref` **逐字取自 #46 的 `OutputRef`**（`output_id`／`contract: "#39"`／`status`）。#46 的 `OutputRef` 是 `additionalProperties: false` 且**无 `contract_version` 字段**，故不得在 `output_ref` 内追加版本号；版本一致性由底稿自身的 `contract_version` 与报告快照三元组承担（见 §9.2 报告行）。
- 任何一处自行重组字段即违反 S5-A1。

### 9.2 投影规则

| 投影 | 规则 |
|---|---|
| API | 落点是 #46 已定义的 `GET /api/v1/outputs/{output_id}`（`operationId = getOutput`）。`data` 为 #46 的 `OutputDocument`：`{output_id, contract, status, payload}`，其中 `output_id` = 本底稿 `document_id`、`contract` = `"#39"`、`status` = `"READY"`、**`payload` = 本底稿 `output_document` 全文**（#39 契约版本由 `payload.contract_version` 承载）。`meta` 用 #46 的 `Meta`（`request_id`／`api_version`／`contract_version`／`generated_at`）；**`meta.contract_version` 回显的是 #46 的 API 契约版本，与 #39 的契约版本是两条版本轴，不得混同、不得互相校验**。 |
| 页面 | 落点是 #46 已定义的 `GET /api/v1/pages/{page_id}`。页面块**不复制载荷**，只带 `source.output_ref = {output_id, contract: "#39", status: "READY"}` 指向底稿，再按角色裁剪渲染；越权字段**显式抹掉**并留 `masked_fields` 痕迹（#38、#29、#46）。页面块的 `id`／`type`／`title`／`source`／`state`／`freshness`／`quality`／`notes`／`actions`／`data` **逐字沿用 #46 `page-data.schema.json` 的既有定义，本票不自造 `state` 取值、不追加字段**；`state = empty` 时 `data` 必须为 `null` 且 `notes` 说明原因，禁止 0 填充（#46）。本票的 `status` → 页面 `state` 映射见 §9.4。 |
| 报告 | 报告章节引用同一 `document_id`，并附**快照三元组**：`document_id` + `contract_version`（本契约版本）+ `content_hash`；复现校验以三元组比对。 |

### 9.3 与 #46 的衔接

#46 的 `page-data.schema.json` 与 `openapi.json` 中，预测／归因类引用当前是 `output_ref{output_id, contract: "#39", status: "PENDING_CONTRACT"}` 占位。**本票落地后，该占位的 `status` 改为 `READY` 并填 `output_id`**。

改动落点与顺序：
1. #46 的 `OutputDocument` 目前只定义了 `{output_id, contract, status, payload}`（`payload` 为自由对象），**本票不修改该 schema 的结构**，只在 `payload` 内落地本契约的 `output_document`；
2. `page-data.schema.json` 的 `x-page-catalog` 中 `tomorrow_forecast` 块（`type: output_ref`）在实现时填入真实 `output_id` 并改 `status: READY`；
3. `openapi.json` 的 `OutputDocument.payload` 若要收紧为字段级定义，由实现票（#49）或 #46 的契约修订引用本票的 `output-contract.schema.json` 完成，**不得在本票改写 #46 文件**。

本票只定义契约内容与占位更新条件，不改 #46 既有文件。

### 9.4 本票 `status` → #46 页面 `state` 映射（页面投影必用）

页面 `state` 只有 `ok`／`empty`／`partial`／`error`／`forbidden` 五个取值，本票的 `status` 有七个，二者**必须**按下表折算，不得自造中间值：

| 本票 `status` | 页面 `state` | 页面块 `data` | 页面块 `notes` 要求 |
|---|---|---|---|
| `OK` | `ok` | 承载载荷 | 可空 |
| `PARTIAL` | `partial` | 承载载荷（缺时段以 `null` 标注，不补零） | 至少一条，写明缺口时段与缺口比例 |
| `DEGRADED` | `partial` | 承载载荷 | 至少一条，写明降级特征与精度影响 |
| `REFUSED` | `empty` | 必须 `null` | 至少一条，写明「源不可达／缺整月／前提不成立」及剔除原因 |
| `OUT_OF_RANGE` | `empty` | 必须 `null` | 至少一条，写明「超出可用范围」，不得给贡献排序 |
| `ANOMALY_EXCLUDED` | `partial`（全部时段被剔除时为 `empty`） | 承载未被剔除的载荷或 `null` | 至少一条，写明剔除批次与受影响范围 |
| `SUPERSEDED` | `empty` | 必须 `null` | 至少一条，写明已被哪个 `document_id` 取代 |

页面块 `quality` 用 #46 的 `Quality`：`coverage_ratio` 取本底稿 `metrics.coverage.value`（四位小数字符串，与 #46 的 `^\d\.\d{4}$` 一致），`valid`／`partial`／`missing` 分别计数 `items[].values[].verification_status` 为 `VERIFIED`／`UNVERIFIED`／`MISSING` 的条目数。`freshness.as_of` 取本底稿 `as_of`。

---

## 10. `actions`：允许做什么

`actions[]` 每项 `{action_id, type, label, target_ref, requires_approval}`。

`type` 仅允许：`VIEW`（查看）／`EXPORT`（导出）／`REQUEST_REVIEW`（申请复核）／`SUBMIT_OVERRIDE`（提交人工修正）／`SWITCH_MODEL`（切换模型）／`PROPOSE_RETRAIN`（建议重训）／`COLLECT_DATA`（建议补数据）。

**禁止**任何写入真实交易系统的动作（承接 #22 Q30、#46「效果枚举无 EXECUTE」）；`type` 不含 `EXECUTE`，出现即校验失败（E11）。

---

## 11. 术语表（#28 要求：中文词（English term））

| 中文词 | English term | 本契约中的含义 |
|---|---|---|
| 输出底稿 | output document | 一条对外结论的唯一事实源，含 `document_id`、状态、溯源、指标与各集合 |
| 投影 | projection | 同一底稿在页面／API／报告三处的呈现形态，字段不重组 |
| 归因 | feature attribution | 特征贡献层的统计关联解释，非因果判定 |
| 置信区间 | prediction interval | 预测分位区间（P10–P90），默认的区间表述方式 |
| 偏差风险 | data-gap / out-of-distribution risk | 数据缺口或分布外导致的输出不可靠信号 |
| 口径 | measurement convention | 同一名词的计算与统计边界（实体、周期、频率、聚合方式） |
| 上卷 | aggregation | 由细时段汇总到粗时段（电量求和、价格按电量加权） |
| 运行类数据 | ex-post operational data | 预测时点之后才产生的数据，必须 shift 后才可用 |
| 预测类数据 | ex-ante declaration data | 预测时点之前已申报／已知的数据 |
| 核验状态 | verification status | 已核验／未核验／缺失三态 |
| 拒答 | refusal | 因前提不成立或源不可达而不给结论 |
| 人工修正 | manual override | 人对机器结论的修改，须留痕并生成新版本 |
| 覆盖率 | coverage | 有效输出点数占应输出点数的比例，一等指标 |
| 漂移 | drift | 模型效果随时间相对基线的衰减 |

> 本仓无 `GLOSSARY.md` 惯例（仓内规格均把术语写在各自 SPEC.md 内），故术语表落在此处；如日后建仓级 `GLOSSARY.md`，本表为其子集，不重复维护。

---

## 12. 验收对照（对齐 #39 五条验收）

| # | #39 验收项 | 本文件落点 | 自检 |
|---|---|---|---|
| ① | 输出契约写成字段级定义，且页面／API／报告三处引用同一份 | §4 字段级定义、§9 投影规则、S5-A1 | E1 |
| ② | 任一输出可追到来源与核验状态 | §4.2、§4.4、§7 | E2、E3 |
| ③ | 缺数据／源不可达／异常三类情形各有一条确定输出路径（G4） | §5.1、§5.2 | E4、E5 |
| ④ | 归因与建议文本过禁用词检查 | §6.1、§6.5、§10 | E6、E7、E11 |
| ⑤ | 人工修正有版本与责任人留痕 | §6.3 | E8 |

补充自检：E9 指标并列与插值禁用；E10 数值格式（字符串十进制、固定小数位、null 不补零）；E12 业务域挂载规则（payload_contract_ref 必填、域字段进 payload、域输出同样受骨架约束）。

---

## 13. 下游接口

- **#46 对外 API 契约**：本契约定义被 API 信封承载的载荷，落点是 `GET /api/v1/outputs/{output_id}` 的 `data.payload`（见 §9.2、§9.3）；`page-data.schema.json` 与 `openapi.json` 中预测／归因块的 `output_ref` 从 `PENDING_CONTRACT` 改 `READY` 并填 `output_id`。
- **#38 原型：负荷预测结果页**：原型字段取自本契约 `items`／`attributions`／`risks`／`metrics`／`overrides`；元信息区（模型版本、数据批次、覆盖率、与季节朴素基线的对比口径）分别对应 `model`、`sources`、`metrics.coverage`、`metrics.baseline_comparison`；块级 `state` 按 §9.4 折算，不得自造取值。
- **#49 实现：负荷预测 API 与预测页**：页面与 API 字段一致＝同一 `document_id` 的两处投影（API 落 `data.payload`，页面落 `source.output_ref`，见 §9.2）；缺特征时的输出标注＝`DEGRADED`／`OUT_OF_RANGE` 路径；人工修正入口＝`overrides[]` 追加。
- **#61 验收指标与阶段门禁**：A06 只认「相对基线改善 + 覆盖率」双条件，基线未锁定前 `metrics.baseline_comparison.baseline_value = null`。
- **#56／#58／#59 业务域规格**：按 §8 挂载规则接入，不得重定义骨架。
- **#37 回测报告与达标判定**：报告字段取自 `metrics` 与 `drift_signals`。

---

## 14. 尚未定（fog）

1. **`GLOSSARY.md` 是否升格为仓级文件**：#28 原本要求「待仓内惯例确认后再建」；现仓内惯例是术语写在各 SPEC.md 内，本票据此只在本文件建表。若用户决定建仓级 `GLOSSARY.md`，本表迁入并保留指针。
2. **域特有 `risk_code` 的扩展**：#56／#58 若需要新风险码，是扩展本票枚举还是归入 `DATA_GAP`／`MODEL_STALENESS`，待域票提出后回改。
3. **报告快照的留存介质**：快照三元组落在文件、数据库还是对象存储，由实现票决定；本票只定义三元组内容。
4. **`actions` 与 #46 Agent 工具的对应**：Agent 侧是否直接暴露 `PROPOSE_RETRAIN` 等动作，待 #72（Agent 编排层）确认。
5. **区间随缺口变宽的量化公式**：`PARTIAL` 下区间加宽的倍数由 #31／#37 的回测证据确定；本票只要求「随缺口比例变宽」这一单调性可验证。

---

## 15. 变更记录

- v1.2（2026-11-06）：实现阶段复核修订——① `document_id` 计算式中的 `created_at` 改为底稿自有字段 `generated_at`（原式引用了顶层并不存在的字段）；② `model.origin_type` 枚举改为与 #45 §10 `forecast_run.origin_type` 同名同义的 `P_CURVE`／`P_BID`／`P_REVIEW`，`output-contract.schema.json` 同步；③ §6.1 增补四项限定语的可判定形式（机器锚点），使 E6 可自动校验；④ 本票新增 `reference_output.py`（最小 JSON Schema 校验器 + 禁用词检查器 + 三处投影 + E1–E12 自检）、`samples/` 样例集与 `verification.txt` 自检记录；⑤ `output_value.value` 的 schema 形态由 `oneOf`（十进制串／整数／null）改为 `anyOf`（另加文本串 `$defs/text_string`，排除纯数字串），与 §4.2 的文本型取值说明对齐；⑥ 人工修正的 `override.supersedes` 明确指向被修正的原底稿（与 `override.target_document_id` 同值），避免与底稿级 `supersedes` 语义混淆。
- v1.1（2026-11-06）：实现阶段自查修订——① 修正 S5-A2 落地的三处结构缺口：`metrics.accuracy[]`、`metrics.baseline_comparison.baseline_value`、`drift_signals[].metric` 原为裸数值，现一律封装为 `output_value`（accuracy 项为展开式 + 指标专有字段），使「任一输出可追到来源与核验状态」在指标层同样成立；② 按 #45 §10 补 `model.origin_type`、`model.missing_features`（与 `degraded_features` 两态不可合并）；③ 明确七态与 #52 五类情形的对应，消解「多出两态」的歧义；④ 明确页面投影复用 #46 既有 `state` 枚举、空态 `data = null`；⑤ 补回测侧覆盖率与拒答率口径；⑥ 公理表补「§1 前提」映射列，使 §1→§2 的推导可逐条核对。
- v1.0（2026-11-06）：首版。落定第一性原理五条、S5-A1~A10 公理、输出对象族八类、`output_document` 字段级定义、`output_value` 封装、`status` 七态与 `status_reason` 一一对应、指标并列规则、归因／风险／人工修正／漂移四集合、业务域挂载规则、三处同源投影规则、术语表；E1–E12 自检全通过。
