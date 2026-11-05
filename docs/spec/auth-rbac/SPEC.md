# 认证与六角色权限矩阵规格 v1.0

- 票：#29 规格：认证与六角色权限矩阵（父地图 #22）
- 日期：2026-11-05
- 状态：v1.0，可执行（矩阵数据见 `rbac.matrix.json`，参考实现见 `reference_rbac.py`，自检见 `verification.txt`）

## 1. 依据与输入

| 来源 | 取用内容 |
| --- | --- |
| 方案文件 `implementation.xlsx` 工作表 06-角色权限 | 六角色 × 九维度原始矩阵（唯一口径来源，本规格逐格转写，未新增行/列） |
| 方案 Q29 | 六角色（交易员 / 交易主管 / 风控人员 / 结算人员 / 系统管理员 / 审计人员）× 九维度逐格授权 |
| 方案 A08 | 未经授权敏感数据不得出域；高风险泄漏 0 项；拦截日志完整 |
| 方案 A09 | 不同角色仅访问授权功能与数据；高危越权 0 项 |
| 方案 A12 | 平台不得直接操作真实账户；无写入真实交易系统能力 |
| `docs/spec/data-contract/SPEC.md` C8 | 数据集敏感级别：PUBLIC=D01/D11/D12；SENSITIVE=D13/D15/D16；HIGHLY=D02–D10/D14；高度敏感出域拒绝、模型侧仅 k≥5 聚合 |
| `docs/spec/data-source-registry/SPEC.md` R7 | 凭据只存 ENV:/VAULT:/FILE: 引用，不入库、不进权限矩阵 |
| #41 拷问（已关闭） | 只保留一条：角色差异只做配置，权限矩阵只管「可见数据域」与「可写操作」 |
| 方案 D15 | 用户与权限：账号、角色、组织、权限、状态；首期本地账户，SSO 留二阶段 |

## 2. 第一性原理

1. **授权是判断，不是界面开关**：同一套判断必须同时服务 API、数据层与 UI，界面隐藏不算隔离；越权判定必须在服务端、在取数之前发生。
2. **默认拒绝，显式放行**：矩阵里没有的（角色, 维度, 动作）一律拒绝，新增功能不会因为「忘了配」而默认放开。
3. **最小权限 + 职责分离**：提议者不审批、审批者不配置、配置者不看敏感数据、审计者不修改任何东西。
4. **敏感性是数据属性，不是角色属性**：能不能看某一行/某一字段由数据集敏感级别 + 授权范围共同决定；角色只决定「可不可能获得」。
5. **不可抵赖**：每一次允许与每一次拒绝都留同构的审计事件；拒绝不是静默的。
6. **可复算**：给定（主体、维度、动作、资源、授权记录、时刻），裁定结果唯一且可离线重放。

## 3. 公理

- **AU-A1** 主体 = 一个账号（D15），角色是账号的属性；一个账号可持有多个角色，有效权限取并集，但职责分离约束（AU7）不被并集绕过。
- **AU-A2** 权限空间 = 九维度 × 动作集；任何动作必属于某一维度，不存在维度外动作。
- **AU-A3** 可见性有且仅有六种表述：完整可见（FULL）、范围内可见（SCOPED）、脱敏可见（MASKED）、仅本人（SELF）、团队（TEAM）、不可见（NONE）。写操作不套用这六种表述，直接列动作。
- **AU-A4** 平台权限集合与「真实交易系统写权限」交集为空；该约束是编译期常量，不是配置项（A12）。
- **AU-A5** 审计日志只追加：任何角色（含系统管理员）都没有删除、改写、关闭审计的权限。
- **AU-A6** 敏感数据出域（提示词、附件、导出、外部模型）默认拒绝；出域判定独立于本矩阵，由 A08 网关执行，本矩阵只回答「平台上可见什么」。

## 4. 认证（AC1–AC8）

- **AC1 账号体系**：首期本地账户（D15：账号、角色、组织、权限、状态）；SSO/OIDC 只在二阶段接入，接入后本地账户仍保留为 break-glass 通道，且 break-glass 每次使用留审计。
- **AC2 口令**：Argon2id（内存 ≥64 MiB、迭代 ≥3、并行度 ≥1）；长度 ≥12；不强制周期更换；禁止出现在泄露口令库（离线比对）；改密后吊销全部会话。
- **AC3 多因子**：系统管理员、风控人员强制 TOTP；其余角色可选。TOTP 绑定变更需重新认证并留审计。
- **AC4 登录节流**：同一账号 15 分钟内连续 5 次失败锁定 15 分钟；同一来源 IP 15 分钟内 20 次失败限流；锁定与解锁均留审计。
- **AC5 会话**：不透明随机令牌（≥256 bit），HttpOnly + Secure + SameSite=Lax，服务端存会话表；空闲 30 分钟失效（滑动），绝对 12 小时失效；令牌轮换在登录、提权、角色变更时发生。
- **AC6 再认证**：修改系统配置、创建/延长授权、导出敏感数据、break-glass 前必须重新输入口令（5 分钟内有效）。
- **AC7 登出与吊销**：登出立即失效；管理员可吊销单个会话；账号停用即时生效（会话校验每次查状态）。
- **AC8 认证审计**：登录成功/失败、锁定、登出、超时、令牌轮换、MFA 变更均产生审计事件。

