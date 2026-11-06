# 依据库与依据版本核验（basis_db）

对应 GitHub Issue #20。规格依据：`docs/spec/verdict-anchor-rules.md` §7「依据库、依据快照与可核验性」。
种子依据取自仓内研究结论 `docs/research/standard-version-watch.md`（访问日期 2026-11-05），本模块运行时不联网。

## 0. 一句话

把「某条审计条款依据哪一版标准文件」变成**可核验的快照**：入库时留下内容哈希，核验时比对来源当前提供的名称标题，
判定为 `current / superseded / unverifiable` 三态，供 #12 判定引擎决定「依据充分」还是「证据不足」。

## 1. 文件清单

| 文件 | 作用 |
| --- | --- |
| `tools/basis_db.py` | 实现：命令行 + Python API |
| `tools/basis_snapshots/registry.json` | 快照登记台账（种子 9 条依据） |
| `tools/BASIS_DB.md` | 本文档 |
| `tools/tests/test_basis_db.py` | 单元测试（unittest） |

默认路径：

- 依据库（SQLite）：`tools/basis_snapshots/basis.sqlite3`（运行时生成，不随仓库提交）
- 快照台账：`tools/basis_snapshots/registry.json`
- 审核点库根目录：`<仓库根>/audit-point-library`

## 2. 数据模型（SQLite 三表）

### 2.1 `documents` —— 依据文档（一行一份依据）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INTEGER PK | 主键 |
| `source_id` | TEXT UNIQUE NOT NULL | 依据标识，全库唯一。规范写法：ISO 用 `ISO 9001:2015`，国标用 `GB/T 19001:2016`（冒号分隔版本号） |
| `publisher` | TEXT | 发布机构（ISO / SAC 等） |
| `title` | TEXT | 依据标题 |
| `number` | TEXT | 标准编号 |
| `category` | TEXT | 类别（国际标准 / 国家标准 等） |
| `listing_url` | TEXT | 来源条目页地址 |
| `file_url` | TEXT | 来源文件地址 |
| `created_at` | TEXT NOT NULL | 登记时间（UTC ISO 8601） |
| `updated_at` | TEXT NOT NULL | 最后更新时间 |

### 2.2 `document_versions` —— 版本快照（一份依据可以有多版）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INTEGER PK | 主键 |
| `document_id` | INTEGER NOT NULL | 外键 → `documents.id` |
| `title` | TEXT | 该版本标题 |
| `filename` | TEXT | 来源提供的文件名 |
| `url` | TEXT | 取件地址 |
| `size` | INTEGER | 字节数 |
| `sha256` | TEXT | 内容 SHA-256（`add_version` 自动计算） |
| `content` | BLOB | 文件内容本体，便于事后复算哈希 |
| `downloaded_at` | TEXT NOT NULL | 取件时间 |
| `note` | TEXT | 备注；人工复核时在此**追加不可变审计行**（见 §7） |
| `verification_status` | TEXT NOT NULL DEFAULT 'candidate' | **核验状态三值**：`candidate / pending_review / human_verified` |
| `verified_by` | TEXT | 最近一次复核人 |
| `verified_at` | TEXT | 最近一次复核时间 |

### 2.3 `checks` —— 核验记录（只追加，不改写）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INTEGER PK | 主键 |
| `document_id` | INTEGER NOT NULL | 外键 → `documents.id` |
| `checked_at` | TEXT NOT NULL | 核验时间 |
| `result` | TEXT NOT NULL | 判定三值：`current / superseded / unverifiable` |
| `stored_title` | TEXT | 库内快照的标题 |
| `source_titles` | TEXT | 来源当前提供的名称/标题/版本标识 |
| `detail` | TEXT | 判定说明 |
| `next_action` | TEXT | 下一步动作（人工复核、补来源、换新版等） |

### 2.4 核验状态字段放在哪里（明确答案）

**核验状态三值（`candidate / pending_review / human_verified`）加在 `document_versions.verification_status`**（见 §2.2），
配套的 `verified_by` / `verified_at` 两列做留痕。

**判定三态（`current / superseded / unverifiable`）不单独建列**，而是每次判定由 `derive_snapshot_status()` 现算，
历史判定以 `checks.result` 的形式逐行追加留档。计算优先级：

1. 台账 `status_evidence` 命中撤回标记（撤回 / 废止 / 作废 / withdrawn / superseded）→ `superseded`
2. 库内最近一次 `checks.result` 为 `superseded` 或 `unverifiable` → 沿用该结论
3. 台账没有 `snapshot_sha256` → `unverifiable`
4. `document_versions.verification_status != human_verified` → `unverifiable`（人工复核闸口）
5. 其余 → `current`

