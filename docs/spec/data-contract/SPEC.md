# 统一数据契约（D01–D16）v1.0

> 票：#33「规格：统一数据契约（D01–D16 十六类数据对象）」｜地图：#22｜日期：2026-11-05
> 执行依据：`contract.schema.json`（机器可读，唯一执行依据）；本文是人读解释与取舍记录。
> 参考实现与自检：`reference_contract.py`（E1–E8，8 项全 PASS 见 `verification.txt`）。

## 1. 依据

### 1.1 需求与方案

- **F007 文件批量导入**：Excel、CSV、PDF、标准模板；**F008 统一主体编码**：用户、计量点、合同、交易主体、零售套餐；**F010 质量规则检查**：缺失、重复、越界、错位、单位、逻辑异常；**F011 血缘与版本**：来源、批次、处理规则、版本、责任人。
- **A02 数据完整性**：关键数据完整率 ≥99%，异常均可定位；**A08 敏感数据控制**：未经授权敏感数据不得出域，高风险泄漏 0 项、拦截日志完整。
- 方案 `02-数据清单` 给出 **D01–D16** 十六类对象的格式、敏感级别、用途域、接入方式、核心字段与优先级（P0/P1）。
- **Q02（客户待确认）**：历史数据样本、字段字典、账单格式 —— 阶段一第 1 周提供；本契约必须在样本缺席时也能先立起来。

### 1.2 已定决策（上游票）

- **#26 时间栅格**：主栅格小时级 24 时段；15 分钟 96 点只作外部源原始粒度；电量求和、功率平均、官方价算术平均；缺失不插值不补零；质量五级 OK/ESTIMATED/PARTIAL/SUSPECT/MISSING；日/月/年为可重建派生（T1–T15）。
- **#27 湖北市场机制**：现货 15 分钟出清、结算小时价=4 点算术平均；披露信息以结构化为主、保留 ≥2 年；预测类申报前披露、运行类次日披露。
- **#36 全国数据可得性**：逐 15 分钟出清价/结算单价需登录；统一开放接口「标准另行制定」，当前无可免登录调用接口。
- **#53 规则版本核查**：规则须可追溯到文件全称+文号+发布/实施日期+现行状态+原文 URL；规则知识库按文件→章节→条款建索引。

### 1.3 已有一手样本（决定字段名与枚举）

- 国家 9 号文信息披露附表结构化 CSV（披露层级：公众/公开/特定）。
- 湖北工商业分时电价时段与浮动比例 CSV（尖峰/高峰/平/低谷 + 时段区间 + 比例）。
- 2026 年逐日交易日标记 CSV（日类型恰为四值：普通工作日、普通休息日、法定节假日、调休上班日）。
- open-meteo 观测 JSON（整点温度、湿度、天气码）。

## 2. 第一性原理

数据层的一切设计都从这五条出发，而不是从「方便某个功能」出发：

1. **数据先于模型，契约先于导入器。** 导入器会有很多个（Excel/CSV/PDF/API），契约只有一份；导入器的差异只能体现在映射表，不能体现在表结构。
2. **事实必须可重建。** 原始文件不可变（保留字节与 hash），主表可由「原始文件 + 导入器版本 + 规则版本」重放；凡是不能重放的派生，都不得成为唯一来源（同 #26 T15）。
3. **事实 = 主体 × 时轴 × 科目 × 数值（含单位）。** 主体编码、时轴、科目是三套正交维度，含义不能编进字段值里（例如不要把「峰段电量」写成一个字段，它是科目/时段的组合）。
4. **缺失不是零，质量是一等字段。** NULL = 未观测，0 = 观测到零；每一行事实都带 `quality_flag`，否则下游无法判断能不能用（同 #26 T12/T7）。
5. **敏感级别是数据的内在属性，出域是策略问题。** 同一字段在本地可用、出域被拦，不因「这次只是给模型看」而降级。

由这五条得到六条公理（C-A1…C-A6）：

