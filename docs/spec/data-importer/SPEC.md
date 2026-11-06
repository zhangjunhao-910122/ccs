# 数据导入器规格（data-importer）

> 本文档是 #24「实现：数据导入器（Excel/CSV/PDF/标准模板 + 幂等入库 + 校验报告）」的规格说明。
> 参考实现：[reference_importer.py](reference_importer.py)；样例生成：[make_samples.py](make_samples.py)；机器可读契约：[importer.schema.json](importer.schema.json)；验收回执：[verification.txt](verification.txt)。

## 1. 目的与范围

导入器负责把外部送来的原始文件（Excel / CSV / PDF / 标准模板）解析成内部标准记录，经过校验后幂等地写入主数据表，并产出一份可追责的校验报告与证据链。

本规格只描述导入通道本身。规则知识本体、API 对外契约、存储引擎实现分别在各自的规格里定义，这里只引用不重复。

## 2. 数据集全景：八类内部数据 ↔ 九个数据集

票面写的「八类内部数据」与仓库里的样例不是一一对应：合同一类拆成批发与零售两个数据集，规则文本 PDF 另作 PDF 通道验收件，外部通道三个数据集单列不计入八类。

| 类别 | 数据集 | 来源通道 | 样例文件 | 说明 |
|---|---|---|---|---|
| 主数据 | D02 | CSV / Excel | `d02_users.csv`、`d02_users.xlsx` | 用户与组织主数据 |
| 计量点 | D03 | CSV | `d03_meters.csv` | 计量点台账 |
| 负荷曲线 | D04 | CSV / 标准模板 | `d04_load_15min.csv`、`template_load_hourly_template.xlsx` | 15 分钟原始曲线，另有小时级标准模板 |
| 合同 | D05 + D06 | Excel | `d05_wholesale_contract.xlsx`、`d06_retail_contract.xlsx` | 批发与零售同属「合同」一类，拆两个数据集 |
| 交易申报 | D07 | CSV | `d07_declaration.csv` | 交易申报单 |
| 成交结果 | D08 | CSV | `d08_trade_result.csv` | 成交结果 |
| 日清 | D09 | CSV | `d09_daily_settlement.csv` | 日清算 |
| 月结 | D10 | CSV | `d10_monthly_settlement.csv` | 月结算 |

不计入八类的三个数据集：

| 数据集 | 来源 | 样例文件 | 不计入的原因 |
|---|---|---|---|
| D01 | PDF | `d01_rule_doc.pdf`、`d01_rule_doc_scanned.pdf` | 规则文本，只作 PDF 通道验收件；规则知识本体归 #63 规则知识库 |
| D11 | 外部 JSON | 天气数据 | 外部通道，按票面「范围」单列 |
| D12 | 外部 CSV | 日历数据 | 外部通道，按票面「范围」单列 |
| D13 | 外部 CSV | 生产计划 | 外部通道，按票面「范围」单列 |

`docs/spec/data-contract/contract.schema.json` 里的 D14 `d14_credit_limit`（HIGHLY_SENSITIVE）、D15 `d15_user_account`、D16 `d16_model_call_log` 不纳入本导入器范围。

## 3. 导入通道

| 通道 | 入口 | 关键约束 |
|---|---|---|
| Excel | `.xlsx` | 多 sheet 需指定目标数据集 |
| CSV | `.csv` | UTF-8，首行表头 |
| PDF | `.pdf` | 只取文本层，不做 OCR；无文本层按 I02 拦截 |
| 标准模板 | `.xlsx` | 模板指纹必须匹配，不符按 I01 整批拒绝 |
| 外部通道 | JSON / CSV | 只读、只入参考表；不可用时降级 W09，不牵连主表 |

## 4. 固定六步流水线

每一次导入都走同一条流水线，顺序不可调换：

```
PARSE → MAP → VALIDATE → COMMIT
```

- **PARSE**：读文件、抽文本层或表格、抽模板指纹。
- **MAP**：按 `contract.schema.json` 把原始列映射到标准字段；字段类型口径以该文件为准，D01–D16 字段与类型唯一口径在此维护。
- **VALIDATE**：跑质量规则 Q01–Q06 与文件级规则 I01–I06，产出问题清单。
- **COMMIT**：按批次写入主表或隔离区，写血缘边，登记批次台账。

换源只换适配器：适配器负责把某个源的原始数据变成标准形状，流水线本身不随源变。

## 5. 质量规则与严重度

