# 认证、会话与六角色权限落地规格（auth-impl）

> 票 #50，父地图 #22。
> 上游依据：#29 `docs/spec/auth-rbac/`（认证与授权模型）、#46 `docs/spec/api-contract/`（对外 API 契约）。
> 参考实现：`reference_auth.py`（仅标准库）。验收回执：`verification.txt`。
> 机器依据：`docs/spec/auth-rbac/rbac.matrix.json`（54 格裁定的唯一来源）、`docs/spec/api-contract/openapi.json`（对外响应形状的唯一来源）、`docs/spec/api-contract/errors.json`（34 个稳定错误码的唯一来源）。

## 1. 目的与范围

把 #29 的认证与授权模型落成可运行、可复算的参考实现，并为 #46 的 `GET /api/v1/me/capabilities` 提供唯一投影。

范围内：

- 账号、凭证、会话三者的生命周期与状态机；
- 登录、登出、令牌轮换、再认证、失败锁定、限流；
- 鉴权中间件契约与字段级裁剪接线；
- 越权拒绝的统一错误形状（不泄露字段存在性）；
- 认证与授权事件的全量审计（只追加哈希链 + 每日锚定 + 留存分级）；
- `GET /api/v1/me/capabilities` 的投影实现与一致性校验。

范围外（与 #29 §11、#46 一致）：

- 服务层自行判断角色名——禁止。一切裁定走 `evaluate()`。
- 任何写入真实交易系统的通路——不存在。
- SSO（二阶段）、组织层级来源、留存期与等保定档值——见 §12 留白。

## 2. 第一性原理

身份底座只回答三个问题，不回答第四个：

1. **你是谁**：账号状态、角色集合、会话是否有效。
2. **你能做什么**：对某维度某动作的裁定，来自同一套矩阵。
3. **你能看见什么**：范围与掩码，来自同一次裁定。

「数据在哪里」属于数据域（#33），不属于本票。

由此推出三条硬约束：

- **裁定唯一**：`evaluate()` 是唯一裁定入口。UI 能力开关、服务端鉴权、审计事件三处取同一个 Decision，不允许出现第二套权限逻辑。
- **拒绝同形**：拒绝响应不得携带被拒资源的存在性、字段名、维度值。调用方无法通过响应差异做资源枚举。
- **审计同构**：allow 与 deny 是同一事件结构的两种取值，没有「成功才有日志」的例外。

## 3. 输入与依赖（复用清单）

| 复用对象 | 路径 | 本票怎么用 |
|---|---|---|
| 54 格矩阵 | `docs/spec/auth-rbac/rbac.matrix.json` | 角色 × 维度 × 动作 × 范围 × 掩码的唯一来源 |
| 裁定引擎 | `docs/spec/auth-rbac/reference_rbac.py` | `evaluate()` / `make_grant()` / `AuditLedger` 直接调用，不重写 |
| 错误码表 | `docs/spec/api-contract/errors.json` | 对外错误码只取这 34 个 |
| 响应形状 | `docs/spec/api-contract/openapi.json` | `CapabilitiesResponse` / `Meta` 的字段名与取值域 |
| 能力投影算法 | `docs/spec/api-contract/reference_api.py` | `compress_capabilities()` 同算法实现，自检逐字节比对防漂移 |

## 4. 数据模型

三张表。本票给出字段与状态机，不落 DDL；DDL 权威唯一 = `storage.schema.json`（见 `docs/spec/engineering/SPEC.md` §3.3 S10）。

### 4.1 accounts

| 字段 | 类型 | 说明 |
|---|---|---|
| `account_id` | str | 主键 |
| `roles` | list[str] | 取值域 = 矩阵 `roles` 的六个角色 |
| `password_hash` | str | 见 §4.4 KDF 编码 |
| `totp_secret` | str? | ADMIN / RISK 必须非空（AC3） |
| `status` | enum | ACTIVE / DISABLED / LOCKED / PENDING_RESET |
| `team` | str | TEAM / RELATED 范围判定的输入（§12 F1） |
| `org_id` | str? | 组织层级来源未定（§12 F1） |
| `service_account` | bool | 服务账号不可交互登录（#29 §11 `svc_import`） |
| `password_changed_at` | ts | 改密后吊销该账号全部会话（AC2） |
| `locked_until` | ts? | 账号锁定截止时间（AC4） |