| 公理 | 内容 |
|---|---|
| C-A1 单一事实源 | 同一事实只存在一张主表，其余是派生；派生必须可重建 |
| C-A2 不可变证据 | 原始文件、file_hash、批次记录永久留存；重跑幂等 |
| C-A3 坐标正交 | 主体、时间、科目三个维度独立编码，不互相编码 |
| C-A4 单位显式 | 每个数值字段在 schema 里声明单位与精度，禁止裸数 |
| C-A5 敏感随字段 | 表级默认 + 字段级覆盖，冲突时取更严 |
| C-A6 双时态 | 业务有效期（valid_from/to）与系统登记时间（recorded_at）分离，禁止覆盖历史 |

## 3. 规范（C1–C12）

### C1 契约形态

- 一份机器可读 schema（`contract.schema.json`，版本 1.0.0）+ 一份人读规格（本文）。
- **schem 是执行依据**：导入器、校验器、提示词脱敏器都读它；本文解释「为什么」。
- 版本规则：加字段/加枚举值 → minor；改字段语义/删字段/改主键 → major。任何变更必须同步 `samples.jsonl` 与自检。

### C2 命名、类型与精度

- 表名 `dNN_<english>`；字段 `snake_case`；枚举值 `UPPER_SNAKE`；布尔 `is_*`。
- 类型只用 9 种：`string`、`date`、`datetime`、`int`、`decimal`、`bool`、`enum`、`array`、`json`。
- 定点数一律 DECIMAL，禁止 float：电量 3 位小数（0.001 MWh = 1 kWh）、价格 4 位、金额 2 位、比例 4 位。
- 时区固定 Asia/Shanghai（+08:00）；`datetime` 必须带偏移量，禁止裸本地时间。
- 单位：MWh / 元每 MWh / 元 / kW / 摄氏度 / %；schema 的 `defaults` 是缺省值，字段可覆盖。

### C3 通用列与血缘（F011）

每张表都带 12 个通用列（见 schema `common_columns`）：`batch_id`、`source_system`、`source_file`、`source_row_no`、`file_hash`、`imported_at`、`importer_version`、`rule_version`、`valid_from`、`valid_to`、`is_current`、`owner`。

- `file_hash` 是原件 SHA-256，既做幂等键也做证据；`source_row_no` 让异常能定位回原文件第几行。
- `is_current` 在同一业务键下唯一（部分唯一索引）；历史版本保留，不物理删除。

### C4 业务键与幂等

- 每类对象在 schema 里声明 `business_key`；物理主键 = `sha256(dataset|business_key 值)`，用于 upsert。
- 幂等键 = `(dataset, file_hash, source_row_no)`：同一文件重跑不重复入库；同键不同值按「来源优先级 → 批次号 → 更新时间」裁决并记冲突（与 #26 T8 同口径）。

### C5 统一主体编码（F008）

五类主体：`USER`（用户）、`METER`（计量点）、`CONTRACT`（合同）、`TRADER`（交易主体/对手方）、`PACKAGE`（零售套餐）。

**格式**：`<TYPE>-<ORG4>-<SEQ6>-<CHK>`

- `ORG4`：行政区划代码前 4 位（湖北 42xx；武汉 4201、宜昌 4205、襄阳 4206…）。
- `SEQ6`：公司内 6 位顺序号，一经分配不复用。
- `CHK`：ISO 7064 MOD 11,2 校验位，对 `ORG4+SEQ6` 共 10 位数字计算；余数 10 记 `X`。
- 例：`USER-4201-000123-X`、`METER-4201-000456-4`、`CONTRACT-4201-000789-2`。

**归一步骤**（同一主体在不同文件里的不同写法）：

1. NFKC 归一（全角→半角）→ 2. 去空白与标点 → 3. 去法律后缀（`股份有限公司` > `有限责任公司` > `有限公司` > `集团有限公司` > `公司`，最长优先）→ 4. 精确匹配 → 别名表匹配。