## 5. 授权模型（AU1–AU10）

- **AU1 主体**：`subject = {account_id, roles[], org_id, status}`；停用账号任何裁定均为 DENY（reason=ACCOUNT_DISABLED）。
- **AU2 维度与动作**：

| 维度 ID | 名称 | 动作集 | 说明 |
| --- | --- | --- | --- |
| RULE_LEARNING | 规则学习 | VIEW | 规则库、课程、题库、考试 |
| DATA_IMPORT | 数据导入 | VIEW / IMPORT / CONFIGURE | 查看批次 / 执行导入 / 配置导入模板与源 |
| SENSITIVE_DATA | 敏感数据（授权范围） | VIEW | 受 C8 敏感级别约束的读取 |
| SIM_TRADING | 模拟交易 | VIEW / SUBMIT | 查看回放 / 提交模拟申报 |
| STRATEGY_ADVICE | 策略建议 | VIEW / GENERATE | 查看 / 生成策略与建议 |
| APPROVAL | 审批建议 | VIEW / APPROVE / VETO | 查看 / 放行 / 否决 |
| SETTLEMENT_REVIEW | 结算复核 | VIEW / REVIEW | 查看 / 复核确认（不可改历史结算） |
| SYSTEM_CONFIG | 系统配置 | CONFIGURE | domain ∈ {ALL, RISK, SETTLEMENT} |
| AUDIT | 审计 | VIEW | scope ∈ {SELF, TEAM, RELATED, TECH, ALL} |

- **AU3 矩阵格**：每个（角色, 维度）= `{actions[], scope, mask, domain?}`；54 格必须全部显式给出，缺一格视为配置非法（加载即失败）。
- **AU4 裁定顺序**：账号状态 → 角色并集 → 维度/动作存在性 → 权限项 → 范围解析 → 掩码计算；任一步失败即 DENY，并带 reason_code。
- **AU5 范围**：`ALL`（全量）/ `GRANT`（按有效授权记录）/ `SETTLEMENT`（结算周期内的 D09/D10 与相关合同）/ `SELF`（actor 本人）/ `TEAM`（actor 直属团队）/ `RELATED`（与 actor 参与过的结算批次相关）/ `TECH`（技术日志，不含业务字段）。
- **AU6 掩码**：`NONE` / `MASKED`；实际掩码还取决于资源敏感级别（AU12）。
- **AU7 职责分离**：
  - APPROVE 与 GENERATE 不能由同一账号对同一建议执行（含多角色并集）；
  - 交易员永远没有 APPROVE；
  - 风控的 VETO 与主管的 APPROVE 互相独立，任一方不能代替另一方；
  - 系统管理员没有 SENSITIVE_DATA 的默认权限，也不能给自己授权（授权只能由「授权管理员」角色执行，见 AU15）。
- **AU8 只读约束**：SETTLEMENT_REVIEW:REVIEW 只写复核意见与状态，不改 D09/D10 原始数值；AUDIT:VIEW 只读；任何角色无 D16 删除权。
- **AU9 多角色并集**：动作取并集，范围取最宽的有效范围；但 AU7 的禁止项优先于并集。
- **AU10 判定可复算**：`evaluate(subject, dimension, action, resource, grants, now)` 为纯函数；相同输入必得相同输出；实现给出确定性 reason_code。

## 6. 矩阵（54 格，逐格转写自 06-角色权限）

记号：动作 + 范围 + 掩码；「—」= 空动作集（不可见/不可操作）。

