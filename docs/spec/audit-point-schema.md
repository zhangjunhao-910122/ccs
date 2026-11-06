# 审核点库数据结构规格（S1）

> 对应 GitHub Issue #2（map #1 的子票 S1）。本文是 #2 的交付规格，也是 #7/#11/#12/#15 的地基。
> 状态：已实现并通过 `tools/build_library.py validate`（0 错误）。版本：v3（2026-11-06 增补 point_key 形态、共享块合并与 V12~V15 校验）。

## 1. 目的

定义一个**可被机器逐条执行**的审核点库，使审核员能在一次审核中回答三个问题：

1. 这一条要求是什么（条款号 + 条款原文要点 + 审核问题）
2. 判定它需要哪些证据（书面证据 + 现场核查证据，逐项可勾）
3. 不符合时怎么写（典型不符合项 + 事实描述 + 锚点）

库必须同时容纳 ISO 9001 / 14001 / 45001 三个体系，新增体系只加数据不改逻辑；
并且能被第二家认证机构复用（同一份库，不同的检查表与抽样策略）。

## 2. 第一性原理：三层分离

把「审核」这件事拆成三类事实，它们变��速度与可信度完全不同，必须分开存放：

| 层 | 名称 | 内容 | 变化频率 | 可否对外复用 |
|---|---|---|---|---|
| L1 | **条款树** | 标准客观结构：条款号、标题、父子关系 | 极慢（换版才变） | 可 |
| L2 | **审核点库** | 判断性内容：审核问题、证据项、典型不符合项 | 慢（随标准换版/机构经验变） | 可（经核定） |
| L3 | **运行时底稿** | 现场结论：锚点、判定、不符合项 | 每次审核都变 | **不可** |

三条硬规则：

- **库里绝不写现场结论。** 符合/不符合/证据不足/不适用是底稿的事。
- **覆盖范围只能来自条款树（L1），不能从审核点表的自然语言括注里猜。**
  实测教训：括注里出现的编号可能是 2008 版残留（如 5.5.1）、可能是另一版（2026）的编号
  （如 6.2.1/6.2.2）、也可能只��数量（8.1「四个内容块」）。猜出来的追溯关系是错的。
- **转换必须确定性可复现。** 同一输入两次 parse，产出字节一致（除 YAML 头注释外无随机量）。

## 3. 文件布局

```
audit-point-library/
  registry.yaml                 # 标准画像注册表（画像元数据真源）
  clause_trees/                 # L1 条款树，按标准版本组织
    qms-gbt19001-2016.yaml
  profiles/                     # L2 画像，一个标准一��文件
    quality/gbt19001-2016.yaml
    environment/gbt24001-2016.yaml
    ohs/gbt45001-2020.yaml
  shared/
    rules.yaml                  # 四态判定规则、锚点规则、照片取证分级、难度枚举
    blocks.yaml                 # 共享条款公共块（跨体系共有的审核骨架）
    aliases.yaml                # 编号别名与无效编号路由
tools/
  build_library.py              # parse（Markdown→YAML 画像）+ validate（结构与规则校验）
docs/
  spec/audit-point-schema.md    # 本文
  research/                     # 人读的条款级审核点基线（parse 的输入）
```

## 4. 数据模型

### 4.1 标准画像注册表 `registry.yaml`

新增一个体系 = 加一个画像文件 + 此处加一行，不改加载逻辑。

```yaml
profiles:
  - id: qms-gbt19001-2016          # 画像唯一 id
    domain: quality                 # quality | environment | ohs
    file: profiles/quality/gbt19001-2016.yaml
    status: current                 # current | superseded | pending
    standard:
      designation: GB/T 19001-2016  # 标准号
      iso_equivalent: ISO 9001:2015 # 对应 ISO 标准
      edition: "2016"
      status: current               # 标准本身的状态
      published: "2016-12-30"
      effective: "2017-07-01"
      withdrawn: null
      supersedes: null
      relation: identical           # identical | modified | ...
      official_sources:             # V8：每个现行画像至少一条一手来源
        - url: https://openstd.samr.gov.cn/bzgk/gb/std_list
          accessed: "2026-11-05"
          title: 国家标准全文公开系统 GB/T 19001 条目
    scope:
      applies_to: 管理体系认证审核（初次第一阶段、初次第二阶段、监督、再认证）
      excludes: [产品认证（CCC）]
    clause_table_ref: docs/research/iso9001-clause-audit-baseline.md
    clause_tree_ref: clause_trees/qms-gbt19001-2016.yaml
    version_meta:
      generated_from: docs/research/iso9001-clause-audit-baseline.md
      verification_status: toc_verified   # toc_verified | unverified | verified
      verified_by: null
      verified_at: null
```

