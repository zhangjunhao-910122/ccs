---
title: 标准版本画像字段规范与换版影响
ticket: "#16 R4"
status: 字段规范已定稿；ISO 14001:2026 撤回线索待一手核实
updated: 2026-11-05
---

# 标准版本画像字段规范与换版影响

> 用途：审核点库（#2 S1）的 `registry.yaml` 标准画像注册表的字段依据；换版时如何迁移审核点的操作规程。

## 0. 一句话结论

标准画像必须显式携带「版本身份 + 条款树 + 换版映射」三件套，否则换版后审核点库无法判定哪些审核点失效、哪些可沿用；
现网关键事实：**CNCA-QMS-01:2025 的认证依据仍是 GB/T 19001/ISO 9001（2015 版）**，
而 ISO 9001:2026 已于 2026-09-16 发布（晚于规则实施日 2026-01-01），换版切换尚未在认证规则层落地 —— 
因此本项目当前库以 **2015 版为 current 画像**，并把 2026 版建为 **pending 画像**。

## 1. 现行版本状态表

| 标准 | 版本 | 状态 | 依据与说明 |
|---|---|---|---|
| GB/T 19001 | 2016 | 现行（等同采用 ISO 9001:2015） | CNCA-QMS-01:2025 以其为认证依据 |
| ISO 9001 | 2026（第 6 版） | 已发布，未切换 | 标准号 88464，2026-09-16 发布，取代 ISO 9001:2015 |
| ISO 9001:2015/Amd 1:2024 | — | 已撤回 | 2026-09-16 随第 6 版发布撤回 |
| GB/T 19001 修订计划 | 20264412-T-469 | 进行中 | TC151 归口，中国标准化研究院牵头；拟等同采用 ISO 9001:2026，代替 GB/T 19001-2016；2027-02-28 前报批；征求意见截止 2026-10-21 |
| GB/T 24001 | 2016 | 现行 | 修订计划 20261483（线索，未一手核实） |
| ISO 14001 | 2015 | 疑于 2026-04-15 撤回（Stage 95.99），由 ISO 14001:2026 取代 | **线索，未一手核实**，见 §4 |
| GB/T 45001 | 2020 | 现行 | 代替 GB/T 45001-2013；对应 ISO 45001:2018 |
| GB/T 19011 | 2021 | 现行 | 代替 GB/T 19011-2013；审核指南 |
| CNCA-QMS-01 | 2025 | 现行（2025-08-31 发布 / 2026-01-01 实施） | 见 docs/research/multi-system-audit-research.md |
| CNCA-EMS-01 | 2026 | 现行（2026-01-14 发布 / 2026-03-01 实施） | 同上 |
| CNCA-OHSMS-01 | 2026 | 现行（2026-01-14 发布 / 2026-03-01 实施） | 同上 |

## 2. 标准画像注册表字段规范

`registry.yaml` 中每个 domain（quality / environment / ohs）可登记多条画像，字段如下：

```yaml
profiles:
  - profile_id: qms-gbt19001-2016        # 稳定主键，不含版本，版本另立字段
    domain: quality                     # quality | environment | ohs
    standard_no: "GB/T 19001"           # 国标号
    iso_standard_no: "ISO 9001"         # 对应 ISO 号
    edition: "2016"                    # 版本年号
    iso_edition: "2015"
    status: current                     # current | superseded | pending | draft
    publish_date: "2013-12-27"         # 国标发布日期（按官方公告）
    effective_date: "2014-11-01"
    supersedes: []                      # 被本画像取代的 profile_id
    superseded_by: null
    clause_tree_ref: docs/spec/audit-point-schema.md#clause-tree
    clause_count: 55                    # 条款/子条款单元数
    audit_point_count: 27               # 条款卡数
    transition_rules: []                # 换版映射，见 §4
    official_sources:                   # 每个来源独立记录核验状态
      - publisher: 国家标准化管理委员会
        title: GB/T 19001-2016 质量管理体系 要求
        url: "..."
        verified: false                 # true=已由有权人员核验
        verification_note: "待 #5 I8 核定"
    snapshot_id: "sha256:4e1cce77..."  # 条款段快照，底稿回溯用
```

### 2.1 字段约束

- `profile_id` 一经发布不可改；版本升级用**新增画像 + supersedes 指针**，旧画像转 `superseded`。
- `status` 取值必须是四者之一；`pending` 画像允许缺 `clause_tree_ref` 与 `snapshot_id`。
- `official_sources[].verified` 为 false 时，该画像**不得驱动自动判定**（对应 #20 的依据有效性接口，
  依据不可核验 → 相关条款只能落「证据不足」）。
