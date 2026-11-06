# 朴素基线与滚动样本外回测框架规格

- 票：[实现：朴素基线与滚动样本外回测框架](https://github.com/zhangjunhao-910122/ccs/issues/31)（#31，父地图 #22）
- 版本：v1.0（2026-11-06）
- 上游：[组合负荷预测特征与口径规格](../load-forecast/SPEC.md)（#45）、[统一数据存储规格](../data-storage/SPEC.md)（#25）、[预测方法基线研究](../../research/forecast-method-baseline.md)（#23）、[公开数据源可达性](../../research/data-source-reachability.md)（#43）
- 可执行产物：`backtest.config.json`（回测配置）、`reference_backtest.py`（参考实现 + E1–E10 自检）、`samples/load_history_backtest.csv` + `samples/missing_points.csv`（历史负荷样例与缺测清单）、`samples/business_calendar_2026.csv`（业务日历）、`out/backtest_period_rows.csv`（逐点明细）、`out/backtest_error_table.csv`（误差表）、`out/backtest_summary.json`（配置快照与摘要）、`verification.txt`（自检输出）

## 1. 问题与目的

本票交付「朴素基线 + 滚动样本外回测框架」，作为 #37（回测报告与达标判定）、#48（可切换模型注册表与训练推理流水线）、#51（回测报告形态原型）三张下游票的共用底座。框架要一次性把五件事钉死，否则下游每一张票都会各写一套、口径互相打架：

1. **回测怎么切**：滚动样本外（rolling origin）的切点、窗口、评估期长度全部写进配置，且同样配置必得同样结果；
2. **基线族怎么算**：可用信息集合由「可得性反推」决定，基线与模型使用**同一套**信息边界，基线不许偷看未来；
3. **指标怎么算**：指标口径只有一份实现，基线与后续模型共用，后续模型的成绩才有可比性；
4. **缺测怎么办**：缺测显式、不插值、不补零、且不计入误差统计（否则精度会被凭空抬高）；
5. **日历怎么用**：日历是**分层维度**，不是过滤器——电负荷每天都在，预测每天都要做。

本期只做**口径与参考实现 + 误差表**，**不锁定精度阈值**。按地图 #22 决策 Q28「已明确门槛 + 基线后锁定门槛」双轨，阈值由 #37 在基线结果确认后写死。

**诚实边界**：本票验收①要求「跑在真实入库数据上」。截至本期，仓内只有合成样例（`docs/spec/load-forecast/samples/load_history.csv` 与本目录 `samples/load_history_backtest.csv` 均为合成数据，非客户真实入库数据），真实数据待地图 #22 决策 Q02（客户样本）到位。因此本期全部误差表与摘要均带 `data_label=SAMPLE-SYNTHETIC` 标识；真数据到位后用同一条命令重跑，把 `data_label` 改为 `REAL` 即可，代码与口径不变。验收①在真实数据回填前**不视为闭环**。

## 2. 第一性原理

1. **预测的价值由「做出决定的时刻」决定，而不是由目标日的零点决定。** 同一个 D 日 24 点曲线，在 D-2 12:00 做和 D-1 20:00 做，可用信息完全不同；起点不钉死，任何精度比较都没有意义。
2. **回测不是「用全部历史训练再预测未来」，而是「在每个时点假装只知当时可知的信息」。** 所以必须逐期重算可得集合（rolling origin），而不是切一次训练集、一次评估。
3. **可得性是可执行属性，不是文档注释。** 每条输入必须带机器可判定的 `available_at`；构造期断言 `available_at <= forecast_origin`，越界抛错。靠人 review 防穿越等于没有防。
4. **基线也必须遵守同一套可得性。** 若基线能用 D-1 实测而模型不能，比较的是「信息量」不是「方法」。基线的取数日一旦越界，基线就变成了泄漏器。
5. **缺测只能显式，不能补。** 缺测点插值或补零，等价于凭空造出数据；正确做法是标缺测原因、显式退出误差统计，并报告覆盖率。
6. **指标口径只能有一份。** 基线与后续模型必须走同一个 `metrics()`；否则「模型比基线好」这句话没有意义。
7. **汇总口径必须先定，且两种口径不能混用。** 「所有期 × 所有时刻一次算」（pooled）与「各期指标再平均」（macro）是两件事，长窗口与短窗口下会给出不同结论；主口径写死为 pooled，macro 附报。
8. **日历是分层维度不是过滤器。** 节假日影响的是负荷分布形态，不是「该不该预测」；把休息日直接剔除，等于让模型逃避最难的那部分日子。
9. **基线是模型的及格线。** 先有基线再谈模型；基线结果被确认前，不许写死任何模型阈值（地图 #22 决策 Q28）。

## 3. 决策（L1–L13）

- **L1 三个起点，沿用 #45 定义，不另起炉灶。** `P_CURVE` = D-2 12:00（lead 36h，默认）、`P_BID` = D-1 08:00（lead 16h）、`P_REVIEW` = D-1 20:00（lead 4h）。起点是**信息截止时刻**；horizon 固定 24 小时。三个起点的回测窗、指标**分别计算、不得混算**，输出表按 `origin_type` 分列。本规格只把起点写进回测框架，起点本身的业务依据在 #45 SPEC L2。
- **L2 可得性反推，唯一算法 `latest_available_date()`。** 规则：`available_at(day) = day + shift_days 天 的 cutoff 时刻`；取满足 `available_at(day) <= origin` 的最近一天。配置 `shift_days=1`、`cutoff="00:00"` 时：数据 D 日在 D+1 00:00 可得，故 `P_CURVE`（D-2 12:00）最近可得日 = **D-3**，`P_BID`/`P_REVIEW`（D-1 08:00 / 20:00）最近可得日 = **D-2**。这三个值由代码统一计算，**任何文档、任何表格都不得手填**（E1 断言 `P_CURVE→2026-10-17`、`P_BID/P_REVIEW→2026-10-18`，目标日 2026-10-20）。`shift_days`/`cutoff` 是配置项：改为 2 时 `P_CURVE` 的可得日同步前移一天（E1 复核 `2026-10-16`）。若客户数据链路实际只到结算级（D+2），只需改配置，代码不动（#45 SPEC §13 第 1 条）。
- **L3 滚动切分：rolling origin，horizon 24h，步长 1 天。** 目标日序列由 `backtest.eval_start` / `eval_end` / `step_days` 生成，本期 28 期（2026-10-01 … 2026-10-28），每期对所有用户、所有时段各出一次预测。同一配置两次运行必得同一摘要指纹（`table_digest`，本期 `37938619cc84741f…`，E6 断言）。
- **L4 基线族四变体，全部实现、全部出表。**
  - `N1` = 最近可得日（anchor）同刻值；
  - `N2` = 最近 `n2_window_days`（默认 7）个**可得日**同刻的中位数；同时给窗口经验 P10/P90 作为区间（`interval_source=N2_WINDOW_EMPIRICAL`，窗口样本 < `interval_min_points` 则不出区间）；
  - `N3` = **目标日**星期几锚的最近 `n3_same_weekday_count`（默认 8）周中位数 → **主基线**；
  - `N3a` = **可得日**星期几锚的同口径中位数 → 口径对照，不是主基线。
- **L5 N3 的锚点写死为「目标日」，这是本规格对 #45 参考实现的细化，并给出数据裁决。** 第一性原理：星期几是**目标本身**最强的已知属性（工作日/周末/周内形态）；若按可得日锚，当 anchor 与 target 星期几不同（如 target 周日、anchor 周五），N3 就丢掉了目标日的星期效应，退化成 anchor 的基线。实测（本期 28 期 × 3 用户 × 24 时段，ALL 层 MAE）：`N3 = 0.367747` vs `N3a = 0.500925`（P_CURVE），差约 **27%**。#45 SPEC L10 的文字「同星期几最近 8 周中位数」并未规定锚点，#45 参考实现取的是可得日锚 —— 故本条属**口径细化与勘误**，不推翻 #45 已定结论。`N3a` 保留在误差表中，供复核与未来复核，不必删除。
- **L6 缺测：显式、不插值、不补零、不计入误差统计。** 缺测点在逐点明细中 `y_true` 为空，预测缺失时 `y_pred` 为空；进入误差统计的只有「真实值存在且预测存在」的点。因此 `n_points` 会小于 `n_expected`：`n_expected = 28 期 × 3 用户 × 24 时段 = 2016`，`N1` 实测 1996（anchor 日部分时刻缺测 → 无预测 → 不计分），`N2/N3/N3a` 实测 2006（真实值本身有缺测）。缺测原因清单见 `samples/missing_points.csv`，取值 `SOURCE_UNAVAILABLE`（沿用 #45 的 `missing_reason` 词表）。
- **L7 指标口径：主 MAE / RMSE / nMAE，MAPE 仅作补充，与 #45 完全一致。** `nMAE = MAE ÷ 测试期真实值绝对均值`；`MAPE` 计算时剔除 `|y| < ε`，`ε` = **全体用户合并、首个切点之前历史负荷的 P1**，写死在 `backtest.config.json`（`mape_eps_mode=P1_ALL_USERS_BEFORE_FIRST_ORIGIN`、`mape_eps_percentile=1.0`），本期实测 `ε = 0.747`（per-user P1 供参考、不参与计算：U000001 = 0.663、U000002 = 0.95927、U000003 = 1.22136）。报告剔除数与剔除比例，比例 > 10% 标「不适用」（`mape_applicable=false`）。ε 只由切点之前的数据决定 → 本身不构成泄漏。
- **L8 口径一致性由代码保证，不由文档保证。** `metrics()` 与 #45 `reference_forecast.metrics()` **逐键交叉校验**（E4：5 组用例、含空 pairs 组，逐键比对 `n/mae/rmse/nmae/mape/mape_applicable/mape_excluded/mape_excluded_ratio`）。基线与后续模型必须调用同一实现；#48 换模型不得换指标函数。
- **L9 汇总口径：主 pooled，辅 macro。** `pooled` = 所有期 × 所有时刻的样本一次性算指标（每点等权）；`macro` = 每期先算指标再对各期平均（每期等权）。两者数值不同属预期（E7 用两组可控数据复算：pooled MAE = 4.666667、macro MAE = 6.0）。输出表同时给 `mae/rmse/nmae/mape` 与 `macro_mae/macro_rmse/macro_nmae/macro_mape`，但**引用时必须标明口径**；跨票比较默认用 pooled。
- **L10 日历只作分层，默认不跳过目标日。** 分层维度 `stratum ∈ {ALL, 普通工作日, 普通休息日, 法定节假日, 调休上班日}`；`skip_non_business_days=false`（默认 28 期全跑），打开后只保留 `business_day_types`（默认「普通工作日 + 调休上班日」）内的目标日（本期 28 → 16 期，E9 断言「跳过后只剩工作日与调休上班日」）。日历数据源见 §6.3。
- **L11 泄漏防护是硬断言，不是警告。** 基线取数日必须 ≤ 该起点的可得日；越界抛 `LeakageError`（`assert_no_leak`）。E5 做双向验证：①对目标日取数直接抛错；②把目标日与未来日的数据注入历史后，N1/N2/N3/N3a 四组结果**完全不变**。
- **L12 配置即契约：一切可调项在 `backtest.config.json`，改配置不改代码。** 切点、窗口、评估期、步长、可得性 shift/cutoff、指标与 ε 规则、日历开关与业务日类型、汇总口径、数据标签全在配置里。E10 验证：改 `n2_window_days` 7→3 时 N2 的 MAE 由 0.403056 变为 0.40156；改 `eval_end` 变 14 期；同配置重复运行指纹相同。
- **L13 数据标签必须随结果走。** `data.data_label` 取 `SAMPLE-SYNTHETIC` 或 `REAL`，写入配置、误差表摘要与 `out/backtest_summary.json`；任何对外报告引用误差表时必须带该标签。**未核验数值不进自动判定**（地图 #22 硬约束）：带 `SAMPLE-SYNTHETIC` 的误差表不得用于任何达标判定。

## 4. 配置项（`backtest.config.json`）

| 段 | 键 | 本期取值 | 含义 |
|---|---|---|---|
| `data` | `history_csv` / `calendar_csv` | `samples/load_history_backtest.csv` / `samples/business_calendar_2026.csv` | 输入数据与日历 |
| `data` | `data_label` | `SAMPLE-SYNTHETIC` | 数据性质标签（L13） |
| `availability` | `shift_days` | `1` | 业务日 D 的实测数据在 D+`shift_days` 天可得 |
| `availability` | `cutoff` | `"00:00"` | 可得时刻；与 `shift_days` 共同决定可得日 |
| `availability` | `origin_types` | `P_CURVE,P_BID,P_REVIEW` | 本轮回测的起点集合，各自独立出表 |
| `backtest` | `eval_start` / `eval_end` / `step_days` / `horizon_hours` | `2026-10-01` / `2026-10-28` / `1` / `24` | 滚动切分（L3） |
| `backtest` | `n2_window_days` | `7` | N2 窗口天数 |
| `backtest` | `n3_same_weekday_count` | `8` | N3 回看的周数 |
| `backtest` | `interval_min_points` | `3` | 窗口经验分位出区间的最小样本数 |
| `backtest` | `primary_baseline` | `N3` | 主基线（L5） |
| `metrics` | `primary` / `supplementary` | `MAE,RMSE,nMAE` / `MAPE` | 指标分级（L7） |
| `metrics` | `mape_eps_mode` / `mape_eps_percentile` / `mape_excluded_ratio_limit` | `P1_ALL_USERS_BEFORE_FIRST_ORIGIN` / `1.0` / `0.1` | ε 来源与剔除规则（L7） |
| `calendar` | `skip_non_business_days` | `false` | 是否跳过非业务日目标日（L10） |
| `calendar` | `business_day_types` | `普通工作日, 调休上班日` | 跳过开关打开时保留的目标日类型 |
| `aggregation` | `mode` / `also_report` | `pooled_all_points` / `macro_of_period_metrics` | 汇总口径（L9） |

## 5. 回测算法

```text
for target in rolling_targets(cfg):                 # L3，由 eval_start/eval_end/step_days 生成
    origin  = origin_timestamp(origin_type, target)  # L1
    anchor  = latest_available_date(target, origin_type, shift_days, cutoff)   # L2
    for user in users:
        N1[s]  = series[anchor][s]
        N2[s]  = median(最近 n2_window_days 个可得日的同刻值)   # L4
        N3[s]  = median({target - 7k 天 : k=1..n3_same_weekday_count})   # L4/L5
        N3a[s] = median({anchor  - 7k 天 : k=1..n3_same_weekday_count})   # L4 口径对照
        assert_no_leak(取数日, target, origin_type, shift_days, cutoff)     # L11
    for s in 1..24:
        y = series[target][s]
        记录 (target, user, origin_type, baseline, s, y, y_pred, p10, p90, day_type)
汇总：pooled（所有点一次算，主）+ macro（各期指标再平均，辅）              # L9
分层：按 day_type 分层，全部层级与 ALL 并列输出                          # L10
```

要点：基线在**每一期、每一用户、每一时段**独立重算，不做跨期缓存；缺测点 `y` 或 `y_pred` 为空即不进入该组的误差统计（L6）。

## 6. 缺测、覆盖与防穿越

### 6.1 缺测处理

| 情形 | 处理 | 结果体现 |
|---|---|---|
| 真实值缺失 | `y_true` 空；该点不进误差统计 | `n_points < n_expected`；`truth_coverage < 1` |
| 预测缺失（如 N1 的 anchor 日该时刻无值） | `y_pred` 空；该点不进误差统计 | `scored_coverage < truth_coverage` |
| 缺测原因 | 记入 `samples/missing_points.csv`，取值 `SOURCE_UNAVAILABLE` | 可追溯 |
| 插值/补零 | **禁止** | 代码中无任何填充路径 |

### 6.2 泄漏点与拦截

| 泄漏点 | 为什么危险 | 本规格的处理 |
|---|---|---|
| 用目标日实测做预测 | 目标日当天才产生 | `available_at > origin` → `LeakageError`（L11） |
| 用切点之后的历史 | 用未来信息选窗口 | 可得日集合由 `available_at <= origin` 反推（L2） |
| 用未来日改进 MAPE 的 ε | 用未来分布定阈值 | ε 只由首个切点之前的数据算（L7） |
| 预处理/统计跨期共享 | 泄漏到训练窗 | 本期基线无拟合参数；模型接入时预处理只在训练窗 fit（#45 §7） |

### 6.3 日历数据来源

`docs/research/samples/交易日历/2026年逐日交易日标记表.csv`（366 行，源 `国办发明电〔2025〕7号` + timor.tech API），本目录 `samples/business_calendar_2026.csv` 为其副本，字段「日期, 星期, 日类型, 节假日名称, 依据」；日类型四值：普通工作日 242、普通休息日 84、法定节假日 33、调休上班日 6。注意：**逐日交易日历匿名不可得**（#43 结论），当前标记表只区分「法定节假日/调休上班日/普通工作日/普通休息日」；真实交易日历到位后以同样字段替换即可，L10 逻辑不变。

## 7. 产物结构与字段

| 文件 | 粒度 | 关键字段 |
|---|---|---|
| `out/backtest_period_rows.csv` | 期 × 用户 × 起点 × 基线 × 时段 | `period, user_id, origin_type, baseline, slot_no, y_true, y_pred, p10, p90, day_type` |
| `out/backtest_error_table.csv` | 起点 × 基线 × 分层 | `origin_type, baseline, stratum, n_periods, n_expected, n_points, truth_coverage, scored_coverage, mae, rmse, nmae, mape, mape_applicable, mape_excluded, mape_excluded_ratio, picp, avg_width, interval_source, macro_mae, macro_rmse, macro_nmae, macro_mape` |
| `out/backtest_summary.json` | 单文件摘要 | `config`（配置快照）、`data_label`、`mape_eps`、`mape_eps_per_user_p1`、`n_periods`、`periods`、`users`、`origin_types`、`table_digest`、`primary`（ALL 层指标） |

字段语义：`n_expected` = 期数 × 用户数 × 24；`n_points` = 实际进入误差统计的点数；`truth_coverage` = 有真实值的点 / `n_expected`；`scored_coverage` = 进入误差统计的点 / `n_expected`；`picp` 空且 `interval_source=NOT_APPLICABLE_POINT_FORECAST` 表示点预测无区间（不留空、不猜）。

## 8. 基线结果（`data_label=SAMPLE-SYNTHETIC`，28 期 × 3 用户 × 24 时段，ALL 层）

| 起点 | 基线 | n_points | MAE | RMSE | nMAE | MAPE | PICP |
|---|---|---|---|---|---|---|---|
| `P_CURVE` | N1 | 1996 | 0.458738 | 0.640744 | 0.128677 | 0.137380 | 不适用（点预测） |
| `P_CURVE` | N2 | 2006 | 0.403056 | 0.597255 | 0.112828 | 0.118576 | 0.605683 |
| `P_CURVE` | **N3（主基线）** | 2006 | **0.367747** | 0.563101 | 0.102944 | 0.114592 | 不适用（点预测） |
| `P_CURVE` | N3a（对照） | 2006 | 0.500925 | 0.685947 | 0.140224 | 0.151996 | 不适用（点预测） |
| `P_BID` | N1 | 1996 | 0.409720 | 0.577853 | 0.114750 | 0.124348 | 不适用（点预测） |
| `P_BID` | N2 | 2006 | 0.354954 | 0.545503 | 0.099362 | 0.106263 | 0.599701 |
| `P_BID` | **N3（主基线）** | 2006 | 0.367747 | 0.563101 | 0.102944 | 0.114592 | 不适用（点预测） |
| `P_BID` | N3a（对照） | 2006 | 0.504955 | 0.687243 | 0.141352 | 0.152441 | 不适用（点预测） |
| `P_REVIEW` | 同 `P_BID` | — | 同上 | 同上 | 同上 | 同上 | 同上 |

说明：
1. `P_BID` 与 `P_REVIEW` 数值完全相同 —— 两者最近可得日同为 D-2，可用信息集合一致，这是正确结果而非复制错误；差异会在可得日不同的链路上体现。
2. `N1` 的 `n_points` 比其余基线少 10：anchor 日有缺测时刻 → 该时刻无预测 → 不计分。
3. 只有 `N2` 给区间（窗口经验 P10/P90），PICP 约 0.60；点预测基线显式标「覆盖率不适用」。
4. **分层结果（P_CURVE / N3）**：

   | 分层 | 期数 | n_points | MAE | RMSE | nMAE | MAPE |
   |---|---|---|---|---|---|---|
   | 普通工作日 | 15 | 1078 | 0.194564 | 0.241017 | 0.050314 | 0.057547 |
   | 普通休息日 | 5 | 360 | 0.189218 | 0.228819 | 0.057611 | 0.068382 |
   | 法定节假日 | 7 | 496 | **0.831271** | 1.015056 | 0.271391 | 0.270796 |
   | 调休上班日 | 1 | 72 | 0.660167 | 0.777729 | 0.160694 | 0.153404 |

   法定节假日的 MAE 是普通工作日的约 4.3 倍：`N3` 用的是「同星期几」历史，**不区分法定节假日标记**，因此在节假日上系统性失准；调休上班日（周六上班）与普通休息日形态也明显不同。这直接证明两点：①日历必须做分层维度（L10）；②后续模型应把日历特征入模（#45 `f_calendar_day_type` / `f_calendar_position`），这也是 #37 写死达标门槛时必须按层给门槛的理由。
5. 以上数值全部来自合成样例，**不得用于任何达标判定**（L13）。

## 9. 自检对照（`verification.txt`，E1–E10 全 PASS）

| # | 检查项 | 断言 |
|---|---|---|
| E1 | 可得性反推 | `P_CURVE→2026-10-17`、`P_BID/P_REVIEW→2026-10-18`（目标日 2026-10-20）；`shift_days=2` 时 `P_CURVE→2026-10-16`；三个 origin 时间戳正确 |
| E2 | 基线手算一致 | 构造序列下 N1=30.0、N2=60.0、N3=88.5、N3a=129.5，取数日与预期集合逐日一致 |
| E3 | 缺测不插值不计分 | 缺测点的 `n` 不增；缺测日不计入可得日集合；`days_with_data` 要求当日至少一个非空值 |
| E4 | 指标口径与 #45 一致 | 与 `reference_forecast.metrics()` 5 组用例逐键比对，`diffs=[]` |
| E5 | 泄漏防护 | 对目标日取数抛 `LeakageError`；注入目标日与未来日数据后四组基线结果不变 |
| E6 | 滚动切点可复现 | 28 个目标日与配置一致；两次运行 `table_digest` 相同 |
| E7 | 汇总口径 | pooled MAE=4.666667 / n=36；macro MAE=6.0；两者不等（口径确实不同） |
| E8 | MAPE 近零剔除 | ε=0.747 与配置算得一致；剔除 15/20=0.75 > 0.10 → `mape_applicable=false` |
| E9 | 日历联动 | 2026-10-01 判为法定节假日、2026-09-20 判为调休上班日；分层齐备含四类；默认 28 期不跳过，开关打开后 16 期且只含工作日与调休上班日 |
| E10 | 配置化 | `n2_window_days` 7→3 时 N2 MAE 由 0.403056 变 0.40156；`eval_end` 改后 14 期；同配置重复运行指纹相同 |

复现方式：

```bash
cd docs/spec/forecast-backtest
python reference_backtest.py --generate-samples   # 重新生成样例（确定性，seed=20261106）
python reference_backtest.py --run                # 跑回测，产出 out/ 下三份产物
python reference_backtest.py --verify             # 跑 E1–E10 自检，写出 verification.txt
```

## 10. 验收对照（#31 验收 5 条）

| # | 验收项 | 状态 | 证据 |
|---|---|---|---|
| ① | 基线与回测框架跑在**真实入库数据**上并产出误差表 | **部分达成** | 误差表已产出（`out/backtest_error_table.csv`，12 行 ALL 层 + 分层）；但数据为**合成样例**（`data_label=SAMPLE-SYNTHETIC`），真实入库数据待地图 #22 决策 Q02（客户样本）到位后用同命令重跑并改标签。**本条未闭环** |
| ② | 滚动切分点与评估期长度写在配置里可复现 | 达成 | `backtest.config.json` 的 `eval_start/eval_end/step_days/horizon_hours`；E6 断言两次运行 `table_digest` 相同（`37938619cc84741f…`）；E10 改配置即改结果 |
| ③ | 缺测点不计入误差统计 | 达成 | L6；E3 断言；误差表 `n_points` < `n_expected`（N1 1996 / N2 2006 / 期望 2016）与 `truth_coverage`、`scored_coverage` 两列 |
| ④ | 指标口径在基线与后续模型之间完全一致 | 达成 | L7/L8：与 #45 `reference_forecast.metrics()` 逐键交叉校验（E4，`diffs=[]`）；`metrics()` 为唯一实现，#48 接入模型不得另写指标函数 |
| ⑤ | 回测全程无未来数据泄漏（可用特征可得性检查验证） | 达成 | L2/L11：`available_at <= origin` 反推可得日；越界抛 `LeakageError`；E5 双向验证（取目标日报错 + 注入未来数据结果不变） |

## 11. 下游接口

- **#37 回测报告与达标判定（A06 滚动样本外）**：直接消费 `out/backtest_error_table.csv`（字段见 §7）与 `out/backtest_summary.json`。达标门槛以 `N3`（主基线）的同口径值为参照，按分层给门槛；`data_label=SAMPLE-SYNTHETIC` 的结果不得用于自动判定（L13）。报告须标 origin 与汇总口径（pooled/macro），并附「时点自证」章节（可得日反推、shift 记录、切分示意）。
- **#48 可切换模型注册表与机器学习训练/推理流水线**：模型必须接入同一 `run_backtest()` 切分与同一 `metrics()`；预处理只在训练窗 fit；模型与基线同窗同起点、逐时点对齐；新模型结果进同一张误差表，与 N1/N2/N3/N3a 并列。
- **#51 原型：回测报告形态**：展示 `origin_type`、`stratum`、`n_points`、`truth_coverage`、`scored_coverage`、`mape_applicable`、`interval_source`；覆盖率与误差指标并列一等指标；「不适用」显式显示而非留空。
- **#37/#48 的共同前置**：真实入库数据到位后重跑本框架；在此之前所有数值带合成数据标签。

## 12. 尚未定（fog）

1. **真实入库数据**：客户 12 个月以上历史负荷的落地路径与字段映射（地图 #22 决策 Q26、Q02）；到位前误差表只能标 `SAMPLE-SYNTHETIC`。
2. **真实交易日历**：逐日交易日标记匿名不可得（#43）；当前只有「法定节假日/调休上班日/普通工作日/普通休息日」四类。真实交易日历到位后替换 `samples/business_calendar_2026.csv`，L10 逻辑不动。
3. **达标门槛**：由 #37 在基线确认后写死，本规格不预填（地图 #22 决策 Q28）。
4. **分层的最小样本量**：单层点数太少时是否只报不判（例如调休上班日本期仅 1 期 72 点），由 #37 定。
5. **模型接入的预处理边界**：训练窗 fit 的具体对象（插补器、缩放器）由 #48 定义，本规格只约束「不得跨期共享」。
6. **结算日维度的联动**：本票只做日类型分层；结算日（如电费结算日）对负荷的影响尚未建模，列入 #37 后续观察项。
7. **区间预测的覆盖目标**：本期只报 N2 窗口经验区间的 PICP，未设定目标覆盖率；模型区间（分位数）的口径与校准要求由 #48/#37 定义。

## 13. 变更记录

- v1.0（2026-11-06）：首版。落定三起点与可得性反推（P_CURVE=D-3、P_BID/P_REVIEW=D-2）、rolling origin 切分配置化、朴素基线族四变体（N1/N2/N3/N3a）、N3 锚点写死为目标日（数据裁决：MAE 0.367747 vs N3a 0.500925）、缺测不插值不补零不计分、指标口径与 #45 逐键交叉校验、pooled 为主 macro 为辅、日历只作分层、配置即契约、数据标签制度；E1–E10 自检全通过。
