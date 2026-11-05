# 对外 API 契约与页面数据契约 v1.0

- 票：#46 规格：对外 API 契约与页面数据契约（父地图 #22）
- 日期：2026-11-05
- 状态：v1.0，可执行（机器依据 `openapi.json` + `errors.json` + `agents.json` + `page-data.schema.json`，参考实现 `reference_api.py`，自检 `verification.txt`）
- 范围边界：本票只定**跨中心通用**的接口约定、数据查询类契约、Agent 工具契约与页面数据形状；各业务中心自己的输出字段（预测/归因/结算报告等）由 #39「规格：预测与分析输出契约」定，本票只定信封与引用方式。

## 1. 依据与输入

| 来源 | 取用内容 |
| --- | --- |
| 方案 01-功能清单 M01–M10、F001–F064 | 三个工作台（交易员/交易主管/风控审计）与八大 Agent 的功能边界 |
| 方案 3.3 核心 Agent 表 | 规则教练、数据治理、负荷预测、头寸分析、策略教练、结算复核、风控合规、复盘考评 |
| 方案 A08 / A09 / A12 | 敏感数据不出域；权限隔离；**无写入真实交易系统能力** |
| #29 认证与六角色权限矩阵 | 六角色 × 九维度、`evaluate()` 裁定、范围与掩码、审计事件结构 AU18、`GET /me/capabilities` |
| #33 统一数据契约 | 16 类对象 D01–D16、字段名/类型/单位/精度、12 通用列、敏感级别 C8、双时态 C9 |
| #25 业务数据存储层 | 物理表名与 `src_*` 列仅为内部实现，不得出现在接口 |
| #26 时间栅格 | 业务日 + slot_no、24 时段、上卷规则 T6/T7、质量五级 |
| #45 组合负荷预测口径 | 预测起点 P_CURVE/P_BID/P_REVIEW、特征可得性、防穿越断言 |
| #54 规则知识库 | 可追溯回答五要素：文件、条款号、版本、生效日期、原文片段（+ page_ref） |
| #39（未关闭） | 预测/归因/修正/漂移的输出字段由它定；本票只引用 `output_id`，不重定义 |

## 2. 第一性原理

1. **接口是授权之后的取数，不是取数之后的裁剪。** 行过滤与字段掩码必须在查询执行前由服务端裁定；返回后再删字段已经晚了（日志、缓存、导出都已泄漏）。
2. **接口的事实只有一份。** 数据集、字段、单位、精度、敏感级别全部来自 #33 契约；接口只做投影与传输，不另立字段表，不做单位换算。
3. **平台只读与建议，不执行。** 对外接口的效果只有 READ（读）、PROPOSE（生成建议/草稿）、SIMULATE（回放/模拟计算）三种；没有 EXECUTE，也没有任何路径能写真实交易系统（A12）。
4. **数值必须无损。** 电量/价格/金额用定点小数字符串传输，禁止 JSON number 承载小数；NULL 表示未观测，绝不补零、绝不省略。
5. **每个请求都可审计、可重放。** 允许与拒绝同构留痕；同一请求（同一幂等键、同一输入）重放得到同一结果。
6. **页面只是查询的视图。** 前端不做业务计算、不拼指标、不猜空态；页面数据由服务端按块组装，缺数据显式标状态。

由此得到六条公理：

- **API-A1 授权先行**：任何数据集读取先过 #29 `evaluate()`，DENY 即拒绝，不进入查询。
- **API-A2 单一契约**：数据集与字段来自 #33；接口不得暴露物理表名、`src_*` 列、SQL、内部主键。
- **API-A3 无执行面**：效果枚举不含 EXECUTE；OpenAPI 中不存在写交易账户/申报/成交的路径。
- **API-A4 无损序列化**：定点数字符串 + 固定小数位；布尔/日期/时间/枚举按契约类型。
- **API-A5 同构审计**：每个请求一条审计事件，字段同 #29 AU18，拒绝必须有 reason_code。
- **API-A6 页面无逻辑**：页面块只声明来源、状态、数据与动作能力，不含计算公式。

## 3. 规范（A1–A18）

### A1 协议与契约形态

