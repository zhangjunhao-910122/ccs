# 组合负荷预测特征与口径规格（含防穿越）

- 票：[规格：组合负荷预测特征与口径（含防穿越）](https://github.com/zhangjunhao-910122/ccs/issues/45)（#45，父地图 #22）
- 版本：v1.0（2026-11-05）
- 上游：[预测方法基线研究](../../research/forecast-method-baseline.md)（#23）、[统一数据契约](../data-contract/SPEC.md)（#33）、[数据源注册表](../data-source-registry/SPEC.md)（#44）、[统一时间栅格](../time-grid/SPEC.md)（#26）、[公开数据源可达性](../../research/data-source-reachability.md)（#43）
- 可执行产物：`feature-manifest.json`（特征台账）、`forecast.schema.json`（预测记录/归因/模型注册表）、`reference_forecast.py`（参考实现 + E1–E10 自检）、`samples.jsonl` + `samples/load_history.csv`（样例）、`verification.txt`（自检输出）

## 1. 问题与目的

方案 M04 要求「单用户次日 24 小时负荷预测」与「代理组合负荷预测」，但「次日」在业务上有三个不同的截止时刻，「特征」在没有可得性标注时无法判断是否偷看了未来，「组合」在没有代理关系快照时无法复算。本规格把这四件事定死：

1. 预测对象与组合复算口径（谁能加、加哪些人、缺人怎么办）；
2. 三个预测起点及其信息截止时刻（曲线分解 / 报量报价 / 复盘），默认取业务上真正卡脖子的那个；
3. 特征台账与可得时刻规则（无台账不入模，越界即报错）；
4. 基线、指标、归因与预测记录的口径（基线不许偷看未来，归因不许下因果结论）。

本期只做**口径与参考实现**，不训练正式模型、不锁定精度阈值（阈值由 #31/#37 在基线跑通后写死）。

## 2. 第一性原理

1. **预测的价值由「做出决定的时刻」决定，而不是由目标日的零点决定。** 同一个 D 日 24 点曲线，在 D-2 12:00 做和 D-1 20:00 做，可用信息完全不同；不把起点钉死，任何精度比较都没有意义。
2. **可得性是可执行属性，不是文档注释。** 每条特征必须带机器可判定的 `available_at`；构造期断言 `available_at <= forecast_origin`，越界抛错。靠人 review 防穿越，等于没有防。
3. **组合是派生量，不是独立事实。** 组合负荷只能由代理关系快照内的用户级小时电量求和得到；求和范围、缺口比例、缺失用户必须随预测一起记录。
4. **缺口只能显式，不能补零。** 缺一个用户补 0，等价于凭空造出一块负荷；正确做法是标 `PARTIAL` 并退出验收统计。
5. **基线也必须遵守同一套可得性。** 若基线能用 D-1 实测而模型不能，比较的是「信息量」不是「方法」。
6. **归因只解释模型，不解释世界。** 特征贡献是统计关联；写成因果即是越界（G2/#28）。
7. **记录不可变。** 预测一旦发出，只能追加新版本；事后改数会让回测与审计同时失效。

## 3. 决策（L1–L13）

- **L1 两级对象。** 单用户负荷 = `D04(user_id, business_date, slot_no).energy_mwh`；组合负荷 = 代理关系快照内用户级电量之和。组合不落独立数据集，只落派生视图；任何组合数必须能由用户级复算（验收②）。区域电网负荷（F7）只作参考特征，不作目标量、不与本项目指标混算。
- **L2 三个预测起点，默认 `P_CURVE`。** 起点是**信息截止时刻**，不是运行时刻；horizon 固定 24 小时（目标日 00:00–24:00）：

  | 起点 | 时刻 | lead | 业务用途 | 依据 |
  |---|---|---|---|---|
  | `P_CURVE`（默认） | D-2 12:00 | 36h | 生成 D 日 24 点曲线，供 D-2 17:00 前提交调度 | 湖北细则 D-2 17:00 前分解曲线（#23 §5.1） |
  | `P_BID` | D-1 08:00 | 16h | D-1 08:30 报量报价前的最后可得信息 | 湖北细则 D-1 08:30（#23 §5.1） |
  | `P_REVIEW` | D-1 20:00 | 4h | 日前出清后复盘、影子运行、教学回放 | 湖北细则 D-1 20:00（#23 §5.1） |

  售电公司当前以「不报量不报价」方式参与现货（#23 §2.1 F2），真正卡业务的是 D-2 17:00 的曲线提交，故默认 `P_CURVE`；`P_BID` 保留给未来参与报量报价或客户要求。三个起点的训练窗、回测窗、指标**分别计算、不得混算**，报告中必须标 origin。
- **L3 特征台账九列，无台账不入模。** 每条特征登记 `feature_id / family / definition / source_dataset / source_system / availability / shift_days / allowed_origins / required`；`feature_set_hash = sha256(规范化后的实际使用特征子集)`，随模型版本与每条预测记录保存。
- **L4 可得时刻四类规则。** `STATIC`（合同、日历等签署/发布即得，按 as-of 版本）、`EVENT`（业务日偏移 + 发布时刻，如 D-1 07:30 事前信息）、`OBS_LAG`（业务日 + lag 天 + cutoff，如计量数据次日推送）、`NEVER`（永不入模，记录原因）。规则写进 manifest，由代码解析，不写散文。
- **L5 运行类强制 shift。** D04 实测负荷 `shift >= 1` 天；D09/D10 结算数据 `shift >= 2` 天；实时价不得用于日前预测；D07 申报按 `submitted_at` 判定；D13 生产修正按提交时刻判定；D11 只能用**预报值**，实况/再分析只能做后验校验，不得入模。
- **L6 构造期硬断言。** `available_at > forecast_origin` → `FeatureLeakageError`（错误，不是警告）；特征不在该起点的 `allowed_origins` → `FeatureNotAllowed`。两条闸都在 `build_feature_matrix` 里，任何模型、任何回测都不能绕过。
- **L7 缺失显式，不插值。** `missing_reason ∈ {SOURCE_UNAVAILABLE, NOT_YET_PUBLISHED, NOT_PROVIDED, FORECAST_NOT_ARCHIVED}`；缺失特征的值为 `null`，允许的降级只有 `DROP_FEATURE` 或 `FALLBACK_BASELINE`，且必须写进预测记录。禁止均值/前值/零值填充。
- **L8 组合复算与缺口。** 用户集合取目标日有效的代理关系快照（D06 的 `term_start <= D <= term_end`，按 as-of 版本）；组合值 = 集合内有值用户之和；`coverage = 有值用户数 / 应含用户数`。`coverage < 0.99` → `is_complete=false`、状态 `PARTIAL`，该时点不进入验收统计；缺的用户列名，不补 0。组合误差不必等于用户误差的加权平均，报告须同时给两级。
- **L9 峰平谷按鄂发改价管〔2024〕77 号。** 时段由「月份类别 × slot」决定：7/8 月尖峰 20–22，其他月份尖峰 18–20；高峰 7/8 月 16–20、22–24，其他月份 16–18、20–24；平段 6–12、14–16；低谷 0–6、12–14。仅 100 kVA 及以上工商业用户在范围内，范围外 `tariff_segment=null` 且原因 `OUT_OF_SCOPE`；湖北现行无深谷，`DEEP_VALLEY` 保留不用。分层指标只统计非空时段。
- **L10 基线按「最近可得日」对齐。** N1 = 最近可得日同刻；N2 = 最近 7 个可得日同刻中位数；N3 = 同星期几最近 8 周中位数（主基线）。可得日由 `available_at <= origin` 反推，因此 `P_CURVE` 下最近完整日是 D-3，而不是 D-1；基线不得使用比模型更晚的信息。三者与模型同窗同起点，逐时点对齐。
- **L11 指标口径。** 主指标 MAE / RMSE / nMAE（MAE ÷ 测试期真实值绝对均值）；MAPE 仅作补充，计算时剔除 `|y| < ε`，`ε` 取该序列历史负荷 P1 并写死，报告剔除数与比例，比例 >10% 标「不适用」。区间预测报 PICP(P10,P90) 与平均宽度；无分位数时显式写「覆盖率不适用」，不留空。组合与单用户分别报，单用户另报中位数、P90、最差 Top-N。
- **L12 归因受 G2 约束。** 只到特征贡献层；每条归因必带四项（来源与核验状态、时间窗、置信度、固定句「基于历史统计关联，非因果判定」）；禁用词见 §9；出现「无法归因」四条件之一时，必须直接说无法归因，不得给贡献排序。
- **L13 96→24 降采样显式标记。** 系统负荷预测、新能源、联络线等 96 点外部数据降到 24 点时，按 4 点算术平均，并在特征值上标 `source_resolution=15min`、`derived_to=hourly`；不得把 96 点指标与 24 点指标混算。

## 4. 特征台账（21 条）

`feature-manifest.json` 是唯一执行依据；下表为摘要。`ALL` 表示三个起点都允许。

| feature_id | 族 | 来源 | 可得规则 | shift | 允许起点 | 必需 |
|---|---|---|---|---|---|---|
| `f_load_same_hour_lag1` | F1 | D04 | OBS_LAG 1d 00:00 | 1d | ALL | 是 |
| `f_load_med7` | F1 | D04 派生 | OBS_LAG 1d | 1d | ALL | 否 |
| `f_load_med_weekday8` | F1 | D04 派生 | OBS_LAG 1d | 1d | ALL | 否 |
| `f_load_peak_stats` | F1 | D04 派生 | OBS_LAG 1d | 1d | ALL | 否 |
| `f_agency_user_set` | F2 | D06 | STATIC（合同生效） | 0 | ALL | 是 |
| `f_contract_energy` | F2 | D05/D06 | STATIC（签署） | 0 | ALL | 是 |
| `f_retail_package` | F2 | D06 | STATIC（签署） | 0 | ALL | 否 |
| `f_declared_energy` | F2 | D07 | EVENT `submitted_at` | 0 | ALL | 否 |
| `f_deviation_assessment` | F2 | D09 | OBS_LAG 2d | 2d | ALL | 否 |
| `f_weather_forecast_temp` | F3 | D11 | EVENT 预报发布 | 0 | ALL | 否 |
| `f_weather_forecast_humidity` | F3 | D11 | EVENT 预报发布 | 0 | ALL | 否 |
| `f_calendar_day_type` | F4 | D12 | STATIC（年度公布） | 0 | ALL | 是 |
| `f_calendar_position` | F4 | D12 派生 | STATIC | 0 | ALL | 否 |
| `f_production_correction` | F5 | D13 | EVENT `submitted_at` | 0 | ALL | 否 |
| `f_agency_change` | F6 | D06 | STATIC（签署） | 0 | ALL | 是 |
| `f_system_load_forecast` | F7 | EXTERNAL_HUBEI | EVENT D-1 07:30 | 0 | `P_BID`,`P_REVIEW` | 否 |
| `f_renewable_forecast` | F8 | EXTERNAL_HUBEI | EVENT D-1 07:30 | 0 | `P_BID`,`P_REVIEW` | 否 |
| `f_tie_line_plan` | F8 | EXTERNAL_HUBEI | EVENT D-2 17:00 | 0 | `P_BID`,`P_REVIEW` | 否 |
| `f_da_price` | F9 | EXTERNAL_MARKET | EVENT D-1 20:00 | 0 | `P_REVIEW` | 否 |
| `f_rt_price` | F9 | EXTERNAL_MARKET | NEVER | — | — | 否 |
| `f_settlement_energy` | F10 | D09 | OBS_LAG 2d | 2d | ALL | 否 |

`f_monthly_settlement`（D10，事后月结）登记为 `NEVER`：任何事后修正的月度/年度结算数据都不得进入特征（保留在 manifest 里是为了让校验器能明确拒绝，而不是「没登记所以能混进来」）。

## 5. 防穿越：规则与已知泄漏点

### 5.1 四条硬规则

1. 每条特征有且只有一个可得规则；规则可解析成 `available_at` 时间戳。
2. `build_feature_matrix` 对每条特征、每个时点断言 `available_at <= origin`；不满足直接抛 `FeatureLeakageError`，测试与线上同一条代码路径。
3. 起点不允许的特征在装配前就被拒绝（`FeatureNotAllowed`），不靠「值为空」蒙混。
4. 任何派生特征（滚动中位数、同星期中位数、峰谷统计）的 `available_at` 取所有输入的最大值；输入越界，派生也越界。

### 5.2 已知泄漏点（来自 #23 §5.4，逐条落成校验）

| 泄漏点 | 为什么危险 | 本规格的处理 |
|---|---|---|
| D 日实测负荷 | D+1 才推送 | D04 特征一律 OBS_LAG 1d；`P_CURVE` 下实际只能用 D-3 及更早 |
| D 日结算电量/偏差 | D+2 才发布 | D09 特征 OBS_LAG 2d，shift=2 |
| 实时价 | T-15 才发布，且是同时段结果 | `f_rt_price` 登记 NEVER，日前预测不得使用 |
| 事后修正的月/年结算 | 事后数据 | `f_monthly_settlement` 登记 NEVER |
| 历史天气实况冒充预报 | 实况是事后观测 | D11 只接受预报序列；无预报档案时按缺失处理，不用 archive 实况替代 |
| 系统负荷预测当目标 | 区域电网口径不同 | F7 只作参考特征，禁止作标签或与组合指标混算 |

### 5.3 缺失与降级

- 缺预报档案（`FORECAST_NOT_ARCHIVED`）：天气两条特征默认缺失，模型走 `DROP_FEATURE`，记录降级；**不**用 Open-Meteo archive 的历史实况顶替（#43 的 archive 是再分析/实况，不是预报回算）。
- 客户未提供生产计划（`NOT_PROVIDED`）：F5 缺失，组合口径精度上限下降，写进报告风险段。
- 尚未发布（`NOT_YET_PUBLISHED`）：在 `P_CURVE` 下，F7/F8/日前价都属此类，直接缺失。

## 6. 组合复算

```text
A(D)      = { u | D06(u).term_start <= D <= term_end, 版本按 as-of 取当前有效 }
P(D, s)   = Σ_{u ∈ A(D), load_u(D,s) 有值} load_u(D,s)
coverage  = |有值用户| / |A(D)|
is_complete = coverage >= 0.99
```

- `A(D)` 取的是**目标日**的代理关系，不是建模日的；退网用户在 D 日已不在集合内，新签用户在 D 日已进入集合。
- 缺用户不补 0；`PARTIAL` 时点只作展示，不进 MAE/RMSE 等验收统计。
- 组合与单用户分别报；单用户门槛（样本量、最低负荷、是否含零负荷）尚未定，写入 fog，由 #31 定。

## 7. 基线与评估

- 基线：N1 最近可得日同刻、N2 最近 7 个可得日同刻中位数、N3 同星期几最近 8 周中位数（主基线）。`P_CURVE` 下「最近可得日」= D-3，`P_BID/P_REVIEW` 下 = D-2；由 `latest_available_date()` 统一计算，不手填。
- 回测：rolling origin，horizon 24h、步长 1 天，预处理只在训练窗 fit；每期等权汇总到「所有期 × 所有时刻」。
- 指标：见 L11。阈值不预填；#31 跑出基线后，由 #37 写死「相对 N3 的 MAE 改善」与覆盖率目标。
- 96 点外部数据降采样后必须标 `source_resolution`，不得与 24 点原生数据混算指标。

## 8. 峰平谷时段（鄂发改价管〔2024〕77 号）

| 时段 | 7/8 月 | 其他月份 | 浮动比（平=1） |
|---|---|---|---|
| 尖峰 SHARP_PEAK | 20:00–22:00（slot 21–22） | 18:00–20:00（slot 19–20） | 平常 1.8；7/8、12/1 月 2.0 |
| 高峰 PEAK | 16:00–20:00、22:00–24:00（slot 17–20、23–24） | 16:00–18:00、20:00–24:00（slot 17–18、21–24） | 1.49 |
| 平 FLAT | 6:00–12:00、14:00–16:00（slot 7–12、15–16） | 同左 | 1 |
| 低谷 VALLEY | 0:00–6:00、12:00–14:00（slot 1–6、13–14） | 同左 | 平常 0.48；7/8、12/1 月 0.45 |

- 每日 2+6+8+8 = 24 段；`DEEP_VALLEY` 在湖北现行文件中不存在，保留枚举但不用。
- 实施范围仅 100 kVA 及以上工商业用户（排除电气化铁路牵引用电、商业用电、非居民照明等）；范围外 `tariff_segment=null`，原因 `OUT_OF_SCOPE`，不参与分层指标。
- 季节类别：SUMMER = 7/8 月，WINTER = 12/1 月，其余 NORMAL；时段表只分「7/8 月」与「其他月份」，浮动比另按季节。

## 9. 归因与措辞（G2）

- 归因输出的是**特征贡献**（`feature_id, contribution, direction`），不是业务因果。
- 每条归因文本必带四项：①数据来源与核验状态（已核验/未核验/缺失）；②时间窗；③置信度（分位区间或等级）；④固定句「基于历史统计关联，非因果判定」。
- 禁用词：因为、因此、原因、导致、保证、必然、一定会、稳赚、建议买入、建议卖出、预测一定、肯定能、由于，以及单独作连词的「因」（「非因果判定」固定句除外）。实现用正则 `(?<!原)因(?!果)` 兜住「因高温」这类写法。
- 「无法归因」四条件：目标源缺失/未核验；特征缺失占比超阈值（阈值待 #31 定）；样本出训练分布；回测中运行类特征未 shift。命中任一即不得给贡献排序。
- 置信度默认用分位区间（P10–P90）；只有具备回测校准证据（校准区间 + 样本量）才可给概率表述。

## 10. 数据模型（`forecast.schema.json`）

| 表 | 关键字段 | 约束 |
|---|---|---|
| `model_registry` | model_id, version, family, feature_set_hash, training_window, hyperparams, created_at, status | `(model_id, version)` 唯一；启用状态显式 |
| `forecast_run` | run_id, origin_type, forecast_origin, target_date, model_id, model_version, feature_set_hash, degraded_features, created_at | 只追加；同 run 重跑生成新 run_id |
| `forecast_prediction` | run_id, entity_type(USER/PORTFOLIO), entity_id, slot_no, p10, p50, p90, point, missing_features, tariff_segment | `(run_id, entity_type, entity_id, slot_no)` 唯一 |
| `forecast_attribution` | run_id, entity_type, entity_id, slot_no, feature_id, contribution, direction, text, evidence | 文本必须过 G2 校验；`text` 不得为空 |

预测记录不可原地更新；修正以新 run 追加，旧 run 保留。

## 11. 验收对照

| # | 验收项 | 证据 |
|---|---|---|
| ① | 特征表带可得性标注，运行类全部经 shift 检查 | `feature-manifest.json` 21 条全带 `availability/shift_days/allowed_origins`；E1 校验九列与 shift 规则，E2 越界抛错，E3 起点禁用生效 |
| ② | 组合聚合可由单用户负荷复算并与手工汇总一致 | §6 公式；E4 三用户求和、缺口 `PARTIAL`、不补零 |
| ③ | 外部特征缺失行为明确（标注+影响），不静默填充 | L7 + §5.3；E6 天气无预报档案 → `FORECAST_NOT_ARCHIVED`、值为 `null`、降级记录 |
| ④ | 归因措辞通过 G2 禁用词检查 | §9；E7 禁用词拦截、四项缺失拦截、无法归因 |
| ⑤ | 基准模型与评估窗口切分口径写死 | §7；E5 三基线按可得日对齐、与手工一致；E8 指标剔除与覆盖率 |

补充自检：E9 校验峰平谷 24 段全覆盖与 77 号浮动比；E10 校验 96→24 降采样与 `source_resolution` 标记。

## 12. 下游接口

- **#31 朴素基线与滚动回测框架**：直接调用 `latest_available_date` / `naive_baselines` / `metrics`；基线必须同窗同起点；三变体全实现。
- **#37 回测报告与达标判定**：报告字段按 L11；阈值在基线确认后写死；必须附「时点自证」章节（每条特征断言结果、shift 记录、rolling 切分示意）。
- **#39 预测与分析输出契约**：点预测/区间/归因/人工修正/偏差风险的字段与本规格 §9、§10 对齐；归因文本渲染前调用 `check_attribution`。
- **#48 可切换模型注册表与训练推理流水线**：模型版本必须绑定 `feature_set_hash`；特征集变更即新版本，不得复用旧版本号。
- **#38 原型：负荷预测结果页**：页面至少展示 origin、model_version、missing_features、组合 coverage 与 `PARTIAL` 标记。
- **#61 验收指标与阶段门禁**：精度门禁只认「相对 N3 改善 + 覆盖率」双条件，禁止固定 MAPE 单判。

## 13. 尚未定（fog）

1. **客户数据能力**：D04 的采集级时标是否支持「D-3 及更早可查」；若只有结算级（D+2），`P_CURVE` 的基线可用日退到 D-4，需按真实链路重算。
2. **气象源与历史预报回算**：需要「当时发布的预报」序列；Open-Meteo archive 是实况/再分析，不能替代。未取得前天气特征默认缺失。
3. **生产计划/检修数据**：客户是否提供、以什么表单、提交时刻如何记录（决定 F5 能否入模）。
4. **代理组合口径的分子分母**：是否含线损、是否含电网企业代理购电用户、是否按市场化电量比例折算（#23 N7）。
5. **单用户纳入门槛**：有效小时数下限、最低负荷量、是否含零负荷时段。
6. **模型选型**：线性/GBDT/分位数的最终选择由基线结果决定，本规格只定接口与口径。
7. **是否参与报量报价**：若长期不参与，`P_BID` 只保留为教学/演练口径，不进入正式验收。

## 14. 变更记录

- v1.0（2026-11-05）：首版。落定三级起点、21 条特征台账、组合复算、峰平谷映射（77 号）、基线与指标口径、G2 归因约束；E1–E10 自检全通过。