同一 domain 可以同时挂多条画像（旧版 `superseded` + 新版 `current`），
换版时新增而不是原地改，否则旧底稿无法回溯「当时按哪版审的」。

### 4.2 条款树 `clause_trees/*.yaml`

条款树是标准的客观结构，只描述「有哪些条款、谁是谁的子条款」，不含任何审核判断。

```yaml
profile_id: qms-gbt19001-2016
standard: GB/T 19001-2016
iso_equivalent: ISO 9001:2015
version_label: "ISO 9001:2015 / GB/T 19001-2016 条款结构"
verification_status: unverified
clause_tree:
  - clause_no: "8"
    title: 运行
    children:
      - clause_no: "8.2"
        title: 产品和服务的要求
        children:
          - clause_no: "8.2.1"
            title: 顾客沟通
          - clause_no: "8.2.2"
            title: 产品和服务要求的确定
```

条款树同时是**覆盖范围的唯一来源**：一张审核点卡（Clause）的 `covers` = 条款树中该条款号的直接子条款。
换版时新增一份新树（`qms-gbt19001-2026.yaml`），旧树保留，画像指向哪棵树由注册表决定。

### 4.3 画像 `profiles/**/*.yaml`

```yaml
profile:
  id: qms-gbt19001-2016
  domain: quality
  standard: {...}                     # 与注册表一致（V8 校验）
  scope: {...}
  version_meta:
    generated_from: docs/research/iso9001-clause-audit-baseline.md
    clause_tree_ref: clause_trees/qms-gbt19001-2016.yaml
    snapshot_id: "sha256:..."          # clauses 段的哈希，见 §7
    consistency_warnings: [...]        # 转换期发现的版本口径疑点
clauses:
  - clause_no: "8.2"
    title: 产品和服务的要求
    scope_note: "含 8.2.1 顾客沟通、8.2.2 要求的确定、8.2.3 评审、8.2.4 更改"
    covers: ["8.2.1", "8.2.2", "8.2.3", "8.2.4"]   # 来自条款树，非括注
    covers_mode: subclauses_named       # subclauses_named | content_blocks | single_clause | not_in_clause_tree
    covers_source: clause_tree
    block_count: null                   # 基线称「N 个内容块」时的 N
    mentioned_units: ["8.2.1", "8.2.2", "8.2.3", "8.2.4"]  # 括注里提到的编号，仅作线索
    blocks: []                          # 引用的共享块 id
    photo_evidence: not_applicable      # allowed | conditional | not_applicable
    photo_note: null
    aliases: []                         # 旧版/别称编号，路由到本卡
    points:
      - point_key: "8.2#1"              # <条款号>#<序号>，卡内序号从 1 起连续
        point_source: baseline          # baseline | block（共享块并入的补充审核点）
        audit_question: 组织如何确定、评审并变更产品与服务的要求？
        written_evidence: ["...", "..."]
        on_site_evidence: ["...", "..."]
        typical_nc: ["...", "..."]
        evidence_items:
          written:
            - {id: w1, kind: written, text: "...", priority: core}
            - {id: w2, kind: written, text: "...", priority: supporting}
          on_site:
            - {id: o1, kind: on_site, text: "...", priority: core}
        difficulty: high                 # high | medium_high | medium | low | null（取不到就留空，不猜）
        difficulty_reason: "..."         # 取自基线难度表同行理由
        difficulty_basis: baseline       # baseline（基线原值）| estimated（类比推定，待 #16 R4 复核）
        apg_ref: []                      # ISO 审计实践组相关条款引用（可空）
        block_ref: []                    # 共享块 id 列表（仅块并入的审核点有）
        block_point_key: null            # 块内原始 point_key（仅块并入的审核点有）
```

**四行式是审核点的最小完整形态**：审核问题 / 需索取的书面证据 / 现场核查证据 / 常见不符合项。
缺任何一行都不生成审核点（转换器会跳过并报告），因为缺一行的审核点在现场无法执行。

**EvidenceItem 是有序列表项**（`w1..`、`o1..`），不是自由文本。理由：判定结果必须指向具体某一项证据，
`锚点` 才能与 `证据项` 一一对应；自由文本无法回指。

**`priority` 不是「必核」标志**。库里给 `core`/`supporting` 只是启发式默认（每类第一项为 core），
真正「本次审核必核哪些」由运行时审核计划的抽样决定。理由见 §8。

### 4.4 共享条款公共块 `shared/blocks.yaml`

三体系共有的条款（4.4 过程方法、7.5 成文信息、9.2+9.3 内审与管理评审、10.2 不符合与纠正措施、6.1 策划）
在三个画像里各写一遍必然漂移。共享块提供共通骨架，画像按 `inheritance: extend` 继承。