- HTTPS + JSON（UTF-8），路径前缀 `/api/v1`；OpenAPI 3.1 的 `openapi.json` 是唯一机器依据，本文解释取舍。
- 所有时间带偏移（`+08:00`）；不接受裸本地时间；日期用 `YYYY-MM-DD`。
- 响应必须带 `meta.request_id`；服务端同时回 `X-Request-Id` 响应头，二者一致。
- 契约版本在 `meta.contract_version` 回显（当前 `1.0.0`）。

### A2 资源命名

- 数据集用逻辑编码 `D01`–`D16`；字段用 #33 契约字段名（如 `energy_mwh`、`slot_no`）。
- 禁止出现物理表名（`d04_load_hourly`）、存储层列名（`src_batch_id`）、内部主键（`record_id`）、SQL 片段。
- 主体编码用 #33 C5 的统一编码（`USER-4201-000123-X` 等），不暴露自增 id。

### A3 认证与会话

- API 用 `Authorization: Bearer <opaque-token>`（#29 AC5 的不透明令牌）；API 不使用 Cookie，避免 CSRF 面。
- 401 统一为 `UNAUTHENTICATED`，不区分「账号不存在 / 口令错 / 会话过期」。
- 会话空闲 30 分钟、绝对 12 小时由 #29 管；令牌轮换后旧令牌立即 401。
- 系统管理员默认看不到敏感数据；越权与缺授权都返回 403，并带稳定 reason_code。

### A4 统一信封

成功：

```json
{"data": {...}, "meta": {"request_id": "req_01J...", "api_version": "v1", "contract_version": "1.0.0", "as_of": "2026-11-05T12:00:00+08:00", "generated_at": "2026-11-05T12:00:01+08:00", "row_count": 24, "next_cursor": null, "masked_fields": [], "quality": {"valid": 24, "partial": 0, "missing": 0, "coverage_ratio": "1.0000"}, "units": {"energy_mwh": "MWh"}}}
```

失败：

```json
{"error": {"code": "RANGE_TOO_LARGE", "message": "slot 粒度单次最多 31 天", "details": {"max_days": 31}, "retryable": false}, "meta": {"request_id": "req_01J...", "api_version": "v1"}}
```

- 成功与失败不混用；HTTP 状态码与 `error.code` 一一对应（见 A13）。
- `message` 是给人看的中文短句；机器判定只认 `code`。
- 不返回堆栈、SQL、内部路径、表名。

### A5 数值与空值

- `decimal` 字段序列化为**字符串**，固定小数位：#33 defaults（电量 3、价格 4、金额 2、比例 4、温湿度 1）。
- 例：`"energy_mwh": "1234.567"`、`"price": "415.0000"`、`"amount": "512.11"`。
- 整数字段（slot_no、row_count）用 JSON number；布尔用 true/false；日期/时间用字符串。
- `null` 必须显式出现，不省略字段；**不得用 0 代替 null**。
- 聚合结果保留输入单位；不自动换算 kWh、万元等。

### A6 时间、粒度与上卷

- 查询的时间维度统一用**业务日** `time_range.from/to`（含 from、不含 to），不用结算日/交易日（#26 T10）。
- `granularity` ∈ `slot` | `day` | `month` | `year`：
  - `slot`：24 时段原始小时行（15 分钟源已按 #26 T5 聚合，接口不暴露 96 点）；
  - `day`/`month`/`year`：按 #26 T6 上卷——电量求和、官方价保持原值、自有价按电量加权、金额求和、比率重算、计数取期末、质量取最差。
- 上卷结果附 `valid_hours` / `expected_hours` / `coverage_ratio`（#26 T7）；`coverage_ratio < 1` 时电量给 `null` + `observed_sum_mwh` 诊断值，不插值。
- 单次范围上限：`slot` ≤31 天/主体、`day` ≤366 天、`month` ≤120 个月、`year` ≤20 年；超过返回 422 `RANGE_TOO_LARGE`。
- 行数上限 100,000；超过同样 422，不静默截断。
- `as_of` 缺省为当前时刻；显式传入必须带偏移。规则/合同/额度类按双时态 `valid_from ≤ as_of < valid_to` 命中唯一版本；同日命中两版返回 409 `AMBIGUOUS_VERSION`（#33 C9）。