## 3. 快照台账 `registry.json`

### 3.1 每条依据的字段（15 个）

| 字段 | 含义 |
| --- | --- |
| `source_id` | 依据标识 |
| `publisher` | 发布机构 |
| `number` | 标准编号 |
| `title` | 标题 |
| `version` | 版本标识 |
| `published_date` | 发布日期 |
| `effective_date` | 实施日期（查不到一手证据时为 null） |
| `retrieved_at` | 取得来源件的时间 |
| `source_url` | 来源条目页地址 |
| `locator` | 定位（条款号或页码/章节） |
| `applicable_reason` | 为什么该依据适用于本次审核 |
| `status_evidence` | 状态结论的证据陈述（撤回/现行/修订在途） |
| `verification_status` | 核验状态三值之一 |
| `snapshot_sha256` | 快照内容哈希（无则 null） |
| `local_file` | 仓内本地来源件相对路径（相对仓库根） |

台账顶层另有 `policy.prohibited`，写明 **「禁止把搜索摘要当已核验正文」**。
台账的 `verification_status` 是证据级别的标注，与库内 `document_versions.verification_status` 的作用一致：
未经有权人员复核的一律 `pending_review`，此时该依据对判定引擎只能是 `unverifiable`。

### 3.2 种子 9 条（取自 docs/research/standard-version-watch.md）

| source_id | 状态结论 | 证据要点 |
| --- | --- | --- |
| `ISO 9001:2015` | 已撤回 → superseded | 2026-09-16 撤回（stage 95.99），被 ISO 9001:2026 取代 |
| `ISO 9001:2015/Amd 1:2024` | 已撤回 → superseded | 与 2015 版同日撤回 |
| `ISO 9001:2026` | 已发布 | 第六版，出版月 2026-09；目录号与实施日期未找到一手证据 |
| `GB/T 19001:2016` | 现行，修订在途 | 修订计划 20264412-T-469（等同采用 ISO 9001:2026），2027-02-28 前报批 |
| `ISO 14001:2015` | 已撤回 → superseded | 2026-04-15 撤回，被 ISO 14001:2026 取代 |
| `ISO 14001:2026` | 已发布 | 第四版，出版月 2026-04，目录号 92300 |
| `GB/T 24001:2016` | 现行，修订在途 | 修订计划 20261483-T-469（2026-03-31 下达，周期 12 个月） |
| `ISO 45001:2018` | 现行，修订在途 | 2024 复审确认继续有效；ISO/DIS 45001 在途，计划 ISO 45001:2027 |
| `GB/T 45001:2020` | 现行 | 发布并实施 2020-03-06；公开平台未检索到修订计划 |

九条种子记录的 `verification_status` 一律为 `pending_review`。

## 4. 命令行

全局参数必须写在子命令**之前**：

```bash
python tools/basis_db.py [--db PATH] [--registry PATH] [--repo-root PATH] <子命令> [参数]
```

### 4.1 四条命令

| 命令 | 用途 | 用法 |
| --- | --- | --- |
| `download` | 库里没有该 source_id 时，从台账来源记录取件入库 | `python tools/basis_db.py download "ISO 9001:2015"` |
| `check` | 库里已有该 source_id 时，比对来源当前提供的名称标题并判定 | `python tools/basis_db.py check "ISO 9001:2015"`（默认离线台账比对；加 `--live` 才实时访问来源页） |
| `list` | 列出库内文档及其版本与最近一次 check 结果 | `python tools/basis_db.py list` |
| `review` | 人工复核：推进某个版本快照的核验状态并留痕 | `python tools/basis_db.py review "ISO 9001:2015" --status pending_review --reviewer 张三 --note "已核对来源页"` |

取件顺序（`download`）：台账 `local_file` → 本地 `source_url` → http(s) `source_url`。
取不到时**不假装成功**：写一条 `checks.result=unverifiable` 的记录并以退出码 3 退出。

### 4.2 退出码对照表

| 退出码 | 常量 | 含义 | 典型触发场景 |
| --- | --- | --- | --- |
| 0 | `EXIT_OK` | 成功且判定明确 | `download` 取件入库成功；`check` 判定 current；`list` 有数据；`review` 复核已落库 |
| 2 | `EXIT_SUPERSEDED` | 判定为非最新版或已更新 | `check` 判定 superseded（来源声明该版本已撤回或版本标识不一致） |
| 3 | `EXIT_UNVERIFIABLE` | 无法判定 | `check` 判定 unverifiable（来源不可达 / 台账无该依据 / 库内无版本快照）；`download` 取件失败 |
| 4 | `EXIT_USAGE` | 参数或库状态错误 | 依据库不存在、库结构不完整、库为空却执行 `list`/`check`、台账未登记该 source_id、库中没有该 source_id、参数非法 |

