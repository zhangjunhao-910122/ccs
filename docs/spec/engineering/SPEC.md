# 工程结构与运行约定 v1.0

- **状态**：v1.0 已定稿（技术栈见 §8；后续变更走 §8.4 变更规则）
- **票**：[规格：工程结构与运行约定](https://github.com/zhangjunhao-910122/ccs/issues/32)（wayfinder:task）
- **地图**：[湖北省售电公司交易员训战一体化平台（按方案 V1.0）](https://github.com/zhangjunhao-910122/ccs/issues/22)
- **日期**：2026-11-06
- **配套文件**：docs/spec/engineering/reference_structure.py（零依赖静态检查器 + 自检 E1–E10）、docs/spec/engineering/samples/apps/（合规骨架样例）、docs/spec/engineering/samples/apps_bad/（6 类代表性违规样例）、docs/spec/engineering/verification.txt（自检输出，当前 10/10 PASS）
- **范围**：仓库目录布局、层间依赖方向、确定性引擎与模型调用的代码边界、AI 控制边界的结构化落点、配置与密钥约定、日志与审计格式、技术栈登记。本票只定「结构与约定」，不写业务实现。

## 0. 一页结论

1. 仓库是单仓多包（Python 后端与引擎 + 前端工作台），代码根目录定为 apps/，按六层切成 7 个包加 1 个契约包；文档与规格仍在 docs/，不与代码混放。
2. 依赖方向写成矩阵并用零依赖静态检查器强制：上层可调下层，专业能力层不依赖模型层，数据层不依赖任何上层，安全网关不依赖业务层。
3. 确定性优先的落地方式不是口号，而是类型与目录：模型输出一律是 Suggestion 类（建议/解释/校验/预警），一切数值结果由专业能力层的确定性函数产出，两者在类型上不可互换。
4. AI 控制边界六条逐条对应到目录、命名与检查器规则，共 10 条可自动发现的结构约束（含敏感数据出域、不得写历史与审计、不得改额度阈值、核心层与智能体层不得直持 HTTP 客户端）。
5. 凭据只以 ENV: / VAULT: / FILE: 三种引用出现在仓库（沿用 #44 R7）；配置按环境分目录，不允许缺环境即回落默认值；仓库出现明文密钥或连接串判违规。
6. 日志与审计沿用 #29 AU18 事件结构与 #46 信封；模型调用审计另加 #71 字段（模型、Token、耗时、脱敏摘要、状态）。
7. 技术栈已定版并登记在 §8：后端 FastAPI、前端 React+TypeScript+Vite（纯静态产物）、Python 依赖 uv + Node 依赖 pnpm、结构与边界检查用仓内零依赖脚本、密钥扫描用仓内正则加 detect-secrets。每项写明唯一权威的锁定机制与依据；改选型必须先改 §8 再改检查器白名单（§8.4），二者必须同一次提交落地。

## 1. 依据与边界

### 1.1 依据

- 方案文件 4.1 六层架构、4.2 企业数据安全域、4.3 LinkAI 模型网关、4.4 AI 控制边界六条。
- #33 数据契约（D01–D16、字段敏感级别、公共列口径）、#46 对外 API 契约与页面数据契约（信封、错误码、operationId 注册路由）、#29 认证与授权（54 格矩阵、AU18 审计事件）、#44 数据源注册表（凭据引用、导入器双闸）、#25 存储层（一对象一表、as-of、导出闸门）、#45 负荷预测（防穿越硬断言）。
- 地图 #22 硬约束：数值确定、规则有据、数据最小化、时间隔离、人工负责、宁可缺数据不插值；未核验数值不进自动判定。
- 工程习惯沿用本仓 ISO 线：规格落 docs/spec/<域>/SPEC.md，参考实现与自检同目录，工具用 Python，结论可复现。
- §8 技术栈的一手出处（官方文档、许可证、版本策略、离线交付方式、被否方案的理由）：本票只定「怎么锁」，不写未核实版本号；一手出处待联网可查时补齐，查不到的写「未核实」，不凭印象填。

### 1.2 边界

- 本票不引入具体业务逻辑，不实现接口、不写前端页面。
- 首阶段不建自动申报下单撤单、不做公网暴露与多租户、不训练自有大模型（地图 Out of scope）。
- AI 不持交易凭证、不写真实交易系统、不改历史数据与审计日志（4.4 与 #72）。

## 2. 仓库布局

代码根目录 apps/，按层切包；文档（docs/）、审计点库（audit-point-library/）与 ISO 线工具（tools/）保持现状不动。

~~~
G:/DSH工作区
├── apps/
│   ├── contracts/        # trader_contracts  跨层契约与机器可读口径（无业务依赖）
│   ├── data/             # trader_data       数据层：导入、as-of 查询、派生、导出闸门
│   ├── gateway/          # trader_gateway    安全网关：脱敏、字段裁剪、敏感检测、用途授权、输出检查
│   ├── model/            # trader_model      模型服务层：LinkAI 适配、模型白名单、预算、审计
│   ├── core/             # trader_core       专业能力层：规则检索、预测、头寸、结算、仿真、风控、评测
│   ├── agents/           # trader_agents     智能体层：编排、任务计划、记忆、工具调用
│   ├── api/              # trader_api        访问层后端：HTTP 边界、鉴权中间件、路由注册
│   └── web/              # 前端三工作台（交易员 / 主管 / 风控审计）
├── docs/spec/<域>/SPEC.md     # 规格
├── docs/research/<主题>.md    # 研究
└── tools/                     # ISO 线既有工具，不并入 apps/
~~~

各目录职责与允许依赖见 §3.1 与 §3.2。apps/ 下每个包自带 pyproject 或 package.json 与 README，包内不允许出现跨包的深层路径导入（只允许导入顶层包名）。

## 3. 依赖方向

### 3.1 层与包映射

| 方案层 | 包 | 职责 | 可依赖 |
|---|---|---|---|
| （无） | trader_contracts | D01–D16 schema、字段敏感级别、API 信封与错误码、权限矩阵、DTO | 无（仅标准库） |
| 数据层 | trader_data | 导入、as-of 查询、日月派生、导出闸门、审计落库 | contracts |
| 安全网关 | trader_gateway | 匿名化、字段裁剪、敏感检测、用途授权、输出检查 | contracts |
| 模型服务层 | trader_model | LinkAI 客户端、白名单、主备路由、预算配额、重试熔断、模型审计 | contracts |
| 专业能力层 | trader_core | 规则检索、预测、头寸、结算、仿真、风控、评测（全部确定性数值） | contracts、data |
| 智能体层 | trader_agents | 任务计划与拆解、上下文记忆、工具调用与归集、失败重试与降级 | contracts、core、data、gateway、model |
| 访问层 | trader_api | HTTP 路由（按 operationId 注册）、鉴权中间件、审计落库 | contracts、agents、core、data、gateway |
| 访问层 | apps/web（前端） | 三工作台外壳与角色化菜单（#42） | 仅经 HTTP 调 api |

### 3.2 禁止项（违反即失败）

1. trader_core 不得导入 trader_model、trader_agents、trader_api —— 专业能力层不依赖模型层，对应 4.3「模型输出不能替代专业引擎的数值结果」。
2. trader_data 不得导入 contracts 以外的任何应用包 —— 数据层不依赖上层。
3. trader_gateway 不得导入 trader_core、trader_data、trader_model、trader_agents、trader_api —— 安全网关只做裁剪与检测，不做业务判断，也不读业务数据。
4. trader_model 不得导入 trader_core、trader_agents、trader_api —— 模型服务层不被业务逻辑反向驱动。
5. trader_agents 不得导入 trader_api —— 编排层不知道 HTTP。
6. 任何包不得导入 apps/web 前端代码。
7. 任何包不得导入另一包的内部模块路径（如 trader_core.engine.settlement），只允许顶层包名导入。

### 3.3 静态检查落地

零依赖检查器 docs/spec/engineering/reference_structure.py，用 Python 标准库 ast 解析导入，检查项：

- S1 目录与依赖清单齐备（apps/ 下 8 个目录；每个 Python 包须有 requirements.txt 与 config/dev、config/prod 两个环境目录；前端包须有 package.json。刻意不查 README / pyproject.toml / 包入口等样板文件：那是落地时的书写规范，不是技术路线，不值得变成一条静态规则）
- S2 层间依赖矩阵（§3.1 允许表）
- S3 §3.2 禁止项逐条
- S4 跨包导入必须是顶层包名（禁深层路径）
- S5 密钥与连接串扫描（受控目录全文正则）
- S6 配置分环境（每个环境一份配置，缺环境即报错，禁止默认值兜底）
- S7 AI 控制边界的结构落点（§5 十条规则）
- S8 依赖清单一致性（包内 import 的第三方包集合与该包 requirements.txt 声明集合求差必须为空）
- S9 第三方依赖白名单（包内 import 的第三方包集合与 §8.1 登记表求差必须为空；要引入新库先改 §8 再改白名单，不得靠“大家都这么写”放行）
- S10 禁 ORM 推 DDL（任何包都不得由 ORM 推表结构；DDL 权威唯一 = storage.schema.json，见 #25）

任何一项 FAIL 即视为违反本约定；CI 与提交前钩子均可跑它。

检查器自带自检（--self）：docs/spec/engineering/samples/apps/ 是合规骨架，扫描后零违规；docs/spec/engineering/samples/apps_bad/ 只保留 6 类代表性违规（层间越界、明文凭据、核心层直持 HTTP 客户端、由 ORM 推 DDL、直连公网、改写历史数据）；其余规则用最小违规片段探针单独验证（每条规则一个独立临时目录，命中即算该规则有效），不必为每条规则再造一整套包。自检输出写在 docs/spec/engineering/verification.txt，当前 10/10 PASS。

## 4. 确定性引擎与模型调用的边界

### 4.1 类型边界

- trader_contracts 区分两类结果：NumericResult（数值结论，字段含值、单位、规则依据、参数版本、口径）与 Suggestion（建议/解释/校验/预警，含理由、置信度、引用）。
- 一切进入看板、结算、风控判定、审批门禁的数值只能来自 NumericResult；Suggestion 只能出现在解释性文案、提示与人工确认路径。
- 模型调用返回的类型只能是 Suggestion 或其子类型；trader_model 对外不暴露任何数值计算入口。

### 4.2 调用边界

- 模型调用只允许从 trader_agents 发起；trader_core 内不得出现任何模型调用点（含 HTTP 客户端封装）。落到结构上：trader_core 与 trader_agents 都不得直接持有 HTTP 客户端（httpx / requests / aiohttp 等），出域能力只在 trader_gateway（裁剪与用途授权）与 trader_model（LinkAI 适配器）两个包里（S7-AI-8）。
- 模型调用必须经过安全网关裁剪与用途授权（§5 第 1 条），调用记录写模型审计（§7.2）。
- 模型不可用时，智能体层降级到确定性路径：直接调用 trader_core，输出 NumericResult 类型的建议数值；任务链不得因模型不可用而中断（对齐 #72 验收）。

### 4.3 人工与自动判定边界

- 风控判定、额度校验、审批放行与否一律由 trader_core 的确定性函数产出结论，模型只可给建议；审批动作由人执行（#60、#69）。
- 未核验数值不进自动判定：合同、规则参数、结算口径缺项时结论留空并标 PENDING，不取默认值（#55 一致做法）。

## 5. AI 控制边界到代码结构的映射

| 方案 4.4 条款 | 结构落点 | 检查器规则 |
|---|---|---|
| 不持交易账号密码 | 凭据只以引用形式存在 | S5：仓库出现 password/secret/token/key/连接串字面量即 FAIL |
| 不写入真实交易系统 | 写入口只在 trader_data，且经注册表双闸 | S7：trader_core、trader_agents、trader_api 中出现写外部系统的调用命名（place_order、submit_order、write_to_trading_system 等）即 FAIL；复用 #44 的 writes_to_trading_system is False 断言 |
| 不改历史数据与审计日志 | 审计与历史表只追加 | S7：出现 delete/update 审计表或历史表的关键字调用即 FAIL；审计写入仅允许 append 语义 |
| 不自行提高额度或放宽风控阈值 | 阈值只能来自配置与规则库 | S7：额度或阈值常量不得在代码里硬编码（出现额度、阈值字面量赋值即 FAIL，须来自配置或规则参数版本） |
| 不用未来数据训练当期交易 | 时间隔离 | S7：trader_core 与 trader_agents 中不得出现回测/训练取数越过 forecast_origin 的入口；防穿越断言以 #45 FeatureLeakageError 语义为准 |
| 不将本企业敏感策略用于其他客户 | 用途授权与租户隔离 | S7：任何出域调用必须携带用途授权标识与裁剪结果，缺一即 FAIL |
| （4.2 敏感经营数据留企业受控域） | 数据层持有原始敏感数据 | S7：trader_core、trader_agents 不得直接引用高敏数据集（D02–D10、D14）原始字段，敏感明细只经 gateway 裁剪后使用 |
| （4.3 网关不得接收未脱敏原始附件） | 出域网关唯一出口 | S7：直接外呼公网模型的代码路径只允许出现在 trader_gateway（网关：裁剪与用途授权）与 trader_model（LinkAI 适配器）两个包，其余包一律不得外呼 |
| （模型输出不能替代引擎数值结果） | 类型边界 | S3 禁止项 1 加 S7：模型返回类型不得出现在数值通道 |
| （4.2 模型调用只许从 trader_agents 发起） | 调用边界 | S7：trader_core 与 trader_agents 不得直接持有 HTTP 客户端；模型调用必须经 trader_agents → trader_model / trader_gateway |

## 6. 配置、密钥与环境

### 6.1 凭据引用

- 只接受三种前缀（沿用 #44 R7）：ENV:（环境变量）、VAULT:（密钥库键名）、FILE:（部署机挂载文件路径）。
- 仓库里的明文密钥、连接串、token 一律判事故；S5 扫描。
- 公开导出注册表时 credentials_ref 一律脱敏为星号。

### 6.2 配置分环境

- 配置目录：apps/<包>/config/<env>/，env ∈ {dev, test, staging, prod}，每个包至少提供 dev 与 prod 两份。
- 环境选择由环境变量指定，缺失即启动失败，不回落默认值（S6）。
- 配置文件只放非敏感项（开关、路径、阈值版本号、模型白名单标识）；凭据一律走 §6.1 的引用。

### 6.3 依赖管理

- Python 侧用 uv 锁定：uv.lock 是唯一权威锁文件；每个包另导出一份 requirements.txt，作为离线交付与 CI 的导出件，不单独升级。目标解释器 CPython 3.12、最低支持 3.11，写在 pyproject.toml 与锁文件里。
- Node 侧用 pnpm 锁定：pnpm-lock.yaml 是唯一权威锁文件。
- 离线交付：依赖缓存或安装包随交付物一起进内网（§8.2）；锁文件入库、缓存不入库，缓存由内网镜像或 pnpm store 重建。

## 7. 日志与审计格式

### 7.1 业务与安全审计

- 事件结构沿用 #29 AU18：event_id、at、actor、roles[]、session_id、dimension、action、resource_type、resource_id、verdict、reason_code、scope、masked_fields[]、grant_id?、request_id、prev_hash、hash。
- 只追加：任何角色都不能删改审计；篡改即断链。

### 7.2 模型调用审计

- 在 AU18 基础上追加：model_id、token_in、token_out、latency_ms、masked_digest（脱敏摘要）、status、fallback_used。
- 审计写入统一走 trader_data 的 append 接口，S7 禁止任何层改写或删除。

### 7.3 运行日志

- 结构化日志，字段固定：ts、level、request_id、actor、module、event、duration_ms、result。
- 禁止在日志中写入完整敏感值；只写脱敏摘要或引用 ID。
- 日志留存等级与期限按方案 07 Q07 待确认项处理，当前不写死。

## 8. 技术栈登记

方案文件只给了六层架构与 CowAgent/LinkAI，未指定 Web 框架；地图把「具体 Web 框架与前端技术栈」列为未定项，由本票决定。本节是该决定的落点：本票其余章节不依赖它，但它决定了 §6.3 依赖管理的形式与 §3.3 S9 白名单的内容。

候选、代价与推荐见本票评论；官方出处、许可证与版本策略待联网可查时补齐（查不到的写「未核实」，不写猜测值）。

### 8.1 登记表

| 项 | 决定 | 唯一权威的锁定机制 | 依据要点 |
|---|---|---|---|
| 后端框架 | FastAPI | uv.lock + 每包导出的 requirements.txt | 自带 OpenAPI 生成与基于注解的依赖注入，能把 §4 的类型边界直接落到接口契约上 |
| 前端 | React + TypeScript + Vite | pnpm-lock.yaml | 构建产物是纯静态文件，运行时不需要 Node 常驻进程，浏览器直连 trader_api 的 HTTP 接口 |
| Python 版本 | CPython 3.12（目标）/ 3.11（最低支持） | pyproject.toml 的 requires-python + uv.lock | 3.11 起标准库自带 tomllib（检查器已在用，源码保留 3.11 以下兼容分支）；小版本随锁文件定，不在本节写死 |
| Python 依赖管理 | uv | uv.lock | 锁文件是单一事实源；requirements.txt 是离线交付与 CI 的导出件，不单独升级 |
| Node 依赖管理 | pnpm | pnpm-lock.yaml | 与 uv 同构的锁文件语义，pnpm store 可在内网复用做离线安装 |
| 结构与边界检查 | 仓内零依赖脚本 reference_structure.py | 本仓源码即权威，无外部锁 | 类型边界要靠静态检查兑现；零依赖脚本可无网跑、无第三方供应链，代价是规则要自己维护（§3.3） |
| lint + format | ruff | uv.lock | 单二进制、离线可装、配置面足够覆盖本仓规模 |
| 类型检查 | mypy（contracts / core / api 三包 strict） | uv.lock | 承担「模型建议 vs 确定性数值」的静态可判性，是类型边界的主要兑现手段 |
| 密钥扫描 | 仓内正则门禁（S5）为主 + detect-secrets 作 CI 补充 | uv.lock + .secrets.baseline | S5 覆盖受控目录且无网可跑；detect-secrets 补历史与 CI 全量扫描 |
| 数据库访问 | SQLAlchemy 2.0 Core，只做参数化查询，不负责建表 | uv.lock | DDL 权威唯一 = storage.schema.json（#25）；Core 不承担建表职责，SQLite → PostgreSQL 的差异收在方言层 |
| OpenAPI 客户端生成 | openapi-generator（生成物入库 + CI 漂移校验 + 生成器版本锁） | 生成器版本锁（字段级细节另立票，见 §10） | 类型边界要前后端两侧都能静态验证；生成物入库才能离线构建 |
| 部署形态 | systemd（生产主形态）+ Docker Compose（可选，仅开发与演示） | 部署清单随包版本走 | 生产主形态不依赖容器运行时；离线镜像仍需企业侧准备 CA 证书、时区与字体 |

### 8.2 离线交付

企业受控域无外网，因此：

- 依赖必须能从内网镜像或缓存完整安装；每个工具都要有官方或仓内可离线执行的方式（依赖缓存、安装包、镜像导入）。
- 锁文件入库、缓存不入库：缓存由内网镜像或 pnpm store 重建，避免把可再生的大文件塞进版本库。
- 构建与门禁在无网环境下必须跑得通 —— §3.3 检查器零依赖正是为此，它不依赖 uv、pnpm 或网络。
- 前端依赖解析必须发生在能联网或已有缓存的构建机上；产物是静态文件，入库或随发布包交付都行。

### 8.3 未采用的方案与理由

| 未采用 | 理由 |
|---|---|
| Django | 自带 ORM 与模板引擎；ORM 建表与 storage.schema.json 的 DDL 权威冲突，admin 与模板引擎对本项目没有价值 |
| Flask | 无原生 OpenAPI 生成与请求校验，§4 的类型边界要另补一层 |
| 裸 sqlite3 / psycopg 直连 | 换库要重写连接层与占位符层；SQLAlchemy Core 把方言差异收在一处 |
| ORM 建表（create_all 一类） | 与 DDL 权威唯一冲突，且 SQLite → PostgreSQL 存在方言降级风险；由 S10 静态拦截 |
| Serverless / BaaS | 依赖外网与第三方托管，受控域不可用；并发不到 20 人也不需要弹性伸缩 |
| gitleaks | Go 二进制，与 Python 工具链版本脱节，受控域预置二进制不合规；detect-secrets 纯 Python |
| npm（相对 pnpm） | 同样有锁文件，但与 uv 的锁文件语义不对齐，且缺少 store 级的离线复用 |
| 生成物不入库 | 离线构建要现场跑 Java 生成器；生成物入库加 CI 漂移校验才能保证可复现 |
| 容器作为生产前提 | 企业 IT 往往不批容器运行时；离线镜像还缺 CA 证书、时区与字体 |

### 8.4 变更规则

- 改任何一项技术栈，必须先改本节（登记表 + 未采用表），再改检查器 S9 白名单（reference_structure.py 的 ALLOWED_THIRD_PARTY），两者必须同一次提交落地，否则 §3.3 会报错。
- 新增第三方包先在本节登记，再进代码；白名单只认本节，不认「大家都这么写」。
- 版本下限以锁文件与研究笔记的一手核实结果为准；研究笔记里标「未核实」的版本号不得写进代码或文档。

### 8.5 版本纪律

本节不写死第三方包的具体版本号：锁文件是构建与运行时的事实来源，实施票以锁文件为准；版本下限待官方出处核实后补齐，查不到的写「未核实」。理由是地图的硬约束「未核验数值不进自动判定」—— 未经一手核实的版本号会污染下游每一张规格票；把「怎么锁」定死，把「锁到哪个版本」交给锁文件，两边都不写猜测值。

## 9. 验收对照

| #32 验收 | 落点 |
|---|---|
| 目录结构与依赖方向写成约定文档且违反能被静态检查发现 | §2、§3、§3.3（reference_structure.py 自检 E1–E10） |
| 每层职责与允许依赖方向明确 | §3.1 表、§3.2 禁止项 |
| 确定性引擎与模型调用边界在代码结构上可见 | §4 类型与调用边界、S3 禁止项 1、S7 |
| 密钥、凭据、连接串不出现在仓库 | §6.1、S5、S6 |
| AI 控制边界逐条对应到代码结构约束 | §5 表（10 条规则） |
| 技术栈登记完整、依赖可锁定且可离线安装 | §8 登记表、§8.2 离线交付、§8.4 变更规则、S9 白名单 |

## 10. 待确认

- 方案 07 Q01–Q08 仍待确认（地图已列），其中 Q07 安全等级日志留存影响 §7.3。
- 内部数据模板确切格式与历史跨度、气象源最终选型，与本票无依赖。
- OpenAPI 生成器（openapi-generator）的版本锁字段与 CI 漂移校验命令，§8 已定方向、字段级细节随 #46 另行立票；本票只定三件事必须同时具备：生成物入库、生成器版本锁、CI 漂移校验。

## 11. 变更记录

- 2026-11-06 v1.0：首版；目录布局、依赖矩阵与禁止项、确定性/模型边界、AI 控制边界结构映射（含 S7-AI-8：核心层与智能体层不得直持 HTTP 客户端）、配置与密钥约定、日志与审计格式、技术栈登记（§8：后端 FastAPI、前端 React + TypeScript + Vite、依赖管理 uv 与 pnpm、结构检查用仓内零依赖脚本、lint ruff、类型检查 mypy、密钥扫描仓内正则加 detect-secrets、SQLAlchemy 2.0 Core、openapi-generator、systemd 加可选 Docker Compose）与离线交付要求；一手出处（官方文档、许可证、版本策略）待联网可查时补齐，查不到的写「未核实」，不写猜测值。