### A7 过滤

- 可过滤字段 = 该数据集契约字段中非 TBD、非掩码的字段；通用血缘列（`batch_id`、`src_*`）不对接口开放，避免经过滤反推导入批次。
- 操作符：`eq`、`in`（≤100 值）、`range`（仅 date/datetime/int/decimal）；v1 不支持 LIKE / 正则 / 任意表达式。
- 未知字段返回 400 `UNKNOWN_FILTER`；对掩码字段过滤返回 403 `FIELD_MASKED`；TBD 字段只能出现在结果中（原样保留），不能作为过滤条件。
- 多个字段之间是 AND；同一字段 `in` 是 OR。
- 默认排序：按数据集 business_key 升序、再按 `valid_from` 升序；`slot` 粒度按 `business_date, slot_no`。

### A8 分页

- 游标分页，不使用 offset；`limit` 默认 200、最大 1000。
- `next_cursor` 为不透明字符串（base64url(payload)+"."+HMAC-SHA256 前 16 字节），payload 含 dataset、last_key、query_hash、exp。
- 游标 15 分钟过期 → 410 `CURSOR_EXPIRED`；被篡改 → 400 `CURSOR_INVALID`；用于另一个查询 → 400 `CURSOR_QUERY_MISMATCH`。
- 最后一页 `next_cursor` 为 `null`；不提供总数（避免全表 count），`row_count` 是当前页行数。

### A9 敏感数据与角色裁剪

- 读取前按 #29 `evaluate(subject, "SENSITIVE_DATA", "VIEW", resource)` 裁定：`resource = {dataset, sensitivity, sensitive_fields, team, owner, energy_month}`。
- 行级范围：`ALL / GRANT / SETTLEMENT / SELF / TEAM / RELATED / TECH` 由 #29 解析；SETTLEMENT 只放行 D06/D09/D10 且 `energy_month` 在近 12 个月内。
- 字段级掩码：PUBLIC 不掩；SENSITIVE 按单元格 `mask`；HIGHLY 默认掩码，只有 GRANT + `expose=HIGHLY` + 双人会签才显数值（#29 AU12）。
- 掩码形式 `MASK-H<sha256 前 4 位>`；数值型不保留量级与排序。
- 响应 `meta.masked_fields` 列出被掩码字段；被掩码字段不出现在 `select` 的数值里，替换为掩码串。
- 聚合接口只对 HIGHLY 数据集开放，且分组后每组主体数 ≥5（k-匿名），否则 403 `K_ANONYMITY_VIOLATION`；聚合结果不带主体标识。
- 任何导出都走 `POST /exports` 的异步任务，再过 A08 出域网关；HIGHLY 一律拒绝（`DATA_EXPORT_DENIED`）。

### A10 审计留痕

- 每个请求（含 401/403/422）写一条审计事件，字段同 #29 AU18：`event_id/at/actor/roles/session_id/dimension/action/resource_type/resource_id/verdict/reason_code/scope/masked_fields/grant_id/request_id/prev_hash/hash`。
- 查询类额外记 `dataset`、`row_count`、`batch_ids`（去重后前 100 个）、`granularity`、`as_of`。
- 审计只追加（#29 AU20），接口无删除/修改审计的路径；审计查询受 `AUDIT:VIEW` 的 scope 限制。

### A11 错误语义

- 错误码是稳定枚举（`errors.json`），HTTP 状态固定映射，`retryable` 明确：
  - 可重试：`RATE_LIMITED`(429)、`DEPENDENCY_UNAVAILABLE`(503)、`TIMEOUT`(504)；
  - 不可重试：400/401/403/404/409/410/413/422。
- 400：请求格式/参数名/游标篡改；422：参数值语义不合法（范围、枚举、精度、引用缺失）。
- 403 必须带 reason_code（如 `NO_GRANT`、`OUT_OF_SETTLEMENT_SCOPE`、`FIELD_MASKED`、`K_ANONYMITY_VIOLATION`、`WRITE_FORBIDDEN`）。
- 5xx 不回内部细节；`request_id` 足以让运维定位。