### 4.2 sessions

| 字段 | 说明 |
|---|---|
| `session_id` | `S-000001` 递增 |
| `account_id` | 归属账号 |
| `token_hash` | 只存令牌的 SHA-256，不存明文（AC5） |
| `created_at` / `last_seen_at` | 绝对超时起点 / 滑动超时起点 |
| `rotated_from` | 上一枚令牌的 session_id（轮换链） |
| `reauth_at` | 最近一次再认证通过时间（5 分钟有效，AC6） |
| `mfa_verified_at` | 本会话完成 TOTP 的时间 |
| `revoked_at` / `revoke_reason` | 登出 / 改密 / 轮换 / 账号停用 |

### 4.3 audit_events

沿用 #29 AU18 的 18 字段结构（其中 `grant_id` 可选），另加 AU20 的 `day_anchor`（同一自然日同值），只追加。认证事件同样入链：`dimension` 固定 `AUTH`，`action` 取 `LOGIN / LOGOUT / TOKEN_ROTATE / MFA_VERIFY / REAUTH / PASSWORD_CHANGE / ACCOUNT_LOCK / ACCOUNT_DISABLED`。

### 4.4 口令 KDF 编码

`scrypt$N$R$P$salt_b64$hash_b64`，默认 `N=65536, R=8, P=1, dklen=32`（内存 = 128·N·R = 64 MiB，串行度 P=1，与 AC2 的「内存 ≥ 64 MiB、并行度 ≥ 1」一致）。若运行环境无 `hashlib.scrypt`，回退 `pbkdf2-sha256$600000$salt_b64$hash_b64`，并登记为偏差（§13）。

口令长度 ≥ 12（AC2）。改密必须与旧口令不同。

## 5. 认证流程

### 5.1 登录

1. **IP 限流**：同一 IP 15 分钟内超过 20 次 → `RATE_LIMITED`，不进入口令校验（AC4）。
2. **查账号**：不存在 → `UNAUTHENTICATED`，与「口令错误」同一对外形状。
3. **账号状态**：非 ACTIVE → `UNAUTHENTICATED`（reason `ACCOUNT_DISABLED`）。
4. **锁定**：同一账号 15 分钟内失败 ≥ 5 次 → 锁定 15 分钟，期间返回 `RATE_LIMITED`（AC4）。
5. **口令校验**：失败则计数 +1 并写审计 deny；成功则清零计数。
6. **TOTP**：角色含 ADMIN 或 RISK 时强制（AC3）；缺失或错误 → `UNAUTHENTICATED`。
7. **成功**：签发 256 bit 随机不透明令牌（AC5）；Cookie `HttpOnly; Secure; SameSite=Lax; Path=/`；绝对寿命 12 小时、空闲 30 分钟滑动失效；写审计 allow。

### 5.2 会话失效

三类同时生效，取最先到者：空闲 30 分钟（`last_seen_at` 滑动）、绝对 12 小时（`created_at` 固定）、显式撤销（登出 / 改密 / 轮换 / 账号停用）。失效后接口一律 `UNAUTHENTICATED`（AC5）。

会话状态机由本票实现（需要轮换链、再认证时间、MFA 时间、吊销原因），超时口径与 #29 `reference_rbac.SessionStore` 完全一致，并由自检交叉比对行为。

### 5.3 令牌轮换

触发点：登录成功、再认证成功、角色变更、提权（AC5）。轮换后旧令牌立即失效，吊销原因 `ROTATED`。

### 5.4 再认证

改系统配置、建授权、导出敏感数据、break-glass 四类动作前须再认证，有效期 5 分钟（AC6）。再认证 = 口令 + TOTP（该角色强制时），成功后轮换令牌。

### 5.5 改密

校验旧口令 + 新口令 ≥ 12 字符 + 与旧口令不同；改密成功即吊销该账号全部会话（AC2）。