合并规则：**基线审核点的内容优先、绝不被块覆盖；共享块审核点作为补充按卡内序号追加**。
块点身份 = (块 id, 块内 `point_key`)，与卡内 `point_key` 命名空间相互独立，
因此块点不会被误判为与基线点同一条；已合并过的块点不重复追加。
每个块点带 `block_ref`（块 id 列表）与 `block_point_key`（块内原 key），用于**漂移检测**。
理由：本阶段的 9001 画像由基线生成、内容更全，块不应覆盖它；
块的价值在于为三体系共有条款补齐通用视角，并给漂移检测提供锚点。

### 4.5 编号别名路由 `shared/aliases.yaml`

别名必须归一到同一张条款卡，否则同一要求会被重复取证或漏取。

```yaml
profiles:
  qms-gbt19001-2016:
    aliases:
      - clause_no: "8.6"
        aliases: ["8.7"]
        note: 部分公开资料与译本用 8.7 指「不合格输出的控制」，主键用 8.6
    invalid_numbers:
      - number: "8.2.5"
        reason: ISO 9001:2015 的 8.2 只有 4 个子条款，不存在 8.2.5
```

`invalid_numbers` 命中即判为无效编号并提示审核员，不得静默忽略。

### 4.6 判定与取证规则 `shared/rules.yaml`

四态判定、锚点规则、照片取证三级（`allowed` / `conditional` / `not_applicable`）、难度枚举。
本文件是 #9 S2 的落点，当前为最小可用版本。

## 5. 转换规则（`parse`）

确定性、可复现：

1. 定位标题含「条款级审核点基线」的一级或二级 Markdown 小节（标题级别自适应），到下一个同级或更高级标题为止。
2. 以 `#### <条款号> <标题>` 切分条款卡；`##### ` 开头的是口径说明块（如 5.5.1），**不生成审核点**。
3. 读每张卡的 `| 项目 | 内容 |` 表，取四行：审核问题 / 需索取的书面证据（也兼容「需索取书面证据」）/ 现场核查证据 / 常见不符合项。
4. 单元格按中文/英文分号切成证据项数组；四项缺任一则跳过该卡并报告。
5. 难度：从标题含「取证难度分级」的表按条款号 join（表头列数自适应：4 列为「难度|条款|…」，3 列为「条款号|难度|理由」；条款格支持 `4.1～4.3` 区间展开），取不到留 `null`（不猜）。
   难度按子条款分档时，卡级难度取「卡自身难度」与「其 covers 子条款难度」的最高档（`resolve_difficulty`）。
6. 标题取全角括号前为 `title`，括号内容进 `scope_note`；括注里的编号进 `mentioned_units`（仅线索）。
7. `covers` 由条款树推导，不由括注推导。
8. `snapshot_id` = `sha256(json.dumps({"clauses": clauses}, sort_keys=True))`。
9. 审核点 `point_key` 形如 `<条款号>#<序号>`，卡内序号从 1 起连续分配；共享块并入的审核点接续编号，并标 `point_source: block`。
10. 难度表同行的理由写入 `difficulty_reason`；理由以「类比推定」开头时 `difficulty_basis: estimated`（待复核），否则 `baseline`。
11. 共享块审核点按 §4.4 的合并规则并入条款卡，四行不齐的块点跳过。

## 6. 校验规则（`validate`）

| 编号 | 规则 |
|---|---|
| V1 | 画像 id / domain / standard.designation 与注册表一致 |
| V2 | 同一条款卡内 `point_key` 唯一 |
| V3 | 审核点四行齐全；`evidence_items` 数量与 `*_evidence` 一致 |
| V4 | `photo_evidence` ∈ {allowed, conditional, not_applicable}；`difficulty` ∈ {high, medium_high, medium, low, null}；`priority` ∈ {core, supporting} |
| V5 | 条款号不在 `invalid_numbers`；别名不与真实条款号冲突 |
| V6 | 条款卡引用的共享块确实包含该条款，且块已登记该条款 |
| V7 | 证据项编号按 `w1..` / `o1..` 顺序 |
| V8 | 每个现行画像至少一条 `official_sources`，且条目含 `url` 与 `accessed` |
| V9 | `covers_mode` 合法；`subclauses_named` 时 `covers` 非空；`content_blocks` 时有 `block_count` |
| V10 | 条款号存在于条款树；`covers` ⊆ 条款树的直接子条款 |
| V11 | 基线括注提到的编号必须在条款树或别名表中；基线称「N 个内容块」时与树的子条款数一致 |
| V12 | 审核点 `point_key` 形如 `<条款号>#<序号>`，前缀与所在条款号一致，卡内序号连续 |
| V13 | 审核点缺难度只告警；难度为类比推定（`difficulty_basis: estimated`）也告警，提示待 #16 R4 复核 |
| V14 | `apg_ref` 为空的审核点在画像级汇总告警，提示待 #16 R4 回填 |
| V15 | 共享块登记的条款必须被条款卡引用，且块内审核点必须已合并进该卡（按 `block_ref` + `block_point_key` 判定） |