### A12 查询接口

| 方法与路径 | 作用 | 效果 |
| --- | --- | --- |
| `GET /api/v1/me/capabilities` | 当前主体的 54 格压缩结果：可用维度/动作/数据域/掩码与受限项 | READ |
| `GET /api/v1/datasets` | 16 类数据集目录（编码、标题、敏感级别、粒度、可用状态） | READ |
| `GET /api/v1/datasets/{dataset}/schema` | 字段、类型、单位、精度、可过滤性、TBD 标记 | READ |
| `POST /api/v1/datasets/{dataset}/query` | 只读查询：过滤、时间范围、粒度、as_of、分页 | READ |
| `POST /api/v1/datasets/{dataset}/aggregate` | k≥5 聚合（模型/看板用），不带主体标识 | READ |
| `POST /api/v1/exports` | 异步导出任务（过出域网关），HIGHLY 拒绝 | PROPOSE |
| `GET /api/v1/exports/{export_id}` | 导出任务状态与下载票据（一次性、短时） | READ |
| `GET /api/v1/pages/{page_id}` | 四页面一次性数据（见 A15） | READ |
| `GET /api/v1/agent-runs/{run_id}` | 异步 Agent 运行状态与输出 | READ |
| `GET /api/v1/outputs/{output_id}` | #39 输出文档引用（未落地时 `PENDING_CONTRACT`） | READ |

查询请求体：

```json
{"select": ["business_date", "slot_no", "energy_mwh"], "filter": {"in": {"user_id": ["USER-4201-000123-X"]}}, "time_range": {"from": "2026-10-01", "to": "2026-11-01"}, "granularity": "slot", "as_of": "2026-11-05T12:00:00+08:00", "limit": 200, "cursor": null}
```

- `select` 为空表示契约默认列（business_key + 主要度量）；选择不存在字段 → 400 `UNKNOWN_FIELD`。
- 单次查询只允许一个数据集；跨数据集 join 由业务中心接口提供，不在通用查询里做（避免隐式笛卡尔积与越权拼接）。

### A13 Agent 工具契约

- 八大 Agent 的每个工具用 `<agent>.<tool>` 命名，登记在 `agents.json`，必须声明：`effect`、`input_schema`、`output_schema`、`requires_context`、`must_cite`、`sensitive_datasets`。
- 调用：`POST /api/v1/agents/{agent_id}/tools/{tool_id}/invocations`，必须带 `Idempotency-Key`（UUID v4）。
- 请求：`{"input": {...}, "context": {"as_of": "...", "forecast_origin": "P_CURVE", "locale": "zh-CN"}}`。
- 同步完成回 200 + `run_id` + `status=SUCCEEDED`；超 10 秒回 202 + `status=RUNNING`，用 `GET /api/v1/agent-runs/{run_id}` 轮询（`Retry-After`）。
- 输出统一：`{"run_id", "status", "output", "citations", "audit": {"model", "prompt_tokens", "completion_tokens", "latency_ms", "cost_cny", "data_export_verdict"}}`。
- `must_cite=true` 的工具（规则问答、规则变更解释）若无引用，整次调用失败 422 `CITATION_REQUIRED`，不返回无源回答。
- 预测类工具必须带 `forecast_origin`（#45 P_CURVE/P_BID/P_REVIEW），缺失 422 `FORECAST_ORIGIN_REQUIRED`；不同起点结果不得混算。
- 所有工具 `effect ∈ {READ, PROPOSE, SIMULATE}`；`EXECUTE` 是编译期非法值，加载即失败（A12）。
- 幂等：同一 `Idempotency-Key` + 同一输入（body SHA-256）24 小时内重放返回首次结果；同键不同 body → 409 `IDEMPOTENCY_KEY_REUSED`。
- Agent 调用模型一律经 LinkAI 网关；`model` 必须在白名单（Q06），不在白名单 → 403 `MODEL_NOT_ALLOWED`。

### A14 引用结构

```json
{"doc_id": "D01-2026-0007", "doc_no": "鄂能源调度〔2025〕66号", "title": "湖北省电力现货市场运营细则", "clause_no": "2.5", "version": "2026.1", "effective_from": "2026-01-01", "page_ref": "p.30 §2.5", "text_span": "……"}
```