| 规则 | 含义 | 严重度 | 处置 |
|---|---|---|---|
| Q01 | 必填字段缺失 | BLOCK | 整批拒 |
| Q02 | 主键重复 | BLOCK | 整批拒 |
| Q03 | 数值越界 | BLOCK | 整批拒 |
| Q04 | 时标偏移 | WARN | 入库并带 conflict_flag |
| Q05 | 单位不一致 | WARN | 换算后入库并带 conflict_flag |
| Q06 | 逻辑校验失败 | BLOCK | 整批拒 |
| I01 | 标准模板指纹不符 | BLOCK | 整批拒 |
| I02 | PDF 无文本层或解析不出 label: value 结构 | BLOCK | 整批拒（不做 OCR） |
| I03 | 单位字段非法 | BLOCK | 整批拒 |
| I04 | 必填列缺失 | BLOCK | 整批拒 |
| I05 | 批次行数超限 | BLOCK | 整批拒 |
| I06 | 脏样例：逻辑冲突 | BLOCK | 整批拒 |
| W09 | 外部通道不可用 | WARN | 降级，不牵连主表 |

BLOCK 规则的处置是整批 0 行进主表、原文进隔离区；WARN 规则的记录照常入库，并带 `conflict_flag`。

## 6. ImportReport（14 字段）

每次导入产出一份报告，字段固定为：

```
source_id, dataset, batch_id, file_hash, status,
rows_read, rows_committed, rows_quarantined, quality_issues,
registry_version, importer_version, started_at, finished_at, evidence_path
```

`status` 取值：COMMITTED / QUARANTINED / FAILED。`evidence_path` 指向本次导入的证据目录 `_evidence/`，其中的回执 JSON 可逐项复查。

## 7. 幂等

幂等键是 `(dataset, file_hash)`。同一数据集下同一文件哈希重复导入时，不重复写入主表，报告状态记 DUPLICATE，已入库行数不变。哈希怎么算、批次怎么登记，见 `docs/spec/data-source-registry/SPEC.md`。

## 8. 隔离区与人工回补

- BLOCK：整批 0 行进主表，原文进隔离区，状态 QUARANTINED。
- WARN：正常入库，带 `conflict_flag`。
- 隔离区里的记录修好后，人工确认回补，过程留痕（回补人、回补时间、原批次号），见 `docs/spec/data-storage/SPEC.md`。

## 9. 血缘

两种边，缺一不可：

- `file:<hash>#<row>` → `row:<record_id>`：这条记录来自哪个文件的第几行。
- `batch:<id>` → `row:<record_id>`：这条记录属于哪个批次。

行号口径见 `docs/spec/data-storage/SPEC.md`。

## 10. 单位换算与时标

- 单位换算走 Q05：换算后入库并带 `conflict_flag=1`，记录标记 ESTIMATED。
- 时标默认用 `interval_end`；15 分钟数据按电量求和聚成小时，缺失不插值，见 `docs/spec/time-grid/SPEC.md`。

## 11. 标准模板

标准模板带指纹，导入时先比对；不符即按 I01 整批拒绝，并提示应使用哪个模板。合格模板与不合格模板各有一份样例：`template_load_hourly_template.xlsx`、`template_load_hourly_bad.xlsx`。

## 12. PDF 通道

- 有文本层：抽取 `label: value` 结构后入库（验收项 E10）。样例 `d01_rule_doc.pdf`。
- 无文本层：按 I02 拦截（扫描件不产 OCR），是设计内的负例，不是失败（验收项 E10 的另一半）。样例 `d01_rule_doc_scanned.pdf`。

PDF 文本层提取只取 `Tj` 文本，不做 OCR，见 `reference_importer.py` 中的最小提取实现。

## 13. 外部通道降级

外部通道（天气 / 日历 / 生产计划）不可用时记 W09，写空值或跳过该通道，主表不受影响。外部通道只有天气、日历与规则文本是匿名可得的，见 `docs/research/data-source-reachability.md`。

## 14. 验收对照

参考实现的 `--verify` 覆盖 E1–E12，逐项对应票面五条验收标准：

| 编号 | 验收内容 |
|---|---|
| E1 | 八类内部数据与外部通道各有可跑样例 |
| E2 | 重复导入幂等，行数不变 |
| E3 | 质量报告能定位异常行与异常类型 |
| E4 | 异常记录只进隔离区，不进主表 |
| E5 | 血缘能回答来自哪个文件、哪个批次 |
| E6 | 单位换算与冲突标记 |
| E7 | as-of 版本替代 |
| E8 | 人工回补留痕 |
| E9 | 标准模板指纹校验 |
| E10 | PDF 文本层入库；无文本层按设计拦截 |
| E11 | 外部通道降级不牵连主表 |
| E12 | ImportReport 14 字段齐全且证据可查 |

实际运行结果见 `verification.txt`，逐条证据见 `_evidence/`。

## 15. 留白

- `contract.schema.json` 里 8 个 TBD 字段保持原样直通，本导入器不填、不猜。
- `samples/zz_probe_02.xlsx`、`samples/zz_probe_03.xlsx` 是过程探测残留，保留在仓库里但不在验收范围内。
- `apps/` 为空：导入器当前是规格 + 参考实现，没有对外应用层。
- 企业数据库与 API 接入属 #73，不在本规格内。
