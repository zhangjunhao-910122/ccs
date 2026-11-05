# 规格：数据源注册表与导入器接口 v1.0

- 票号：#44（父地图 #22「湖北省售电公司交易员训战一体化平台」）
- 类型：wayfinder:task（AFK）；上游依赖：#26 时间栅格、#33 统一数据契约（均已关闭）
- 状态：v1.0，2026-11-05
- 配套文件：`registry.schema.json`、`samples.registry.json`、`reference_registry.py`、`verification.txt`

## 1. 这张票要定的事

任何数据进入平台之前，必须先在**数据源注册表**里有一个条目，并通过**导入器接口**的固定六步流水线入库。本规格定死：

1. 注册表条目有哪些字段、哪些值合法；
2. 三类源（文件 / 数据库 / API）与一类实时只读通道分别允许在哪个阶段启用；
3. 导入器接口的方法签名与流水线步骤，保证换源只换适配器、下游契约不变；
4. 凭据、只读、幂等、失败隔离的硬约束。

## 2. 第一性原理

1. **采集与消费解耦**：下游只认识契约（#33 的 D01–D16）与导入报告，不认识某个源。源会换（Excel 换 SFTP、人工换接口），契约不该跟着动。
2. **默认拒绝（allowlist）**：未注册的源不能入库，不是"注册表用来查"，而是"没有条目就没有入口"。
3. **只读是能力约束，不是纪律**：接口层不提供写方法，注册表里写能力直接判非法；不靠人记得别写交易系统。
4. **凭据与配置分离**：仓库只存引用名，真值在企业侧环境/密钥库；仓库里的明文密钥视为事故。
5. **阶段是风险门槛，不是技术分类**：同一个 API，公开匿名的可以一阶段接，企业内部授权的要等二阶段。判据是"客户授权 + 敏感级别 + 只读性"，不是协议。
6. **失败整批回滚**：一条脏数据不能污染半天数据；批次事务 + 隔离区 + 幂等键，重放不产生重复事实。

公理：

- **R-A1** 一个数据事实只有一个入口，入口由 `source_id` 标识；同一 `source_id` 的 schema 变更视为新版本，不静默改语义。
- **R-A2** 注册表是配置数据，可版本化、可审计、可回滚；导入批次永远记录当时的 `registry_version`。
- **R-A3** 映射只做"源列名 → 契约字段名"，不做业务计算；计算属于下游，映射里出现公式即违规。
- **R-A4** 无法映射的列进 `extras` 原样保留，不猜、不丢；契约里 `confidence=TBD` 的字段不映射（#33 C12）。
- **R-A5** 一切通道对交易系统只读；写能力在系统设计上不存在，而不是默认关闭。
- **R-A6** 凭据只以引用形式出现在仓库配置里，取值发生在运行时、由部署环境注入。

## 3. 规范

### R1 注册表条目

一个条目一个 JSON 对象，必填字段：

| 字段 | 说明 |
|---|---|
| `source_id` | 唯一标识，`SRC-<INT\|EXT>-<大写短名>`，如 `SRC-INT-LOAD-XLSX` |
| `title` | 人读名称 |
| `channel` | `INTERNAL`（企业内部数据）/ `EXTERNAL`（外部特征通道） |
| `transport` | `FILE` / `DB` / `API` / `STREAM` |
| `availability` | `{since_stage: S1\|S2\|S3, anonymous: bool}` |
| `datasets` | 写入的契约数据集键，如 `["D04"]` |
| `sensitivity` | 条目敏感级别，必须 ≥ 所写数据集里最高的一级 |
| `cadence` | `ONCE` / `DAILY` / `WEEKLY` / `MONTHLY` / `REALTIME` |
| `mapping` | `{template_fingerprint, time_basis, unit_map, columns, pending_confirmation}`，见 R5 |
| `capabilities` | 只能是 `READ` / `LIST` / `STREAM` 的子集 |
| `writes_to_trading_system` | 必须为 `false` |
| `failure_policy` | `{on_block: QUARANTINE_BATCH, max_retries: 0–5, backoff_seconds, alert}` |
| `owner` | 数据责任人（角色/工号，不写个人姓名以外的敏感信息） |
| `registry_version` | 语义版本，映射变更必须升版 |
| `lifecycle` | `DRAFT` / `ACTIVE` / `DEPRECATED` / `RETIRED` |

可选：`credentials_ref`（R7）、`notes`、`stage_note`（阶段说明，如"二阶段接企业结算库"）。

### R2 三类源与阶段矩阵