## 6. 鉴权中间件契约

```python
authorize(subject, dimension, action, resource) -> Decision
```

Decision 字段：`allowed: bool`、`scope: str`、`masked_fields: list[str]`、`reason_code: str`、`audit_event: dict`。

调用约定：

- 服务层不得自行判断角色名，不得读矩阵格子做二次判断；
- 行过滤按 `scope`、字段过滤按 `masked_fields`，两者来自同一次裁定；
- `resource` 传入判定所需的全部上下文：`owner`、`team`、`related_accounts`、`sensitivity`、`sensitive_fields`、`dataset`、`energy_month`、`config_domain`；
- 授权发生在取数之前（#46 §25–27 第一性原理）。

## 7. 越权拒绝不泄露字段存在性

对外错误码映射（只取 #46 的 34 个稳定码）：

| reason_code | 对外 code |
|---|---|
| `NOT_GRANTED` / `UNKNOWN_DIMENSION` / `UNKNOWN_ACTION` / `NO_ROLE` / `SEPARATION_OF_DUTY` / `TRADER_CANNOT_APPROVE` / `CONFIG_DOMAIN_MISMATCH` / `SELF_SCOPE_ONLY` / `TEAM_SCOPE_ONLY` / `RELATED_SCOPE_ONLY` / `TECH_LOG_ONLY` / `UNKNOWN_SENSITIVITY` | `FORBIDDEN` |
| `NO_GRANT` | `NO_GRANT` |
| `OUT_OF_SETTLEMENT_SCOPE` | `OUT_OF_SETTLEMENT_SCOPE` |
| `FORBIDDEN_ACTION` | `WRITE_FORBIDDEN` |
| `ACCOUNT_DISABLED` / 令牌缺失或过期 | `UNAUTHENTICATED` |
| 账号锁定 / IP 限流 / 账号级限流 | `RATE_LIMITED` |

三条同形规则（实现与自检都覆盖）：

1. 「资源不存在」与「资源存在但无权限」返回**完全相同**的 error body：`code`、`message`、`details` 结构一致，`details` 不含资源 id、字段名、数量。
2. 越权响应不泄露「被拒动作是否存在于矩阵中」：未知动作与已知但无权动作同码。
3. 掩码字段以 `MASK-H<4 位十六进制>` 形式出现（#29 AU13），不回传原值、不回传原字段长度。

## 8. 审计

- **结构**：#29 AU18 的 18 字段；allow 与 deny 同构（AU19），deny 必须有 `reason_code`。
- **链**：只追加；`hash = sha256(prev_hash + canonical_json(event))`；首条 `prev_hash = GENESIS`；`AuditLedger.verify()` 校验断链（AU20）。
- **每日锚定**：同一自然日的所有事件带同一个 `day_anchor`（该日链头 = 当日首条事件的 `prev_hash`），使跨日篡改可检出，且事件字段集保持同构（AU20）。
- **留存**：在线 180 天 + 归档 3 年（AU21）。最终期限待 Q07，任何角色不能缩短。本票只实现分级函数，不实现删除。
- **认证事件与授权事件同链**，不分表。

## 9. GET /api/v1/me/capabilities

- **形状**取 `openapi.json` 的 `CapabilitiesResponse`：`data.subject{account_id, roles[], org_id?}`、`data.capabilities[]{dimension, actions[], scope, mask}`、`data.forbidden_actions[]`；`meta{request_id, api_version, contract_version, generated_at}`。
- **算法**与 #46 `compress_capabilities()` 相同：多角色并集；`scope` 取并集中最宽（按 #29 `SCOPE_RANK`）；任一格 `mask=MASKED` 则该维度 `mask=MASKED`；`actions` 去重且升序。
- **投影是矩阵唯一对外投影**：能力开关不允许出现矩阵外的能力。

## 10. 与风控合规口径的边界

本票只提供身份与判定。风控合规（#69）消费本票的 Decision 与审计事件，不在本票实现风控规则，也不把风控维度写进矩阵。边界一句话：任何需要知道「这个账号能不能碰这类数据」的模块，都必须调用 `authorize()`，不得各自实现。