- 五要素（文件、条款号、版本、生效日期、原文片段）缺一即不合格；`page_ref` 是定位辅助，可空但需说明。
- 引用串直接复用 #54 的 `qa_contract`，不另定义。

### A15 页面数据契约

- 页面数据由 `GET /api/v1/pages/{page_id}` 一次返回，页面不自行拼多个接口（避免同一屏不同 as_of）。
- 一期页面：`trader_home`、`supervisor_home`、`risk_audit_home`、`data_explorer`；页面外壳与导航由 #32 前端脚手架落地，本票只定数据形状。
- 块类型：`kpi`、`chart`、`table`、`quality`、`actions`、`output_ref`。
- 每块必须带：`id`、`type`、`title`、`source`（operation_id + 参数摘要 + dataset）、`state`（`ok|empty|partial|error|forbidden`）、`freshness`（`as_of`、`stale`）、`quality`（coverage、quality_counts）、`notes`、`actions`。
- 空态：`state=empty` + `notes` 说明是「无数据」「无权限」还是「源不可达」，禁止用 0 填充。
- 动作只能是 `capabilities` 里出现的能力（如 `STRATEGY_ADVICE:VIEW`），且不允许「执行真实交易」类动作。
- 预测/归因类块用 `output_ref: {output_id, contract: "#39", status: "PENDING_CONTRACT"}` 引用 #39 的文档，本票不复制其字段；#39 落地后把 `status` 改为 `READY` 并填 `output_id`。

### A16 版本与兼容

- 主版本在路径（`/api/v1`）；新增字段/枚举值为 minor，不破坏旧客户端；删除/改语义升 `/v2`。
- 弃用：响应头 `Deprecation` 与 `Sunset`（≥90 天），文档标注替代项。
- `contract_version` 变化不改变 API 版本号，但客户端应按字段名而非位置解析。

### A17 限流与容量

- 每账号 120 请求/分钟；查询接口 30 请求/分钟；导出 5 个/小时；超限 429 + `Retry-After`。
- 查询超时 10 秒（504 `TIMEOUT`）；导出与 Agent 异步任务单次 ≤30 分钟，超时标记失败并留审计。

### A18 与上下游的对齐点

- 与 #33：字段、单位、精度、敏感级别直接引用契约文件，接口不维护第二份字段表。
- 与 #29：鉴权、范围、掩码、审计全部调用同一 `evaluate()`；`/me/capabilities` 是矩阵的唯一对外投影。
- 与 #25：只暴露逻辑数据集，不暴露物理表与 `src_*` 列；查询下推到存储层的 `query_as_of`。
- 与 #45/#39：预测类工具必须带 `forecast_origin`；输出字段归 #39，API 只做信封与引用。
- 与 #47：实现票按本契约落地查询 API 与预览页，不另行定义错误码/分页/掩码。
- 与 #32：技术栈与目录结构由 #32 定；本契约只约束 HTTP 面与 JSON 形状。

## 4. 错误码目录（摘录）

完整 34 条见 `errors.json`；核心：

| code | HTTP | 重试 | 触发 |
| --- | --- | --- | --- |
| `INVALID_ARGUMENT` | 400 | 否 | 参数格式/未知参数 |
| `UNAUTHENTICATED` | 401 | 否 | 无令牌/令牌失效 |
| `FORBIDDEN` | 403 | 否 | 维度/动作未授权 |
| `NO_GRANT` | 403 | 否 | 需显式授权而无有效 grant |
| `OUT_OF_SETTLEMENT_SCOPE` | 403 | 否 | 结算范围外数据集/月份 |
| `FIELD_MASKED` | 403 | 否 | 用掩码字段做过滤 |
| `K_ANONYMITY_VIOLATION` | 403 | 否 | 聚合组主体 <5 |
| `WRITE_FORBIDDEN` | 403 | 否 | 任何写真实交易系统的尝试 |
| `RANGE_TOO_LARGE` | 422 | 否 | 超粒度范围上限 |
| `CITATION_REQUIRED` | 422 | 否 | 需引用工具无引用 |
| `FORECAST_ORIGIN_REQUIRED` | 422 | 否 | 预测工具缺起点 |
| `CURSOR_EXPIRED` | 410 | 否 | 游标过期 |
| `IDEMPOTENCY_KEY_REUSED` | 409 | 否 | 同键不同输入 |
| `RATE_LIMITED` | 429 | 是 | 超限流 |
| `DEPENDENCY_UNAVAILABLE` | 503 | 是 | 存储/模型网关不可用 |
| `TIMEOUT` | 504 | 是 | 查询/任务超时 |