别名表字段：`raw_name`、`canonical_id`、`source`、`confidence`、`approved_by`、`approved_at`。**模糊匹配只进人工待审队列，不自动合并**——错误合并比不合并更难恢复。

例：`湖北 长江新材料有限公司`、`湖北长江新材料有限责任公司`、`湖北长江新材料股份有限公司` 归一后同为 `湖北长江新材料`，命中同一 `canonical_id`。

### C6 时轴与粒度（接 #26）

- 时序主表用 `business_date + slot_no + interval_start`；`slot_no ∈ 1..24`，`interval_start == business_date + (slot_no-1) 小时`。
- 15 分钟源数据进 **落地区**（证据层，非 16 类对象），按 #26 T5 聚成小时；官方价存 4 点算术平均，另算电量加权价供经济分析。
- 结算日、交易日、电量归属日三分（#26 T10）；日清/月结的主键用**电量归属日/月**，不拿结算日反推。

### C7 质量规则（F010 / A02）

六类规则，rule_id 固定，导入器直接执行：

| rule_id | 名称 | 判定 | 级别 | 定位 |
|---|---|---|---|---|
| Q01 | MISSING | 必需字段/应到时点为空 | BLOCK | dataset + business_key + field |
| Q02 | DUP | 同一业务键多条 | BLOCK | business_key |
| Q03 | RANGE | 数值/枚举/日期越界（slot 1..24、比例 0..1、价格为负等） | BLOCK | field |
| Q04 | SHIFT | 时标错位、表头列错位、日期与时段错配 | BLOCK | field |
| Q05 | UNIT | 单位缺失或不一致 | WARN | field + 源单位 + 换算率 |
| Q06 | LOGIC | 金额≠电量×价格（容差 0.01 元）、日历与工作日矛盾等 | BLOCK | 跨字段 |

- 质量记录表 `quality_issue(dataset, business_key, field, rule_id, severity, detail, batch_id, detected_at)`；BLOCK 行进隔离区 `quarantine_<dataset>`，WARN 行进主表并带 `quality_flag`。
- **A02 门槛**：完整率 = Σ有效时点 / Σ应到时段，逐实体逐日计算；≥99% 且异常全部可定位，才允许该批次发布。

### C8 敏感级别与出域（F050 / A08）

表级默认（字段可更严）：

| 级别 | 对象 | 出域（EXTERNAL） | 进模型提示词（MODEL） |
|---|---|---|---|
| PUBLIC | D01、D11、D12 | 原样 | 原样 |
| SENSITIVE | D13、D15、D16 | 脱敏（哈希/掩码/聚合 k≥5） | 白名单字段 + 脱敏摘要 |
| HIGHLY_SENSITIVE | D02、D03、D04、D05、D06、D07、D08、D09、D10、D14 | **拒绝** | 仅 k≥5 的聚合值，且不带主体标识 |

- 标识类字段（`*_id`）脱敏 = SHA-256 前 12 位；聚合要求覆盖 ≥5 个主体，防止小样本反推。
- 任何出域动作写审计日志（谁、何时、哪张表、哪些字段、允许/拒绝理由）；A08 要求高风险泄漏 0 项。

### C9 双时态与版本

- 规则文件、合同、额度都有版本：业务有效期 `valid_from/valid_to`（左闭右开）+ 系统登记 `recorded_at`。
- 查询默认 as-of：给定日期命中唯一版本；**同一日命中两个版本视为配置错误，直接拒绝**，不做「取最新」的隐式兜底。
- 规则条款（D01 子表）带 `clause_no`、`text_hash`，改一个字即新版本（#53 的可追溯要求）。

### C10 导入器协议（F007）

每个数据集一个 importer，先声明 manifest，再走四段流水线：