## 11. 与 #46 的接线

- 错误信封：`{"error": {code, message, details, retryable}, "meta": {request_id, api_version, contract_version, generated_at}}`，与 #46 `error_response()` 一致。
- `meta.request_id` 满足 `^req_[A-Za-z0-9]{10,40}$`。
- 所有裁定走 `evaluate()`；`/me/capabilities` 是矩阵唯一对外投影（#46 §210）。

## 12. 留白（fog，不在本票解决）

| # | 留白 | 本票采用的默认口径 | 来源 |
|---|---|---|---|
| F1 | 组织层级来源 | `team` 字符串 + `related_accounts` 列表作为范围判定输入 | #29 §12 Q02 |
| F2 | 留存期与等保定档 | 180 天在线 + 3 年归档；只实现分级，不实现删除 | #29 §12 Q07、`docs/spec/engineering/SPEC.md` §10 Q07 |
| F3 | SSO 二阶段 break-glass 账号数量与保管 | 本票不实现 SSO；break-glass 作为再认证路径之一预留 | #29 §12 |
| F4 | TOTP 之外的因子 | 本票只实现 TOTP，不实现额外因子 | #29 §12 |
| F5 | GRANT_ADMIN 岗位归属 | 授权创建走 `make_grant()`，审批人来源留给组织目录 | #29 §12 |

## 13. 偏差登记（必须显式）

| 偏差 | AC2 原文 | 本票落地 | 影响与切换路径 |
|---|---|---|---|
| 口令 KDF | 「口令 Argon2id」 | 标准库 `hashlib.scrypt`（N=2¹⁶、R=8、P=1、64 MiB），无 `hashlib.scrypt` 时回退 PBKDF2-HMAC-SHA256 600000 轮 | 算法标识与参数不同；内存与并行度口径对齐 AC2。切换路径：口令记录带算法标识与参数，改密时按新算法重算，旧摘要保留一轮用于平滑过渡 |

其余 AC 的逐条对照见 §14。

## 14. 验收对照

自检编号与票面验收一一对应，结果见 `verification.txt`。

| E 编号 | 验收项 | 对应票面验收 |
|---|---|---|
| E1 | 六角色 × 九维度 54 格裁定结果与矩阵逐格一致 | 六个角色裁定结果与矩阵逐格一致 |
| E2 | 未知角色 / 未知维度 / 未声明动作一律 DENY 且有 reason_code | — |
| E3 | HIGHLY 默认掩码；GRANT + expose=HIGHLY + 双人会签才解掩；掩码前缀 `MASK-H` | 字段级裁剪 |
| E4 | 六角色 `TRADE_SUBMIT_REAL` / `TRADE_CANCEL_REAL` / `TRADE_ACCOUNT_WRITE` 全 `FORBIDDEN_ACTION`；54 格无禁止动作 | 全链路无写入真实交易系统的通道 |
| E5 | 会话空闲 30 分钟失效、绝对 12 小时失效、登出即时失效、轮换后旧令牌失效 | 会话失效后接口不再放行 |
| E6 | 5 次失败锁 15 分钟；同 IP 15 分钟 20 次限流；越权全被拒且有审计 | 越权请求全被拒且有审计记录 |
| E7 | 越权拒绝对「存在 / 不存在」字段返回同形响应 | 越权不泄露字段存在性 |
| E8 | 审计 allow / deny 同构、18 字段、只追加、篡改即断链、每日锚定、留存分级 | 审计谁在何时访问哪类数据、越权尝试全量留痕 |
| E9 | `/me/capabilities` 投影与矩阵一致，且与 #46 `compress_capabilities()` 逐字节相同 | 统一身份底座 |
| E10 | 令牌轮换、提权轮换、改密吊销全部会话 | — |
| E11 | 登录 / 登出 / 锁定 / 限流 / TOTP / 再认证状态机与对外错误码正确 | — |
| E12 | 全部自检通过 | — |

## 15. 变更记录

- 2026-11-07 v1.0 首版（票 #50）。
