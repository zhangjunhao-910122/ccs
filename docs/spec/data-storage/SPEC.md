# 业务数据存储层规格（D01–D16）

- 票：[实现：业务数据存储层（D01–D16 十六类数据对象）](https://github.com/zhangjunhao-910122/ccs/issues/25)（#25，父地图 #22）
- 版本：v1.0（2026-11-05）
- 上游：[统一数据契约（#33）](../data-contract/SPEC.md)、[统一时间栅格（#26）](../time-grid/SPEC.md)、[数据源注册表与导入器接口（#44）](../data-source-registry/SPEC.md)、[认证与六角色权限矩阵（#29）](../auth-rbac/SPEC.md)
- 可执行产物：`storage.schema.json`（物理策略）、`reference_store.py`（SQLite 参考实现 + E1–E8 自检）、`object-map.csv`（契约字段到物理列的逐列映射）、`verification.txt`（自检输出）

## 1. 问题与目的

契约（#33）定义了 D01–D16 十六类数据对象的字段、编码、质量与敏感级别；导入器接口（#44）定义了数据怎样进来；时间栅格（#26）定义了小时/日/月怎样上卷。缺的是中间那一层：**数据落在哪里、以什么形态落、怎样保证同一批只进一次、脏数据不污染查询、每个数能追回来源**。

本规格把这层定死为「一对象一表 + 公共血缘列 + 批次台账 + 隔离区 + 可重建派生表」，并给出可运行的 SQLite 参考实现。明确不采用「统一栅格表」抽象：十六类对象的生命周期、主键、保留期和查询模式都不同，压进一张宽表只会把差异变成空列和特例。

## 2. 第一性原理

1. **事实的最小单元是观测，不是格子。** 一条负荷是「某用户某小时的电量」，一条结算明细是「某主体某时段某科目的金额」；栅格是查询时的时间视图（#26），不是存储形态。
2. **生命周期决定物理归属。** 规则与合同保存 10 年、负荷与结算 7 年、天气 3 年、模型日志 1 年；保留期与敏感级不同的数据不应共表。
3. **派生物必须可重建。** 日/月汇总只能由小时事实重算得到，且重算结果逐字段相等；否则「以小时表为唯一事实来源」只是口号（#26 T15）。
4. **不可变 + 时间窗是版本的唯一表达。** 一行数据一旦入库不再原地改；变更以新行 + `valid_from/valid_to/is_current` 表达，任何历史时点可 as-of 查回。
5. **隔离与主表物理分离。** 被阻断的批次一行也不进主表，但原始行必须可查、可重放、可修；「拒绝」不等于「丢弃」。
6. **敏感级别必须可执行。** 敏感级别不能只写在文档里，要落到列级策略表，并让导出函数在行级直接拒绝高度敏感表。
7. **可移植优先。** 阶段一 SQLite、阶段二 PostgreSQL，只用两者都支持的类型与 SQL；DDL 由策略文件生成，不手写第二套。

## 3. 决策（S1–S12）

- **S1 一对象一表。** 16 个主表 + D01 条款辅助表；表名固定为 `d01_rule_doc` … `d16_model_call_log`。不建统一栅格表、不建 EAV 表、不为「以后可能加字段」预留 JSON 大列。
- **S2 decimal 存整数最小单位。** 电量/电量类 `volume` 3 位、价格 4 位、金额 2 位、比例 4 位、温度/湿度 1 位，其余默认 4 位；应用层用 `Decimal` 换算。禁止 `REAL/FLOAT`，避免 0.1+0.2 类误差进入结算。
- **S3 公共列加 `src_` 前缀。** 契约 12 个公共列（batch_id/source_system/source_file/source_row_no/file_hash/imported_at/importer_version/rule_version/valid_from/valid_to/is_current/owner）物理名统一为 `src_*`，避免与业务字段同名（D07 自身就有 `batch_id`）。
- **S4 行身份。** `record_id = sha256(dataset | 业务键值 | valid_from)`；`current_key = 业务键去掉 version 后的规范 JSON`；部分唯一索引 `(dataset, current_key) WHERE src_is_current=1` 保证「同一业务键只有一个当前版本」。
- **S5 批次幂等。** `ingest_batch` 以 `(dataset, file_hash)` 唯一；同文件同数据集重复导入返回 `DUPLICATE` 与原 `batch_id`，一行不写。
- **S6 整批事务。** 任一行 BLOCK → 该批 0 行进主表、全部原始行进 `quarantine_row`；WARN → 入主表但 `quality_flag` 不得为 OK 且 `conflict_flag=1`。隔离保留 180 天。
- **S7 质量语义。** `OK/ESTIMATED` 可参与聚合；`PARTIAL/SUSPECT/MISSING` 不进 `v_*_valid` 视图。质量不插值、不补零（#26 T7）。
- **S8 血缘边。** `lineage_edge(from_ref, to_ref, transform_id, run_id)`；导入写 `file:<hash>#<row> → row:<record_id>`（IMPORT）与 `batch:<id> → row:<record_id>`（ASSIGN_BATCH）；日汇总写 `row:<小时> → agg:<user>:<date>:<run>`（ROLLUP_HOUR_DAY）。
- **S9 as-of 查询。** 以 `src_valid_from <= t AND (src_valid_to IS NULL OR src_valid_to > t)` 选版本，按 `valid_from` 倒序取一条；`is_current` 只用于快速过滤，不能用来判断历史。
- **S10 派生表可重建。** `d04_load_daily` / `d04_load_monthly` 由小时表重算，带 `rebuild_run_id`；重算前按 run 删除，重算后与小时表求和逐字段相等。小时表是唯一事实来源，日/月表可随时丢弃。
- **S11 列级敏感策略。** `column_policy(table_name, column_name, sensitivity, mask_rule)` 逐列登记；导出函数 `export_rows` 对 HIGHLY 表直接抛 `HIGHLY_SENSITIVE`，只允许 `k>=5` 聚合结果出域。PUBLIC/SENSITIVE 表提供 `v_*_masked` 视图，HIGHLY 表不提供行级视图。
- **S12 引擎可移植。** 阶段一 SQLite 3.35+（WAL、`foreign_keys=ON`、`busy_timeout=5000`），阶段二 PostgreSQL 16+；只用标准类型（TEXT/INTEGER）、标准索引与部分索引；DDL 由 `storage.schema.json` 生成。

## 4. 表清单

| # | 对象 | 物理表 | 业务键 | 保留期 | 敏感级 |
|---|------|--------|--------|--------|--------|
| D01 | 规则文件 | `d01_rule_doc`（+ `d01_rule_clause`） | doc_id, version | 10y | PUBLIC |
| D02 | 用户 | `d02_user` | user_id, valid_from | 7y | HIGHLY |
| D03 | 计量点 | `d03_meter` | meter_id, valid_from | 7y | HIGHLY |
| D04 | 用户负荷小时 | `d04_load_hourly`（+ daily/monthly 派生） | user_id, business_date, slot_no | 7y | HIGHLY |
| D05 | 批发合同 | `d05_wholesale_contract` | contract_id, version | 10y | HIGHLY |
| D06 | 零售合同 | `d06_retail_contract` | contract_id, version | 10y | HIGHLY |
| D07 | 申报 | `d07_declaration` | declaration_id | 7y | HIGHLY |
| D08 | 成交结果 | `d08_trade_result` | trade_id | 7y | HIGHLY |
| D09 | 日清结算 | `d09_daily_settlement` | settle_id | 7y | HIGHLY |
| D10 | 月结结算 | `d10_monthly_settlement` | settle_id | 10y | HIGHLY |
| D11 | 天气 | `d11_weather` | region_code, valid_at | 3y | PUBLIC |
| D12 | 日历 | `d12_calendar` | calendar_date | 10y | PUBLIC |
| D13 | 生产修正 | `d13_production_correction` | correction_id | 7y | SENSITIVE |
| D14 | 信用额度 | `d14_credit_limit` | participant_id, as_of | 7y | HIGHLY |
| D15 | 用户账户 | `d15_user_account` | account_id | 2y | SENSITIVE |
| D16 | 模型调用日志 | `d16_model_call_log` | call_id | 1y | SENSITIVE |

公共表：`ingest_batch`（批次台账）、`quarantine_row`（隔离行）、`lineage_edge`（血缘）、`column_policy`（列级敏感策略）、`schema_version`（存储版本）。

> 字段总数以契约 schema 为准：当前 **143** 个业务字段（#33 交付时口述的 141 是旧计数，D01 后补了 2 个字段；本规格以 `contract.schema.json` 实际内容为准）。逐列映射见 `object-map.csv`，共 143 + 12×16 = 335 行（另加内部列）。

## 5. 入库流程

1. 导入器按 #44 完成解析、映射、校验，产出规范化行与 `file_hash`。
2. `import_batch` 先查 `ingest_batch(dataset, file_hash)`；命中即 `DUPLICATE`，返回原批次，不重复写。
3. 逐行校验：必填、枚举、类型、decimal 位数、`slot_no ∈ [1,24]`；D04/D07/D09 还校验 `interval_start == business_date + (slot_no-1) 小时`（错误码 `SLOT_INTERVAL`）。
4. 批内业务键重复 → `DUP_IN_BATCH`；当前版本已存在且 `record_id` 不同 → `CURRENT_CONFLICT`。
5. 有任何错误：整批写 `ingest_batch(BLOCKED)`，原文逐行写 `quarantine_row`，主表零行。
6. 无错误：写 `ingest_batch(LOADED)` → 写主表 → 写两条血缘边，全部在一个事务内提交。

## 6. 查询、版本与派生

- **当前值**：`v_<table>_valid`（`src_is_current=1` 且质量可聚合）。
- **历史值**：`query_as_of(keys, t)`，返回 `valid_from <= t < valid_to` 的版本；合同类以 `version` 递增、`is_current` 只标最新。
- **血缘**：`trace_lineage(ref, depth)` 沿 `to_ref → from_ref` 回溯，可从一个日汇总追到 24 条小时行、再到文件行号与批次。
- **派生**：`rebuild_daily/rebuild_monthly` 从小时表重算，只取 `energy_mwh IS NOT NULL` 且质量 `OK/ESTIMATED` 的行；每次重算生成新 `rebuild_run_id`，旧 run 可按需清理。

## 7. 敏感与导出

- HIGHLY：不提供行级视图、不提供 `v_*_masked`；`export_rows` 直接拒绝；只允许 k≥5 的聚合查询出域（#29 AU 规则）。
- SENSITIVE：提供 `v_*_masked` 视图，标识类列按 #29 掩码规则处理。
- PUBLIC：可导出。
- 所有导出都经 `column_policy` 判定，不以表名硬编码。

## 8. 验收对照

| 票面验收 | 本规格对应 | 证据 |
|---|---|---|
| ① D01–D16 全部有存储对象与字段来源说明 | 第 4 节表清单 + `object-map.csv`（335 行逐列映射） | E1 PASS：16 类 / 143 字段 / 16 样例 / 399 条列策略 |
| ② 同批次重复导入行数不变 | S5 + `ingest_batch` 唯一键 | E2 PASS：首次 LOADED、重复 DUPLICATE、行数只 +1 |
| ③ 脏数据隔离可查不污染查询 | S6 + `quarantine_row` + `v_*_valid` | E3 PASS：整批 BLOCKED、主表行数不变、隔离行可读且含原始 `slot_no` |
| ④ 血缘能答「从哪来、经哪几道加工」 | S8 + `trace_lineage` | E4 PASS：小时行可追到 file#row 与 batch；日汇总可追到小时行 |
| 时标与当前版本一致性 | 第 5 节校验 | E5 PASS：`interval_start` 错配 → `SLOT_INTERVAL`；重复当前键 → BLOCKED |
| 列级敏感可执行 | S11 | E6 PASS：D04 25 列 = 25 条策略、导出被拒；D11 可导出 |
| 版本可 as-of 查回 | S4/S9 | E7 PASS：同一合同 2025-06 查回 v1、2026-06 查回 v2 |
| 派生可重建 | S10 | E8 PASS：小时 = 日 = 月 = 重建后 = 1234（样例电量） |

复现命令：

    J:/DSH/data/dsh-runtimes/dsh-primary-runtime/dependencies/python/python.exe -X utf8 docs/spec/data-storage/reference_store.py --write-object-map docs/spec/data-storage/object-map.csv
    J:/DSH/data/dsh-runtimes/dsh-primary-runtime/dependencies/python/python.exe -X utf8 docs/spec/data-storage/reference_store.py --verify

## 9. 下游接口

- **组合负荷预测（#45）**：读 `v_d04_load_hourly_valid`，按 `user_id, business_date, slot_no` 取数；不直接读原始表。
- **结算复现与对账（#55）**：读 `d09_daily_settlement` / `d10_monthly_settlement`，用 `valid_from` 做 as-of；金额与电量按字段类型还原 Decimal。
- **对外 API 与页面数据契约（#46）**：只经 `export_rows`/聚合查询取数，敏感判定交给 `column_policy`。
- **风控与人工审批（#60）**：读 `d16_model_call_log` 做审计；审批意见不写模型日志表。
- **认证与权限（#29）**：角色权限决定「能看哪张表」，`column_policy` 决定「能看哪一列」，两者正交。

## 10. 尚未定的事（fog）

- 分区与归档：7 年负荷数据在 SQLite 单文件下的容量上限、按年归档策略未定；阶段二 PostgreSQL 原生分区待迁移时再定。
- 增量同步：阶段二数据库直连的游标字段、断点续传与去重边界未定（#44 R10 只定了只读账号与游标增量）。
- 并发写：多导入器同时写同一数据集时的锁粒度与重试策略未压测。
- 字段数差异：143 与 #33 口述 141 的 2 个字段需在契约变更记录里补一条说明，避免下游引用旧数。
- 匿名阈值：k≥5 是 #29 的默认值，是否需要按数据集配置未定。
- 备份与恢复：SQLite 在线备份与隔离区重放的演练脚本未写。

## 11. 变更记录

- v1.0（2026-11-05，#25）：首版；16 张主表 + 5 张公共表 + D01 条款辅助表；E1–E8 全 PASS。