## 5. 验收对照

| 票面验收 | 落点 | 自检 |
| --- | --- | --- |
| 前后端可契约先行并行开发 | `openapi.json` 全量路径 + `page-data.schema.json` | E1/E9 |
| 查询契约可先落地、不等预测侧 | 查询接口只依赖 #33/#29；预测块用 `output_ref pending` | E9/E12 |
| 错误语义统一；不暴露内部表；越权裁剪 | `errors.json` + 物理名扫描 + `evaluate()` 投影 | E5/E6/E8 |
| 不存在写入真实交易系统的接口 | effect 枚举无 EXECUTE；路径/动词扫描 | E8 |

### 参考实现与自检

| 文件 | 作用 |
| --- | --- |
| `openapi.json` | 11 条路径、39 个 schema；每个操作带 `x-effect` 与错误响应的 `x-error-codes` |
| `errors.json` | 34 条稳定错误码：HTTP、retryable、when、user_action |
| `agents.json` | 8 个 Agent / 10 个工具；effect、输入输出 schema、requires_context、must_cite、sensitive_datasets |
| `page-data.schema.json` | 四页面块契约 + `allOf` 空态/非 ok/引用约束 + `x-page-catalog` |
| `reference_api.py` | 定点小数、信封、游标 HMAC、查询校验、`evaluate()` 投影、能力压缩、页面校验、工具调用与幂等、无写扫描 |
| `verification.txt` | E1–E12 全 PASS（12/12） |
| `samples/` | 查询请求/响应、页面数据、工具调用各一份可读样例 |

自检覆盖：契约结构（E1）、信封与定点小数（E2）、游标篡改/过期/跨查询（E3）、查询范围与过滤（E4）、六角色投影与双人会签解掩（E5）、错误码完整性与 orphan（E6）、幂等重放（E7）、无写/EFFECT 扫描（E8）、四页面目录与空态/动作越界（E9）、Agent 引用与预测起点（E10）、序列化不变性（E11）、#39 对齐（E12）。

## 6. 下游接口

- #47 实现：按 `openapi.json` 实现 7 个数据查询端点（能力/目录/字段/query/aggregate/exports×2）+ 页面、agent-run、output 三个引用端点，以及游标、错误码、掩码与审计。
- #50 登录与六角色权限落地：`/me/capabilities` 的投影与 `evaluate()` 接线。
- #39 输出契约：填充 `output_ref` 的字段形状；本票预留 `pending` 状态。
- #55/#56/#61 等业务中心：各自输出接口复用本票信封、错误码、分页与引用结构。
- #32 工程结构：本契约不绑框架；实现票按 `operationId` 注册路由。

## 7. 尚未指定（fog）

- 客户 IT 条件决定是否走 API 网关/反向代理（限流、mTLS、IP 白名单）；当前只定应用层限流。
- 页面实时刷新是轮询还是 SSE/WebSocket；一期按轮询 30 秒设计，实时推送留给 #32。
- 文件导出的一次性下载票据有效期与存储介质（对象存储/本地），待部署形态定。
- 规则全文检索是否作为独立端点开放（v1 只提供结构化过滤；问答走 Agent 工具）。
- 多租户/多组织隔离是否需要（当前单公司单组织；Q02 组织数据定稿后再议）。
- 国际化：`locale` 字段已留，但一期只做 zh-CN。
- `GET /pages` 的块级缓存与个性化参数（不同角色的块集合差异）是否由服务端缓存，待性能基线。

## 8. 变更记录

- 2026-11-05 v1.0：首版；信封、错误码、查询、分页、掩码、审计、Agent 工具、页面块、验收对照与可执行自检。
