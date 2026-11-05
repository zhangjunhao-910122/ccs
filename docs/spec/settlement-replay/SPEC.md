# 结算复现与对账口径 v1.0

- **状态**：ACTIVE（自检 12/12 PASS；A03 仍为 PENDING_Q03）
- **票**：[规格：结算复现与对账口径](https://github.com/zhangjunhao-910122/ccs/issues/55)（wayfinder:task）
- **地图**：[湖北省售电公司交易员训战一体化平台（按方案 V1.0）](https://github.com/zhangjunhao-910122/ccs/issues/22)
- **日期**：2026-11-05
- **配套文件**：`settlement.schema.json`（机器可读口径）、`reference_settlement.py`（参考实现 + E1–E12）、`samples/settlement_sample.json`（可重放样例）、`verification.txt`
- **范围**：单一家售电公司、自然日与自然月、日清与月结复现；年度清算、发票、支付、自动申报不在本期。

## 0. 一页结论

1. 结算复现不是「再算一遍总额」，而是把**合同曲线 + 规则参数 + 计量电量**三者按科目展开成可逐行核对的账；总额相等但科目错位仍是失败。
2. 全部科目收敛为 19 个 `item_code`、9 个公式族；每个科目必须能回答「量、价、金额、规则依据、参数版本」五件事。
3. 差异按**科目 × 时段 × 量/价/参数/归属**分类，共 14 类；`UNEXPLAINED` 与 `UNMAPPED_ITEM` 必须为 0 才可能 PASS。
4. 参数缺失或生效期重叠一律 BLOCK，禁止默认值；2024 年的 4.79% 线损、偏差档位只是样例，标 `SYNTHETIC`，不得进入生产参数集。
5. 容差不是系统默认：`CONFIRMED` 档当前为空（PENDING_Q03），系统最多给 `PROVISIONAL_PASS`；只有双方书面确认代表月与容差后才可能 `PASS`。
6. 官方日清与月结是外部事实，内部账是推导账；复现只读官方数据，结果写派生表，不回写官方账单。

## 1. 依据与边界

### 1.1 规则依据

- 1656 号文：中长期以自然月为周期、按日清分按月结算；设中长期结算参考点，按差价或差量结算；未签中长期合同的电量按偏差电量结算；居民农业与代理购电偏差分别结算。
- 湖北零售细则（鄂电交易〔2025〕9 号）：零售按月结算、按合同约定电价；售电公司收益 = 零售应收 − 批发应付 − 市场运营费；偏差考核由套餐自约；电量差错追退补追溯期原则上不跨年；交易机构出结算依据、电网企业出凭证。
- 披露时点：D+1 计量电量（小时），D+2 结算电量与电价（小时实时节点价 + 综合价），异议 D+2 24:00 前；月度结算结果每月 4 日发布；正式月结单每月前 10 个工作日。
- 现货：实时每 15 分钟出清；小时节点价 = 该小时 4 个 15 分钟节点价的算术平均（与 #26 T5 一致）。
- 绿电：价格 = 电能量 + 环境价值；环境价值不纳入峰谷分时电价与力调电费（1656 第三十三条）。
- 上游接口：#26 时间栅格、#33 数据契约（D05/D06/D09/D10）、#44 数据源与导入、#25 存储层、#54 规则知识库。
- 下游：头寸（#56）、历史回放（#57）、零售套餐（#59）、验收门禁（#61）、结算引擎实现（#64）。

### 1.2 明确不做

- 年度清算（次年 2 月底前）、发票与税务、资金支付、自动申报与真实交易写入。
- 用总额差反推科目差；用官方结算日反推业务归属日。
- 在 Q02/Q03 未确认前猜官方科目编码、用户维度、容差或 2026 年偏差档位。

## 2. 第一性原理

1. **结算是可重算函数**：给定合同、参数、计量与规则版本，任意一次复现必须得到同一结果；不可重算的部分不属于结算。
2. **广延量守恒、强度量加权、类别量不聚合**：电量与金额按科目求和；价格按电量加权或按规则取数；科目、方向、质量标识不跨类合并（承接 #26）。
3. **差异必须可归因**：任何金额差都要落到科目、时段、量、价、参数版本或归属之一；归不了因就是 `UNEXPLAINED`，必须为 0。
4. **参数先于计算**：规则参数有生效期与文号；缺失或重叠时停止，而不是取「最近一条」。
5. **官方账与内部账分离**：官方数据只读；复现产物是派生账与差异报告，不回写、不覆盖。
6. **追退补是增量**：原单不可变，调整单用 `retro_seq>0` 追加入账，`related_month` 指回原期。
7. **容差是合同事实**：容差与代表月由双方确认；系统只能执行，不能自行放宽。

## 3. 公理

- **S-A1（科目完备）**：任一结算金额必须落在 19 个科目之一；科目间不得重复计入合计。
- **S-A2（守恒）**：合同分解前后电量相等；科目金额之和等于其分项之和；毛利恒等式残差为 0。
- **S-A3（可追溯）**：每行结算至少带 `rule_ref` 或参数证据（文号、条款、版本、生效期）。
- **S-A4（幂等）**：同一输入、同一参数集、同一引擎版本重复复现，`input_digest` 与 `output_digest` 相同。
- **S-A5（不猜）**：官方科目未映射、参数缺失或重叠、合同电量为 0 时 BLOCK 或记差异，不设默认值。
- **S-A6（只读）**：复现不修改 D09/D10 与合同数据；结果只写 `settlement_run`、`settlement_line`、`settlement_diff`。

## 4. 规范 S1–S20

- **S1 主体口径**：`subject_scope=COMPANY`；官方日清与月结按单一售电公司复现；用户与合同层是内部推导层，不假设官方账单提供用户维度（Q02 待样本）。
- **S2 科目表**：19 个科目、9 个公式族（DIRECT、SPOT_RESIDUAL、SPOT_RT、CONTRACT_DIFF、TIERED、ALLOCATION、GREEN_SPLIT、RETRO、MARGIN）。
- **S3 互斥组**：`SPOT_BASIS` = {W_SPOT_REF, W_SPOT_DA + W_SPOT_RT, W_CONTRACT_DIFF}，一次复现只能选一组；混选即 BLOCK。
- **S4 合同分解**：优先 `CURVE`（显式 24×N），其次 `EQUAL_MONTH_DAY`（月总量按日最大余数，再按 24 时段），最后 `EQUAL_SLOT`；量子 0.001 MWh，分解前后必须相等，负电量 BLOCK。
- **S5 日清**：逐日逐时段输出 W_CONTRACT 与 W_SPOT_REF；W_CONTRACT = Σ q_contract × p_contract；W_SPOT_REF = (q_actual − q_contract) × p_ref；合同外电量可为负（回购），符号保留。
- **S6 月结**：日清按科目汇总，再叠加月度科目（线损、输配、系统、基金、分摊、偏差、辅助、市场费）；月度科目不得在日清重复出现。
- **S7 线损**：W_LOSS = 合同金额 × loss_rate ÷ (1 − loss_rate)；线损率必须来自当期有效参数；2024 年 4.79% 仅作样例。
- **S8 偏差考核**：r = (q_actual − q_contract) / q_contract；按 |r| 分段，分段电量 = 合同电量 × 段宽，费率单位元每 MWh；最后一段吸收全部剩余；q_contract = 0 且 q_actual ≠ 0 时记 `ZERO_CONTRACT_DENOMINATOR`；不默认正负对称。
- **S9 绿电拆分**：R_GREEN_ENERGY = q_green × p_green_energy；R_GREEN_ENV = q_green × p_green_env；两项都是 R_ENERGY 的备查分解项，`included_in_totals=false`，不重复计入收入。
- **S10 批零毛利**：T_GROSS_MARGIN = T_RETAIL_REVENUE − T_WHOLESALE_COST + T_ADJUSTMENT；分用户与分科目毛利之和必须等于总额，残差 0.00 元。
- **S11 成交链接**：D05 合同与 D08 成交只能用 `position_link(contract_id, version, trade_id)` 显式链接；禁止按日期、电量、对手方隐式匹配；未链接成交记 `UNLINKED_TRADE`，不静默净额。
- **S12 官方科目映射**：`official_subject_map` 当前 PENDING_Q02；映射缺失的官方行走 `UNMAPPED_ITEM` 并 FAIL，不得按标题相似度猜测。
- **S13 精度与舍入**：金额 0.01 元、电量 0.001 MWh、价格 0.0001 元每 MWh；Decimal 28 位、ROUND_HALF_UP；行级舍入，总额由行相加，禁止先总后分。
- **S14 参数注册表**：字段为 (code, value, unit, rule_doc_id, clause_no, doc_version, effective_from, effective_to, source_url)；有效判据 `valid_from ≤ as_of ≤ valid_to`；缺失记 `MISSING_PARAMETER`、重叠记 `PARAMETER_OVERLAP`，一律 BLOCK；`evidence=SYNTHETIC` 不得进生产。
- **S15 差异分类**：14 类（见第 7 节）；每行差异必须带科目、时段（月结为 null）、内部与官方的量价额、差额、类别、证据、处理建议。
- **S16 容差**：EXACT（0，仅自检）、PROVISIONAL（0.001 MWh / 0.01 元 / 0.0001）、CONFIRMED（当前空，PENDING_Q03）；只有 CONFIRMED 加代表月确认、`unexplained=0`、`unmapped=0`、覆盖率 ≥99% 才 PASS。
- **S17 质量闸**：只使用 OK 与 ESTIMATED 行；官方日清覆盖自然日比例低于 99% 判 `DATA_INCOMPLETE`，不进入容差判定；官方行质量非可用记 `OFFICIAL_QUALITY`。
- **S18 可复现**：报告必须带 `input_digest`、`output_digest`、`run_id`、`engine_version`、`schema_version`、`parameter_set_digest`；两次运行 digest 必须相同。
- **S19 运行产物**：`run_status ∈ {PASS, PROVISIONAL_PASS, FAIL, BLOCKED, DATA_INCOMPLETE}`；报告含差异项、原因、影响、处理建议四段；不回写官方数据。
- **S20 时间口径**：自然日 24 个小时段、自然月；`energy_date` 按 interval_start 归属；`settle_date` 不得反推 `energy_date`（#26 T9 与 T10）。

## 5. 科目表与公式

| 科目 | 名称 | 方向 | 频率 | 公式族 | 互斥组 | 计入合计 |
|---|---|---|---|---|---|---|
| W_CONTRACT | 中长期合同电能量电费 | BUY | DAILY | DIRECT |  | 是 |
| W_SPOT_REF | 现货参考价电费（合同外电量） | BUY | DAILY | SPOT_RESIDUAL | SPOT_BASIS | 是 |
| W_SPOT_DA | 日前市场电费 | BUY | DAILY | DIRECT | SPOT_BASIS | 是 |
| W_SPOT_RT | 实时偏差电费 | BUY | DAILY | SPOT_RT | SPOT_BASIS | 是 |
| W_CONTRACT_DIFF | 中长期差价电费 | BUY | DAILY | CONTRACT_DIFF | SPOT_BASIS | 是 |
| W_LOSS | 上网环节线损费用 | BUY | MONTHLY | DIRECT |  | 是 |
| W_TRANSMISSION | 输配电费 | BUY | MONTHLY | DIRECT |  | 是 |
| W_SYSTEM | 系统运行费 | BUY | MONTHLY | DIRECT |  | 是 |
| W_GOV_FUND | 政府性基金及附加 | BUY | MONTHLY | DIRECT |  | 是 |
| W_SHARE | 保障居民农业用电新增损益分摊与分享 | BUY | MONTHLY | ALLOCATION |  | 是 |
| W_DEVIATION | 偏差考核费 | BUY | MONTHLY | TIERED |  | 是 |
| W_ANCILLARY | 辅助服务费 | BUY | MONTHLY | DIRECT |  | 是 |
| W_MARKET_FEE | 市场运营费 | BUY | MONTHLY | DIRECT |  | 是 |
| R_ENERGY | 零售电能量收入 | SELL | MONTHLY | DIRECT |  | 是 |
| R_RETAIL_DEVIATION | 零售偏差考核收入 | SELL | MONTHLY | TIERED |  | 是 |
| R_GREEN_ENERGY | 绿电电能量收入（分解项） | SELL | MONTHLY | GREEN_SPLIT |  | 否（备查） |
| R_GREEN_ENV | 绿电环境价值收入（分解项） | SELL | MONTHLY | GREEN_SPLIT |  | 否（备查） |
| A_RETRO | 追退补 | SIGNED | MONTHLY | RETRO |  | 是 |
| A_OTHER_ALLOC | 其他分摊与返还 | SIGNED | MONTHLY | ALLOCATION |  | 是 |

公式族与参数口径的机器可读定义见 `settlement.schema.json → formula_families` 与 `parameter_registry`，本表只给口径边界与计入规则。

## 6. 合同曲线分解

- 输入 D05：`total_energy_mwh`、`price_type`、`price`、`formula_text`、`curve_json`、`version`、`status`。
- 三种模式都必须显式标注，不允许「看起来像」就推定：
  1. `CURVE`：逐日逐时段显式曲线；校验 24 段、非负、合计等于合同总量。
  2. `EQUAL_MONTH_DAY`：月总量按自然日等分（最大余数，量子 0.001 MWh），再按 24 时段等分；质量标 `ESTIMATED`。
  3. `EQUAL_SLOT`：总量按日、再按 24 时段等分；仅在合同没有更细曲线时使用。
- 守恒：分解行合计必须严格等于合同总量，否则 `CURVE_NOT_CONSERVED`；负电量记 `NEGATIVE_CURVE_ENERGY`。
- 样例 C1：5,376 MWh / 400 元每 MWh，8 MWh × 24 时段每日 × 28 天；日合同电费 76,800 元。
- 样例 C2：1,200 MWh 均分但不参与结算（`settle=false`），用于验证 `EQUAL_MONTH_DAY` 守恒。

## 7. 对账与差异分类

对账键：

- 日清：`(energy_date, slot_no, item_code)`；官方 `subject_code` 先经 `official_subject_map` 映射。
- 月结：`(settle_month, item_code, retro_seq)`；`retro_seq=0` 原单，大于 0 调整单。
- 同一键出现多行即 BLOCK，不取「最后一行」。

14 类差异：

| 类别 | 含义 | 是否可解释 |
|---|---|---|
| MISSING_OFFICIAL | 内部有、官方无该科目或时段行 | 数据缺口，需补官方数据 |
| MISSING_INTERNAL | 官方有、内部无该科目或时段行 | 映射或漏算，需修内部 |
| QUANTITY_DIFF | 电量与计量差异 | 是 |
| PRICE_DIFF | 合同价、参考价或单价差异 | 是 |
| PARAMETER_VERSION | 参数版本或生效期错配 | 是（需换正确版本） |
| FORMULA_DIFF | 公式族或结算方式选择错配 | 是（需改公式） |
| ROUNDING | 舍入差异 | 是 |
| TIMING | 归属日、时段或结算日错配 | 是 |
| ALLOCATION | 分摊比例或分摊池差异 | 是 |
| UNMAPPED_ITEM | 官方科目未映射 | 否，必须为 0 |
| OFFICIAL_ADJUSTMENT | 官方追退补或更正导致 | 是 |
| UNLINKED_TRADE | 成交未与合同显式链接 | 是（需补链接） |
| OFFICIAL_QUALITY | 官方行质量非 OK 或 ESTIMATED | 是（需换数） |
| UNEXPLAINED | 无法归类 | 否，必须为 0 |

判定顺序：量差 → 价差 → 金额自洽性 → 舍入；`UNMAPPED_ITEM` 与 `UNEXPLAINED` 任一非 0 即 FAIL。

## 8. 容差与 A03

| 档位 | 电量容差 | 金额容差 | 比例容差 | 用途 |
|---|---|---|---|---|
| EXACT | 0 | 0.00 | 0 | 自检与公式单测 |
| PROVISIONAL | 0.001 MWh | 0.01 元 | 0.0001 | 内部预检，不得宣布 A03 |
| CONFIRMED | 待 Q03 | 待 Q03 | 待 Q03 | 双方书面确认后写入 |

- `CONFIRMED` 必须同时有双方确认人、金额与电量数值、代表月清单；缺一即 `TOLERANCE_NOT_CONFIRMED`。
- `PASS` 条件：`profile=CONFIRMED`、`representative_month_confirmed=true`、`unexplained_count=0`、`unmapped_count=0`、覆盖率 ≥99%，且所有差异金额不超过容差。
- 系统不得为了让结果通过而放宽容差、剔除科目或改映射；只能报差异并给出处理建议。

## 9. 质量闸与数据完整性

- 只使用 OK 与 ESTIMATED 行；PARTIAL、SUSPECT、MISSING 不进入计算，记 `OFFICIAL_QUALITY`。
- 官方日清覆盖的自然日比例 = 有官方行的自然日 ÷ 期间自然日；低于 99% 判 `DATA_INCOMPLETE`。
- 任一关键参数缺失或重叠，整个 run 判 `BLOCKED`，不产出半套账。
- 报告 `summary` 必含：内部合计、官方合计、差额、差异数、可解释数、不可解释数、是否在容差内、最大绝对差、覆盖天数与应覆盖天数。

## 10. 参考实现与样例

- `reference_settlement.py`：`build_report(period, contracts, users, actual_profile, ref_profile, params, official_d09, official_d10, subject_map, profile_name, representative_confirmed, complete_month, as_of)`。
- 样例为 2026-02 全月：合同 5,376 MWh、实际 6,720 MWh、参考价 350 元每 MWh、零售价 500 元每 MWh、绿电 672 MWh（450 + 50）。
- 关键结果：
  - 合同电费 2,150,400.00；合同外现货 470,400.00；线损 108,186.28；输配 672,000.00；系统 201,600.00；基金 134,400.00；分摊 1,200.00；偏差 34,944.00；辅助 13,440.00；市场费 6,720.00。
  - 批发成本 3,793,290.28；零售收入 3,360,000.00；批零毛利 −433,290.28。
  - 绿电备查 302,400.00（电能量）与 33,600.00（环境价值），已含在零售收入内，不重复计入。
  - 内部与官方全科目合计均为 7,489,290.28，差异 0 行、0.00 元。
- 偏差样例：合同 5,376、实际 6,720，偏差率 25%；按 10%、15%、20% 三档（10、20、100 元每 MWh）计得 34,944.00 元。档位与费率是 2024 年样例，2026 年适用档位未确认。

## 11. 自检 E1–E12

| 编号 | 检查 | 结果 |
|---|---|---|
| E1 | schema 结构、19 科目唯一、公式族与互斥组与差异类完整 | PASS |
| E2 | CURVE 5,376、EQUAL_MONTH_DAY 1,200、最大余数守恒 | PASS |
| E3 | 2026-02-01 日清：合同 192 MWh 与 76,800 元，合同外 48 MWh 与 16,800 元 | PASS |
| E4 | 13 个月度科目与批发、零售、毛利 | PASS |
| E5 | 官方样例 0 差异、双方合计 7,489,290.28 | PASS |
| E6 | 空 CONFIRMED 拒绝；零容差 PASS；超差 FAIL | PASS |
| E7 | 数量、价格、公式、一致、未映射、缺失六类判定 | PASS |
| E8 | 偏差 25% 得 ±6,500；10% 免考核；零合同 BLOCK | PASS |
| E9 | 线损公式、绿电拆分、毛利恒等式 | PASS |
| E10 | 两次运行 input 与 output digest 相同 | PASS |
| E11 | 缺日 27/28 判 DATA_INCOMPLETE；未映射判 FAIL | PASS |
| E12 | 参数唯一、as_of 边界、缺失、重叠、可选缺失 | PASS |

运行方式：

~~~
cd docs/spec/settlement-replay
python -X utf8 reference_settlement.py --verify
~~~

## 12. 下游接口

- **#64 结算引擎**：直接实现本 schema；`run_artifact` 是引擎与对账报告的契约，不允许自行增删科目。
- **#61 验收门禁**：A03 只读 `run_status`、`tolerance_profile`、`representative_month_confirmed`、`unexplained_count`、`unmapped_count`；这五项之外不得自造通过条件。
- **#56 头寸**：合同曲线与结算电量复用同一分解与时间口径，不另造「结算电量」定义。
- **#57 历史回放**：替换参数集与场景输入即可复用；回放结果必须标 `SIMULATE`，不得覆盖真实结算。
- **#59 零售套餐**：毛利分解与 R_ 前缀科目对齐；零售月度口径不与 15 分钟现货混算。

## 13. 未决与 fog

1. **Q03 容差与代表月**（阻塞 A03）：双方尚未给出容差数值与代表月份；当前只能 `PROVISIONAL_PASS`。
2. **Q02 官方科目编码与用户维度**：D09 与 D10 无用户与交易主体字段；官方是否分用户、科目编码表均待客户样本。
3. **2026 年偏差考核档位**：现行档位、考核基准（总电量或分时段）、超额回收口径未确认；2024 档位仅样例。
4. **线损边界**：按零售用户计量边界还是关口；若按关口需 `loss_boundary=GATEWAY` 并做线损毛增，Q02 前不切换。
5. **差价与差量结算选择**：1656 允许参考点价按日前或实时、差价或差量；具体组合待交易中心结算细则。
6. **绿电环境价值**：环境价值不参与峰谷与力调已定；但环境价值的结算主体、发票与分摊方式未定。
7. **辅助服务费科目拆分**：调频、备用、无功等是否单列，与现货电费是否重叠，待规则包。
8. **跨年追退补**：细则「原则上不跨年」，例外审批与账务处理未定。
9. **D08 成交链接覆盖率**：老数据可能无 `trade_id`，需补链接或整批标 `UNLINKED_TRADE`。

## 14. 验收对照（#55）

| 票面验收 | 本规格回应 | 证据 |
|---|---|---|
| 每科目可执行公式加参数来源 | 19 科目表、9 公式族、参数注册表（文号、条款、版本、生效期） | schema、第 5 与 6 节、E1 与 E4 |
| 两次复现一致 | input 与 output digest 加幂等规则 | 第 4 节 S18、E10 |
| 差异定位到科目与时段并分类 | 日清与月结键、14 类差异、处理建议 | 第 7 节、E5、E7、E11 |
| 容差与代表月双方确认、系统不放宽 | CONFIRMED 空档、PASS 五条件、禁止改容差 | 第 8 节、E6 |
| A03 代表月逐科目比对 | 报告 `run_status`、`summary`、`diffs` 可直接供 #61 判定 | 第 9 节、E5 |

**A03 当前结论：PENDING_Q03，不能宣布通过。**

## 15. 变更记录

- 2026-11-05 v1.0：首版；19 科目、9 公式族、14 差异类，E1–E12 全 PASS；Q02 与 Q03 未决。