`since_stage` 必须等于该 transport 在给定 channel 下的**最早可用阶段**：

| transport | channel / 条件 | 最早阶段 | 理由 |
|---|---|---|---|
| `FILE` | 任意 | **S1** | 方案一阶段就是 Excel/CSV/PDF 导入 |
| `DB` | 任意 | **S2** | 需要企业侧只读账号与网络策略 |
| `API` | `EXTERNAL` 且 `anonymous=true` | **S1** | 公开天气/日历，无凭据、无写入面 |
| `API` | 其他（企业内部接口、需授权的外部接口） | **S2** | 有凭据与授权边界 |
| `STREAM` | 任意 | **S3** | 实时数据是影子运行的辅助输入 |

这条矩阵的用途是**登记时判非法**：把企业内部 DB 标成 S1 会被校验器拒绝，而不是等上线才发现没权限。三阶段不等于"到 S3 才可用"，S1 的源在 S2/S3 继续可用。

### R3 只读闸

1. `capabilities` 中出现 `WRITE` / `UPDATE` / `DELETE` / `SUBMIT` 即判非法；
2. `writes_to_trading_system` 必须为 `false`，缺失也判非法；
3. 适配器接口（R4）不声明任何写方法；`STREAM` 适配器只能订阅/轮询只读端点；
4. 校验器对每条登记做静态检查，导入器运行时再做一次 `assert entry["writes_to_trading_system"] is False`（双重闸）。

### R4 导入器接口

导入器实现一个固定协议（Python `Protocol`，语言无关时按同名方法约定）：

```python
class Importer(Protocol):
    importer_version: str
    def describe(self) -> SourceManifest: ...      # 指纹、时标、单位表、字段映射
    def parse(self, ctx: ImportContext) -> Iterator[RawRecord]: ...
    def map(self, raw: RawRecord) -> CanonicalRow: ...   # 只做列名映射与单位换算
    def validate(self, rows: Iterable[CanonicalRow]) -> ValidationResult: ...
    # commit 由框架实现，适配器不直接写库
```

框架提供模板方法 `run()`，步骤固定、顺序不可变：

1. **批次登记**：`(dataset, file_hash)` 查重，重复则直接返回上一次的回执（幂等）；
2. **解析**：适配器逐条产出原始记录；
3. **映射**：按注册表 `mapping.columns` 转成契约字段，未映射列进 `extras`；
4. **质量校验**：必填、枚举、范围（`slot_no∈1..24`）与 #33 的 Q01–Q06；
5. **幂等入库**：整批一个事务，任一 BLOCK 则整批进隔离区，已入库 0 行；
6. **血缘与报告**：写 12 个公共血缘列（#33 C3），产出 `ImportReport`。

`ImportReport` 是下游唯一依赖的导入产物，字段固定：

```
source_id, dataset, batch_id, file_hash, status(COMMITTED|QUARANTINED|DUPLICATE|FAILED),
rows_read, rows_committed, rows_quarantined, quality_issues[],
registry_version, importer_version, started_at, finished_at, evidence_path
```

换源 = 新写一个适配器 + 注册表新增/改一行 `source_id`；`ImportReport` 与目标数据集不变，下游零改动。

### R5 字段映射

- `mapping.columns` 是 `{源列名: 契约字段名}`；目标字段必须存在于该条目 `datasets` 声明的数据集里，否则登记即拒；
- `mapping.time_basis` ∈ `interval_end`（默认）/ `interval_start`，接 #26 T3；
- `mapping.unit_map` 声明源单位到契约单位的换算，如 `{"kWh": "MWh"}`；换算系数由导入器库统一维护，不在注册表里写公式；
- `mapping.template_fingerprint` 记录模板/表头指纹，模板变了不自动适配，必须显式升 `registry_version`；
- 契约中 `confidence=TBD` 的字段（8 个，见 #33）**允许直通映射，但必须在 `mapping.pending_confirmation` 里声明**：源值原样搬运、不做编码归一/枚举校验，导入报告记 `TBD_PASSTHROUGH:<字段>`，批次照常入库。禁止的是"猜语义"（自行编行业码、科目码的对照表），不是禁止搬运；声明了非 TBD 字段则判非法。Q02 样本到位后补对照表、升 `registry_version`，再撤下待确认声明。

### R6 幂等与失败处理