V11 不报错只告警：基线是判断性内容，与条款树冲突时应由人裁定谁对，而不是让工具替人裁定。
V13、V14 同样只告警：难度缺失与 `apg_ref` 缺失都是「取不到就留空，不猜」的既定口径，
由 #16 R4 复核或回填，校验器只负责把缺口显式喊出来，不替人补值。
V15 报错：共享块已登记的条款与审核点若没有并进画像，说明 §4.4 的继承规则没有被真正执行。

## 7. 版本与失效

- 同一 domain 可同时存在多条画像：旧 `superseded` + 新 `current`。
- 每份画像带 `version_meta.snapshot_id`（clauses 段哈希）。运行时底稿记录所用 `snapshot_id`，
  事后可回答「这次审核当时按哪一版审的」。
- 校验器会重算哈希并与文件内值比对，对不上即报「内容被手改或过期」（快照失效检测）。
- 标准换版（ISO 9001:2026 已于 2026-09-16 发布）时：新增条款树 + 新增画像，
  旧画像置 `superseded` 而不删。映射与影响面由 #16 R4 产出。

## 8. 运行时底稿层（不入库）

审核计划确定本次要查哪些标准（用户每次自选标准集合，可单选可多选）、哪些条款必核之后，
才产生底稿：

- `anchor`：证据锚点 = 书面 `文件路径 + 页/段/表行`，或现场 `照片 ID + 区域`
- `judgment`：四态（符合 / 不符合 / 证据不足 / 不适用）
- `finding`：不符合项（条款号 + 事实描述（带锚点）+ 判定依据 + 类别 + 纠正与纠正措施要求）

**必核集合不放在库里**：若库里标满必核，一次现场查不完 600+ 证据项，结果是全部判「证据不足」，
审核员拿不到任何结论。抽样由审核计划按风险与范围决定——这是「全量清单 + 动态抽样」
而非「线性逐条执行」的原因。

## 9. 已知缺口（截至本版）

1. **条款单元数口径**：三个画像的条款树均按 ISO 官方预览版目录一手核定，节点数分别为
   QMS 73（7 个章级 + 28 个二级 + 38 个三级）、EMS 45、OHS 50。
   转换器按条款树推导的覆盖单元数：QMS 66（28 二级 + 38 三级，章级只作组织层级）、EMS 38、OHS 43 ——
   三个体系均 100% 覆盖条款树的全部非章级节点（`build_library.py parse` 输出）。
   基线侧另有一个「审核点分组」口径（9001 §8.2 明细表为 56 个），两者统计对象不同：基线按卡内分组计数，
   转换器按条款树节点计数；差异已在基线中逐条说明。
2. **中文条款标题与正文措辞未核定**：条款号与层级已一手核定（`verification_status: toc_verified`），
   但中文标题、正文措辞，以及 GB/T 与 ISO 的目录差异（ISO 45001:2018 官方目录含 6.1.2.1~6.1.2.3、
   8.1.4.1~8.1.4.3，GB/T 45001-2020 官方目次未列该层级）仍须 #5 I8 以正版标准逐条核定，
   核定后回填 `verified_by` / `verified_at`。
3. **难度标注**：基线按子条款分档，转换器聚合到卡级（`resolve_difficulty`）。
   三份基线中未被难度表覆盖的条款（ems 20 条、ohs 4 条）已按同类条款类比补齐，
   理由列一律以「类比推定，待研究复核」开头，对应 `difficulty_basis: estimated`，
   并登记在各自基线的待核实表里，由 #16 R4 复核；校验器 V13 持续告警提示这些点是推定值而非基线原值。
4. `photo_evidence` 全部为 `not_applicable`，白名单待 #11 I4 照片取证链路核定。
5. `shared/rules.yaml` 目前是最小版本，四态细则与锚点规则由 #9 S2 展开。

## 10. 本票不做什么

- 不做条款原文复制（标准正文受版权保护，条款标题按公开结构重建，编号与文字以正版为准）。
- 不写判定引擎（#12 I2）、不做报告生成（#15 I3）、不做照片识别（#11 I4）。
- 不做人工核定的替代（#5 I8 的逐条正版核定仍是上线前提）。