- `snapshot_id` = 画像条款段内容的 sha256；运行时底稿记录 `profile.version_meta.snapshot_id`，
  事后可回溯「当时按哪一版审的」。

## 3. 条款树（clause tree）规范

- 节点 = 条款或子条款单元，标识用**层级点号**（`8.2.3`），不是 Markdown 标题文本。
- 每个节点带：`clause_no` / `title` / `parent` / `children[]` / `is_audit_unit`（是否单独出审核点）。
- 审核点与条款的关系用 `covers[]` + `covers_mode` 表达：
  - `subclauses_named` = 该条款卡逐条覆盖列出的子条款（如 8.2 覆盖 8.2.1~8.2.4）；
  - `content_blocks` = 该条款卡按内容块覆盖多个要求（非编号块，如 7.5 的三块、8.1 的四块）；
  - `single_clause` = 单条款。
- 共有条款骨架走 `shared/blocks.yaml`（`pdca-process-approach` / `common-documented-information` /
  `common-audit-and-review` / `common-nonconformity` / `common-planning`），不重复写进每个画像。
- 别名与无效编号走 `shared/aliases.yaml`；**无效编号**（如 8.2.5、8.2.6、5.5.1 在当前版本）必须登记，
  命中时提示「疑似引用了另一版本口径」而不是静默放行。

## 4. 换版影响与迁移规则

### 4.1 换版对审核点库的三类影响

1. **条款消失** → 审核点作废（标 `superseded`，不出现在新画像的审核范围）。
2. **条款号变更** → 靠 `shared/aliases.yaml` 的 `moved_from` 记录旧号→新号；旧审核点的 `clause_no` 改指新号，
   但**审计点正文与证据项不能自动继承**，必须重新核对（因为要求内容变了）。
3. **条款内容改写** → 条款号不变但审核问题、证据项、常见不符合项全部要重写。
   因此换版映射必须记录 `change_kind: renumbered | reworded | removed | added`。

### 4.2 版本并行期的处置

- 审核计划选定标准集合时，**绑定 profile_id（含版本）**，不绑标准号字符串。
- 换版过渡期若同时存在两个有效版本：审核底稿必须记录 `profile_id` + `snapshot_id`，
  报告抬头写明「依据 GB/T 19001-2016（快照 …）」，避免多年后无法回溯。
- `pending` 画像（如 ISO 9001:2026 的 GB 转化版）只作对照，不得进入自动判定。

### 4.3 需要人工裁定的冲突（不得自动决定）

- **C-R4-1**：ISO 14001:2015 是否已于 2026-04-15 撤回（Stage 95.99），ISO 14001:2026 是否已发布并取代。
  影响 #13（GB/T 24001 基线）的目标版本。未取得一手来源前，#13 按 GB/T 24001-2016 执行，并在报告中标注版本待核。
- **C-R4-2**：CNCA 认证规则何时切换到 ISO 9001:2026 对应国标（GB/T 19001 修订版预计 2027-02-28 前报批）。
  在切换公告发布前，现行认证依据仍是 2015 版。
- **C-R4-3**：ISO 9001:2026 的条款结构与审核点与 2015 版的映射关系 —— 须由正版标准逐条核定（#5 I8）。
- **C-R4-4**：GB/T 24001 修订计划 20261483 的范围与时点。
- **C-R4-5**：ISO 45001 / GB/T 45001 是否有换版计划。

## 5. 与其它票的接口

| 接口 | 提供方 | 消费方 |
|---|---|---|
| `profile_id` + `snapshot_id` | #2 S1 注册表 | #12 I2 判定引擎（运行时记录） |
| 条款号归一 / 无效编号 | #2 `shared/aliases.yaml` | #12 I2、#15 I3 报告生成 |
| 依据有效性（verified） | #20 依据库 | #12 I2（不可核验 → 证据不足） |
| 正版条款核定 | #5 I8 | #2 回写注册表与条款树 |
| 换版映射 `change_kind` | 本票 + #5 | #2 迁移工具 |

## 6. 待核实

1. ISO 14001:2026 发布与撤回的 ISO 官方记录（一手）。
2. GB/T 19001 修订计划 20264412-T-469 的官方项目页与征求意见稿。
3. ISO 9001:2026 的条款结构（须正版，#5 I8）。
4. GB/T 24001 修订计划 20261483 详情。
5. 各标准国标修订计划编号与时点的统一核验（std.samr.gov.cn 计划页、openstd 公告页）。