| 角色 | 规则学习 | 数据导入 | 敏感数据 | 模拟交易 | 策略建议 | 审批建议 | 结算复核 | 系统配置 | 审计 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 交易员 TRADER | VIEW/ALL | — | VIEW/GRANT | VIEW+SUBMIT/ALL | VIEW+GENERATE/ALL | — | VIEW/ALL | — | VIEW/SELF |
| 交易主管 SUPERVISOR | VIEW/ALL | VIEW/ALL | VIEW/GRANT | VIEW+SUBMIT/ALL | VIEW+GENERATE/ALL | VIEW+APPROVE/ALL | VIEW/ALL | — | VIEW/TEAM |
| 风控人员 RISK | VIEW/ALL | VIEW/ALL | VIEW/GRANT | VIEW/ALL | VIEW/ALL | VIEW+VETO/ALL | VIEW/ALL | CONFIGURE/RISK | VIEW/ALL |
| 结算人员 SETTLEMENT | VIEW/ALL | VIEW+IMPORT/ALL | VIEW/SETTLEMENT | — | VIEW/ALL | — | VIEW+REVIEW/ALL | CONFIGURE/SETTLEMENT | VIEW/RELATED |
| 系统管理员 ADMIN | — | VIEW+CONFIGURE/ALL | — | — | — | — | — | CONFIGURE/ALL | VIEW/TECH |
| 审计人员 AUDITOR | VIEW/ALL | — | VIEW/GRANT + MASKED | VIEW/ALL | VIEW/ALL | VIEW/ALL | VIEW/ALL | — | VIEW/ALL |

逐格对照（原始单元格 → 规范化）：

| 角色 | 原始单元格 | 规范化 |
| --- | --- | --- |
| 交易员 | 是（规则学习） | VIEW/ALL |
| 交易员 | 否（数据导入） | — |
| 交易员 | 授权范围（敏感） | VIEW/GRANT，掩码按敏感级别 |
| 交易员 | 是（模拟交易） | VIEW+SUBMIT/ALL |
| 交易员 | 是（策略建议） | VIEW+GENERATE/ALL |
| 交易员 | 否（审批） | — |
| 交易员 | 查看（结算复核） | VIEW/ALL |
| 交易员 | 否（系统配置） | — |
| 交易员 | 本人（审计） | VIEW/SELF |
| 交易主管 | 是 / 查看 / 授权范围 / 是 / 是 / 是 / 查看 / 否 / 团队 | VIEW / VIEW / VIEW-GRANT / VIEW+SUBMIT / VIEW+GENERATE / VIEW+APPROVE / VIEW / — / VIEW-TEAM |
| 风控人员 | 是 / 查看 / 授权范围 / 查看 / 查看 / 否决-放行 / 查看 / 风控参数 / 全部 | VIEW / VIEW / VIEW-GRANT / VIEW / VIEW / VIEW+VETO / VIEW / CONFIGURE-RISK / VIEW-ALL |
| 结算人员 | 是 / 导入 / 结算范围 / 否 / 查看 / 否 / 是 / 结算参数 / 相关 | VIEW / VIEW+IMPORT / VIEW-SETTLEMENT / — / VIEW / — / VIEW+REVIEW / CONFIGURE-SETTLEMENT / VIEW-RELATED |
| 系统管理员 | 否 / 配置 / 默认不可见 / 否 / 否 / 否 / 否 / 是 / 技术日志 | — / VIEW+CONFIGURE / — / — / — / — / — / CONFIGURE-ALL / VIEW-TECH |
| 审计人员 | 查看 / 否 / 脱敏-授权 / 查看 / 查看 / 查看 / 查看 / 否 / 全部 | VIEW / — / VIEW-GRANT+MASKED / VIEW / VIEW / VIEW / VIEW / — / VIEW-ALL |

矩阵中无「待定」单元格；每一格都有动作集（可为空）与范围/掩码。

## 7. 敏感数据与脱敏（AU11–AU14）

- **AU11 级别继承**：行/字段的敏感级别来自数据契约 C8；同一数据集内字段可更高，不得更低。
- **AU12 掩码裁定**：
  - PUBLIC（D01/D11/D12）：不掩码；
  - SENSITIVE（D13/D15/D16）：矩阵 `mask=MASKED` 时掩码，否则可见；
  - HIGHLY（D02–D10/D14）：默认掩码；只有 `GRANT` 范围内、且授权记录显式 `expose=HIGHLY`、且经双人批准时，才可显式数值；审计人员的授权范围也要走同一条件。
- **AU13 掩码形式**：确定性、不可逆：`MASK-H<前4位sha256>`（如 `MASK-H9f2a`）；数值型字段不保留量级、不保留排序；同一值在同一密钥下掩码稳定，跨密钥不可关联。
- **AU14 出域**：导出、提示词、附件、外部模型调用一律再过 A08 网关；本矩阵裁定「平台内可见」，不替代出域审批；HIGHLY 数据出域无论角色一律拒绝（模型侧仅 k≥5 聚合结果）。

## 8. 授权授予（AU15–AU17）