要点：

- `check` 的退出码由**本次判定结果**决定：current→0，superseded→2，unverifiable→3。
- `list` / `check` 在**库为空**或**库不存在**时都返回 4 并在 stderr 给出明确提示，不得静默通过。
- 判定为 current 但人工复核未完成时，`check` 仍返回 0，但输出中的「生效状态」是 `unverifiable`：
  **判定引擎必须按 unverifiable 处理**（见 §5.2）。

## 5. 供 #12 判定引擎调用

### 5.1 函数签名

```python
def init_db(path) -> sqlite3.Connection
def open_db(path) -> sqlite3.Connection
def register_document(conn, source_id, publisher, title, number, category,
                      listing_url, file_url) -> int
def add_version(conn, document_id, title, filename, url, content_bytes,
                note=None) -> int
def record_check(conn, document_id, result, stored_title, source_titles,
                 detail, next_action) -> int
def set_version_verification(conn, version_id, new_status, reviewer,
                             note=None) -> int
def resolve_clause_basis(conn, profile_id, clause_no, registry_path=None,
                         library_root=None) -> Dict[str, Any]
def is_basis_current(conn, source_ref, registry_path=None) -> str
```

- `init_db(path)`：建库并保证三表存在，返回已提交的连接。
- `open_db(path)`：打开已存在的库；库不存在或缺表时抛 `BasisError`（映射退出码 4）。
- `add_version(...)`：写入版本快照并**自动计算 sha256**，新版本一律以 `verification_status='candidate'` 落库。
- `record_check(...)`：追加一条核验记录，`result` 只接受 `current / superseded / unverifiable`。
- `set_version_verification(...)`：人工复核流转（见 §7）。
- `resolve_clause_basis(conn, profile_id, clause_no)`：按「画像 + 条款号」解析依据快照，返回字典。
- `is_basis_current(conn, source_ref)`：返回三态字符串。`source_ref` 可带条款定位（`"ISO 9001:2015#4.1"`），按 `source_id` 处理。

### 5.2 resolve_clause_basis 的返回与错误

成功时返回字典，**必含** §7.1 的快照字段：

```python
{
  "error": None,
  "profile_id": "qms-gbt19001-2016",
  "clause_no": "4.1",
  "clause_title": "...",
  "standard": "GB/T 19001-2016",
  "source_id": "GB/T 19001:2016",
  "source_ref": "GB/T 19001:2016#4.1",
  "version": "2016",
  "locator": "4.1",
  "snapshot_sha256": "<sha256>",
  "retrieved_at": "2026-11-05",
  "status": "current" | "superseded" | "unverifiable",
  "insufficient_reason": None | "BASIS_UNVERIFIABLE" | "BASIS_SUPERSEDED",
  "iso_equivalent": {...},
}
```

解析不到时**返回带 error 字段的字典，绝不返回悬空结果**（既不返回 None，也不返回缺字段的半成品）：

| `error` | 触发条件 | 对应判定 |
| --- | --- | --- |
| `PROFILE_NOT_FOUND` | 画像 id 在 `audit-point-library/registry.yaml` 中不存在 | 落 insufficient |
| `CLAUSE_TREE_UNAVAILABLE` | 条款树文件缺失或不可解析 | 落 insufficient |
| `CLAUSE_NOT_IN_TREE` | 条款号不在该画像条款树中（§7.4） | 落 insufficient，原因码 `CLAUSE_NOT_IN_TREE` |
| `BASIS_NOT_REGISTERED` | 依据未登记进快照台账 | 落 insufficient，status=`unverifiable` |

四个错误分支的返回结构一致：`error` 为错误码、`message` 为可读说明、`status` 一律 `unverifiable`、
`insufficient_reason` 为对应原因码（`BASIS_NOT_REGISTERED` 对应 `BASIS_UNVERIFIABLE`，
`CLAUSE_NOT_IN_TREE` 对应 `CLAUSE_NOT_IN_TREE`，其余同理）。

### 5.3 联动：unverifiable 时只能落证据不足

规格 §7.3 原文：

> 判定前调用依据库核验；**核验返回退出码 3（无法判定）时**，该条款只能落 `insufficient`，且不得凭标准文本或模型记忆补证（§0 红线一）。
> 依据库核验通过（退出码 0/1/2）时，把快照的 `snapshot_sha256` 写进判定记录，用于事后复核「当时依据的是哪一版」。