1. **PARSE**：按 `template_id + template_fingerprint`（表头列名与顺序的 SHA-256）认模板；指纹不符直接拒绝，**不猜列**。
2. **MAP**：源列 → 契约字段；声明 `time_basis`（`interval_end` 默认，按 #26 T3）、`unit_map`（kWh→MWh 等）、`encoding`、`decimal_sep`。
3. **VALIDATE**：执行 Q01–Q06；BLOCK 进隔离区，WARN 放行并登记。
4. **COMMIT**：一个批次一个事务，要么整批入主表，要么整批隔离；同一 `file_hash` 重跑走幂等。

### C11 字段字典与示例

- `contract.schema.json` 给出 16 类对象、160+ 字段的 name/type/unit/enum/required/confidence/desc。
- `samples.jsonl` 每类一行可执行示例，自检 E7 要求全量通过 BLOCK 级校验。
- 示例（D04 负荷，小时主栅格）：`{"user_id":"USER-4201-000123-X","business_date":"2026-11-04","slot_no":9,"interval_start":"2026-11-04T08:00:00+08:00","energy_mwh":1.234,"source_15min_count":4,"quality_flag":"OK"}`
- 示例（D09 日清）：`{"energy_date":"2026-11-04","slot_no":9,"subject_code":"S01-电能量","direction":"BUY","energy_mwh":1.234,"price":415.0000,"amount":512.11}`

### C12 缺口登记与置信度（Q02）

每个字段带 `confidence`：

- `RULE_DERIVED`：由一手规则/披露附表可直接推出（如日类型四值、披露层级）。
- `PLAN_DERIVED`：由方案 02-数据清单的核心字段推出（如合同品种、账户角色）。
- `TBD`：必须等客户样本/字段字典才能定稿；**导入器遇到 TBD 字段：保留原始列、不映射、登记缺口，不得按猜测填**。

当前 8 个 TBD 字段（自检 E8 自动清点）：`D02.industry_code`、`D03.metering_period`、`D04.tariff_segment`、`D05.curve_json`、`D09.subject_code`、`D10.subject_code`、`D10.retro_adjust_type`、`D12.tariff_segment_map_version`。

## 4. 十六类对象总表

| 编码 | 表 | 对象 | 敏感 | 业务键 | 核心字段 | 置信度 |
|---|---|---|---|---|---|---|
| D01 | d01_rule_doc | 市场规则文件 | PUBLIC | doc_id | doc_no、issuer、effective_from/to、status、url、file_hash | RULE_DERIVED |
| D02 | d02_user | 用户主数据 | HIGHLY | user_id | industry_code、region_code、voltage_level、status、contract_capacity_kw | PLAN_DERIVED |
| D03 | d03_meter | 计量点主数据 | HIGHLY | meter_id | user_id、metering_period、ct_ratio、pt_ratio、unit | PLAN_DERIVED |
| D04 | d04_load_hourly | 用户负荷曲线 | HIGHLY | user_id + business_date + slot_no | energy_mwh、observed_sum_mwh、source_15min_count、quality_flag | RULE_DERIVED |
| D05 | d05_wholesale_contract | 批发合同 | HIGHLY | contract_id + version | variety、period、total_energy_mwh、price_type、curve_json | PLAN_DERIVED |
| D06 | d06_retail_contract | 零售合同 | HIGHLY | contract_id + version | user_id、package_id、price_type、formula_text、term | PLAN_DERIVED |
| D07 | d07_declaration | 交易申报 | HIGHLY | batch_id + trade_date + variety + slot_no | energy_mwh、price、submitted_at、status | PLAN_DERIVED |
| D08 | d08_trade_result | 成交结果 | HIGHLY | trade_id | trade_date、slot_no、volume_mwh、price、cleared_at | PLAN_DERIVED |
| D09 | d09_daily_settlement | 日清数据 | HIGHLY | energy_date + slot_no + subject_code + direction | energy_mwh、price、amount、settle_date、rule_ref | RULE_DERIVED |
| D10 | d10_monthly_settlement | 月结数据 | HIGHLY | settle_month + subject_code + direction + retro_seq | energy_mwh、amount、retro_adjust_type、related_month | PLAN_DERIVED |
| D11 | d11_weather | 天气数据 | PUBLIC | region_code + valid_at | temperature_c、humidity_pct、weather_code、alert_level | RULE_DERIVED |
| D12 | d12_calendar | 节假日与交易日历 | PUBLIC | calendar_date | day_type（四值）、is_workday、makeup_for | RULE_DERIVED |
| D13 | d13_production_correction | 生产计划修正 | SENSITIVE | correction_id | user_id、business_date、type、impact_ratio | PLAN_DERIVED |
| D14 | d14_credit_limit | 履约与额度 | HIGHLY | participant_id + as_of | credit_limit、margin、guarantee、used、warning_line | PLAN_DERIVED |
| D15 | d15_user_account | 用户与权限 | SENSITIVE | account_id | role、org_code、permissions、status、mfa_enabled | PLAN_DERIVED |
| D16 | d16_model_call_log | 模型调用日志 | SENSITIVE | call_id | task、model、tokens、latency_ms、redacted_summary、trace_id | PLAN_DERIVED |