- **AU15 授权管理员**：唯一可创建/延长/撤销授权的角色能力是 `GRANT_ADMIN`，由系统管理员账号中的一个专门账号持有，且该账号不得同时持有 SENSITIVE_DATA:VIEW（避免自授权）。授权记录：`{grant_id, subject, dimension, scope, mask, datasets[], expose, grantor, reason, created_at, expires_at, status}`。
- **AU16 约束**：reason 必填且 ≥10 字；expires_at 必填且 ≤90 天；HIGHLY 的 expose 需两名 GRANT_ADMIN 会签；到期自动失效（裁定时刻比较，不依赖定时任务）；撤销立即生效。
- **AU17 审计**：授权创建/修改/撤销、以及每次以 GRANT 范围读取敏感数据，均写审计事件（含 grant_id）。

## 9. 审计与留痕（AU18–AU21）

- **AU18 事件结构**：`{event_id, at, actor, roles[], session_id, dimension, action, resource_type, resource_id, verdict, reason_code, scope, masked_fields[], grant_id?, request_id, prev_hash, hash}`。
- **AU19 同构**：允许与拒绝使用同一结构，拒绝时 verdict=DENY 且 reason_code 非空；禁止无 reason 的拒绝。
- **AU20 只追加**：事件表无 UPDATE/DELETE 权限；hash = sha256(prev_hash + canonical_json(event))，形成链；每日锚定一次链头哈希；断链即告警。
- **AU21 留存**：默认 180 天在线 + 3 年归档；最终期限由 Q07 确认；任何角色不能缩短留存期。

## 10. 不变量与验收对照（A09 / A08 / A12）

| 编号 | 不变量 | 验证 |
| --- | --- | --- |
| I1 | 54 格无空缺、无待定 | E1：加载即校验，缺格报错 |
| I2 | 未声明即拒绝 | E2：未知角色/维度/动作全 DENY 且带 reason |
| I3 | 无真实交易写权限 | E5：全矩阵动作 ∩ FORBIDDEN = ∅；运行期 evaluate("TRADE_SUBMIT_REAL") 全 DENY |
| I4 | 管理员默认看不到敏感数据 | E3：ADMIN × SENSITIVE_DATA × VIEW → DENY |
| I5 | 提议者不审批 | E6：同人 GENERATE+APPROVE → DENY(SEPARATION_OF_DUTY)；TRADER × APPROVE → DENY |
| I6 | 审计不可改 | E8：审计链校验通过；篡改中间事件即断链 |
| I7 | 授权可过期、不可自授 | E7：过期授权回落到角色默认；自授被拒 |
| I8 | 每次裁定都有审计事件 | E8：allow 与 deny 均返回事件；事件字段齐全 |

验收对照：A09 高危越权 0 项 ← E2/E3/E6/E7 的角色×动作探针；A08 拦截日志完整 ← AU19/AU21 与 E8；A12 无写入真实交易系统 ← E5 与 schema 枚举。

## 11. 下游接口

- 服务端中间件：`authorize(subject, dimension, action, resource)` → `Decision{allowed, scope, mask, reason_code, audit_event}`；服务层不得自行判断角色名。
- 数据层：行过滤按 scope（TEAM/RELATED/SELF/SETTLEMENT），字段过滤按 mask；两者都来自同一 Decision。
- UI：能力开关由 `GET /me/capabilities` 返回同一套裁定结果（54 格压缩为当前主体可见项），不另写一份权限逻辑。
- 导入器/定时任务：服务账号 `svc_import` 仅持有 DATA_IMPORT:IMPORT，不可交互登录（AC1 例外，无口令）。
- 审计写入：`audit.append(event)`；审计查询 `audit.query(scope, filter)` 强制叠加 AUDIT:VIEW 的 scope。
- 与 #33 的接口：字段级 `sensitivity`、`dataset` 作为本矩阵资源输入；与 #44 的接口：registry 条目 `owner` 与 `sensitivity` 不参与授权，只做展示。

## 12. 尚未指定（fog）

- 组织层级来源：TEAM / RELATED 依赖 D15 的 org_id 与「参与过的结算批次」定义，需随客户组织数据定稿（Q02）。
- 日志留存期与等保/第三方测评要求（Q07）决定 AU21 的最终天数与归档介质。
- SSO/OIDC 二阶段接入时，本地 break-glass 账号的数量与保管方式。
- TOTP 之外的因子（短信/硬件密钥）是否需要，取决于客户 IT 条件。
- GRANT_ADMIN 由谁担任（岗位而非角色）需客户确认。

## 13. 变更记录

- 2026-11-05 v1.0：首版；54 格逐格转写方案 06-角色权限；认证、会话、授权、掩码、审计、验收对照；参考实现与 E1–E8 自检。