本模块的落地方式：

1. 判定引擎先调 `resolve_clause_basis(conn, profile_id, clause_no)`。
2. 返回 `status == "unverifiable"`（或 `error` 非空）时，**该条款只能落 insufficient**，
   原因码取返回里的 `insufficient_reason`（依据未登记时为 `BASIS_UNVERIFIABLE`，条款不在树中时为 `CLAUSE_NOT_IN_TREE`），
   不得用标准原文或模型记忆补证。
3. 返回 `status == "superseded"` 时，**不得用它下结论**，须换用新版依据快照，否则同样落 insufficient（原因码 `BASIS_SUPERSEDED`）。
4. 返回 `status == "current"` 时，把 `snapshot_sha256` 写进判定记录，用于事后复核当时依据的是哪一版。
5. `is_basis_current(conn, source_ref)` 单独调用时返回 `"current" / "superseded" / "unverifiable"` 三态字符串；
   **unverifiable 时绝不返回 current**（未经有权人员复核一律 unverifiable）。

命令行侧的等价物：`check` 的退出码 3 就是「无法判定」，判定引擎按 §7.3 把它映射为 insufficient。

## 6. 已知局限

**check 只比对来源提供的名称标题，不比对文件内容；同名改内容不会被发现；补偿手段是 sha256 快照比对。**

补充说明（不替代上面这段原文）：

- `check` 默认走**离线台账记录**（`registry.json` 的 `status_evidence` / `title` / `version`），
  加 `--live` 才会实时访问来源页；离线判定反映的是「台账记录的取得时间状态」，不是实时状态。
- 同名改内容只能靠 §2.2 的 `sha256` 发现：把库内 `document_versions.sha256` 与重新取件后的哈希比对。
- `content` BLOB 存的是取件当时的原件，用于事后复算；不保证来源方日后仍可提供同一份文件。
- 台账 `snapshot_sha256` 为 null 的依据一律 unverifiable，不得凭 `status_evidence` 的文字断言当作已核验正文
  （台账 `policy.prohibited`：禁止把搜索摘要当已核验正文）。

## 7. 人工复核流程

三态流转：`candidate` → `pending_review` → `human_verified`。

| 阶段 | 谁来改 | 怎么改 | 留痕 |
| --- | --- | --- | --- |
| `candidate` | 系统自动 | `add_version()` 新建快照时默认写入 | `document_versions.note` 记录取件来源与 `retrieved_at` |
| `pending_review` | 有权人员（或受委托的复核人） | `set_version_verification(conn, version_id, "pending_review", reviewer, note)` 或 `basis_db.py review <source_id> --status pending_review --reviewer <姓名>` | `note` 追加一行不可变审计记录，`verified_by` / `verified_at` 写入复核人与时间 |
| `human_verified` | 有权人员 | 同上，`--status human_verified` | 同上追加一行；此后该依据才可能被判为 `current` |

规则：

- 复核人不得留空（`reviewer` 必填，缺则抛错）。
- `note` 只**追加**不覆盖，历史流转记录不可改写。
- 未到 `human_verified` 的依据，`is_basis_current` 一律返回 `unverifiable`，不得参与下结论。
- 台账（`registry.json`）的 `verification_status` 与库内字段同步维护；两边都未到 `human_verified` 时，
  以库内 `document_versions.verification_status` 为准（为空时回落台账）。

## 8. 运行与测试

```bash
export PYTHONIOENCODING=utf-8
/i/python3.10/python.exe -m unittest discover -s tools/tests -v
```

测试全部在临时目录里建库建台账，不写入仓库任何目录。

只跑本模块用例：

```bash
export PYTHONIOENCODING=utf-8
/i/python3.10/python.exe -m unittest discover -s tools/tests -p "test_basis_db.py" -v
```

注意：`discover -s tools/tests` 会把该目录下其它模块的用例一起跑进来。当前仓内 `test_calculate.py`
另有 13 errors + 1 failure，源于 `tools/params.yaml` 缺少 `ratio.sampling.initial.factor` 等参数条目，
与本模块无关（本模块 20 个用例全部通过）。

日常使用：

```bash
python tools/basis_db.py download "GB/T 19001:2016"     # 取件入库
python tools/basis_db.py check "GB/T 19001:2016"        # 核验（退出码 0/2/3）
python tools/basis_db.py list                            # 查看库内依据
python tools/basis_db.py review "GB/T 19001:2016" --status pending_review --reviewer 张三
```