补充说明：

- **D01 子表** `d01_rule_clause(doc_id, chapter, clause_no, text, text_hash, applicable_subject, trade_type, time_scope)`，支撑 F002 条款结构化与 F003 可追溯问答。
- **D12 日类型四值**与 #26 T9 的中文措辞一一对应：普通工作日 = 工作日、普通休息日 = 周末、法定节假日 = 法定节假日、调休上班日 = 调休工作日；**代码以本契约的英文枚举为准**。
- **D04 的 `tariff_segment`** 只留字段与枚举，赋值规则等地图 U5（峰平谷 24 段）定稿后由后续票补；未定稿时为空，不阻塞负荷聚合。
- **D09/D10 的 `subject_code`** 当前是「客户账单科目对照表」占位（形如 `S01-电能量`），Q02 样本到位后枚举化。

## 5. 验收对照

| 票面验收项 | 证据 |
|---|---|
| 16 类对象每类有字段级定义与示例行 | schema 16 datasets / 160+ 字段；samples 16 行；E1、E7 |
| 主体编码可把同一主体不同写法归一 | `normalize_name` + 别名表；E2（3 种写法归一到同一 canonical_id） |
| 敏感级别与 F050 出域检查一致 | C8 矩阵 + `export_decision`；E3（高度敏感拒绝、公开放行、敏感脱敏、模型 k≥5） |
| 质量规则可被导入器直接执行 | C7 六条 rule_id + `validate()`；E4 六类各自命中 |
| Q02 缺口显式登记 | C12 + 8 个 TBD；E8 自动清点 |

## 6. 下游接口

本契约是地图第 3 层 12 张票的共同输入：它们只消费 schema，不各自定义字段。三个直接契约点：

1. **时序类**（负荷/日清/月结/申报/成交）：主键与时轴一律走 #26 的 `(entity_id, business_date, slot_no)`，不得自建日期格式。
2. **文本/规则类**（规则文件、模型日志）：走 `file_hash + clause_no` / `trace_id`，保证可追溯。
3. **敏感类**：任何出域（含提示词、附件、导出）先过 `export_decision`，再过脱敏，最后写审计。

## 7. 交给后续票的待定项（fog）

- Q02 样本到位后定稿 8 个 TBD 字段（尤其日清科目编码与合同曲线载体）。
- 分时电价 24 段映射（地图 U5）定稿后补 D04/D09 的 `tariff_segment` 赋值规则。
- 若客户合同曲线是长表（逐日逐时段）而非 JSON 列，D05 需拆子表 `d05_contract_curve`，主键 `(contract_id, version, year_month, slot_no)`。
- 15 分钟落地区的保留期与归档（证据层，非 16 类对象），等存储体量实测后定。
- D15 账号体系与公司 IAM/SSO 的字段映射，等权限方案（F046）细化。

## 8. 变更记录

- 2026-11-05 v1.0.0：首版。16 类对象、C1–C12、E1–E8 自检全 PASS。