- 幂等键：`(dataset, file_hash)`；行级幂等键沿用 #33 C4 `(dataset, file_hash, row_no)`；
- 同一文件重复导入返回 `status=DUPLICATE` 与首次 `batch_id`，不新增行；
- 任一行 BLOCK → 整批 `QUARANTINED`，隔离区保留原件与失败原因，人工处置后重新导入（新批次）；
- 自动重试只针对**可重试故障**（网络超时、文件未到），`max_retries` 上限 5，默认 3，指数退避 `[30, 120, 600]` 秒；校验失败不重试；
- 告警级别 `WARN`（可继续）/ `ERROR`（需人工介入）。

### R7 凭据与企业侧配置

- `credentials_ref` 只接受三种前缀：`ENV:`（环境变量）、`VAULT:`（密钥库键名）、`FILE:`（部署机上的挂载文件路径，仅限企业内网部署）；
- 需要凭据的条目：`DB`、需授权的 `API`、`STREAM`；`EXTERNAL+anonymous` 的 API 与 `FILE` 不需要；
- 仓库里出现明文密钥、连接串、token 即判违规；公开导出注册表时 `credentials_ref` 一律脱敏为 `***`；
- 连接参数（host/port/库名/账号）不进仓库，由企业侧部署配置注入；换环境不改注册表。

### R8 生命周期与版本

- `DRAFT`（可登记、不可导入）→ `ACTIVE`（可导入）→ `DEPRECATED`（仍可读历史，禁止新批次）→ `RETIRED`（只保留元数据）；
- 任何映射/时标/单位变化都升 `registry_version`；历史批次保留导入时的版本号，可追溯；
- 停用源不删条目（审计需要），置 `DEPRECATED` 并在 `notes` 写替代源。

### R9 外部特征通道

天气（D11）、节假日日历（D12）、生产计划修正（D13）、规则文件（D01）走 `channel=EXTERNAL` 或独立 `source_id` 注册，与内部计量/结算数据**分开登记**：外部源可匿名、可降级（缺报不阻塞结算），内部源缺报按 A02 完整率门槛处理。两类源在注册表里用 `channel` 区分，报表与权限按 channel 分组。

### R10 二阶段：企业数据库与内部 API

- 只读账号 + 最小权限（仅 SELECT / 只读视图）；
- 增量同步以游标（`updated_at` 或自增 id）为准，注册表 `cadence` 至少 `DAILY`；
- 超时、连接池、限流参数由部署配置给出，注册表只声明 `transport=DB/API` 与频率；
- 二阶段接入不改变数据集契约，只新增 `source_id`。

### R11 三阶段：实时只读

- `STREAM` 条目只声明订阅/轮询的只读端点与限流，不落原始 tick，只落聚合后的小时事实（接 #26 主栅格）；
- 断线重连、补偿窗口（默认 5 分钟）、限流（默认 ≤1 次/秒）由适配器实现，注册表声明 `cadence=REALTIME` 与 `failure_policy`；
- 实时通道的结论只用于辅助决策与影子运行，不直接触发申报（方案一阶段不建自动申报的延续）。

### R12 Q05 缺口

客户侧接口数量与技术条件（Q05）尚未确认：`DB`/`API` 的具体条数、网络策略、授权方式待补。本规格只定结构与校验规则，**不预设条数**；Q05 确认后只增注册表条目，不改接口。

## 4. 验收对照

| 票面验收 | 落点 | 证据 |
|---|---|---|
| 三类源可登记并标阶段 | R1/R2 + samples 8 条 | E1、E2 |
| 接口签名稳定、新增源不影响下游 | R4 + ImportReport 固定字段 | E6 |
| 二/三阶段凭据在企业侧、不进仓库 | R7 | E4 |
| 任何通道无写交易系统能力 | R3 | E3 |
| 接口数量与技术条件对应 Q05 | R12 | 票面备注 + samples 不含具体条数 |

## 5. 下游接口

- 数据契约（#33）：映射目标字段的唯一来源；TBD 字段直通并在 `pending_confirmation` 声明；
- 时间栅格（#26）：`time_basis` 与 15min→小时聚合的落点；
- 校验规则（后续票）：Q01–Q06 由导入器调用，规则版本写进血缘列；
- 结算/回放/预测：只读 `ImportReport` 与契约表，不感知源。

## 6. Fog（留给后续票）

- 企业侧密钥库选型（VAULT 具体产品）待 IT 环境确认；
- 文件到达通知方式（SFTP/邮件/人工上传）影响 `cadence` 的实现，待客户流程确认；
- Q05 接口清单；
- 实时通道的补偿窗口与限流值需按实际行情频率调参。

## 7. 变更记录

- v1.0 2026-11-05：首版，随 #44 交付。