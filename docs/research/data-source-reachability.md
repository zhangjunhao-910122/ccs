---
title: 公开与外部数据源可达性实测（第一版匿名公开渠道）
ticket: wayfinder:research（公开与外部数据源可达性实测；票号 #43）
status: done
updated: 2026-11-05
---

# 公开与外部数据源可达性实测（第一版匿名公开渠道）

> 口径说明（2026-11-05 父代理给定方案文件为准）：本平台是**湖北单一售电公司的内部系统**，首期数据来自**公司自有历史数据（12 个月以上）**，接入方式是**文件导入**（Excel / CSV / PDF / 标准模板），**不是**公开渠道抓取。因此本票的范围收窄为三类**外部特征类 / 市场公开信息类**来源的可达性实测：
> ① 天气数据（公开 API 或 CSV）；② 节假日 / 交易日历（配置表或公开）；③ 市场公开信息（湖北 / 南方区域规则文件、披露平台公开页、日清月结公告等）。
> **企业内部历史数据不在本票范围内**（走文件导入）。
>
> 硬约束（全程遵守）：只用匿名公开渠道——不登录、不注册、不付费、不索要任何凭据、不绕验证码 / 反爬 / 访问控制、不伪造登录态。拿不到的源一律如实记录「拿不到 / 需账号 / 需付费 + 已查渠道 + 绕行方案」，不编造 URL、字段或数值。
> 全部实测访问日期：**2026-11-05**。

---

## 1. 结论速览

### 1.1 第一版（匿名公开渠道）能用的三类外部通道

| 通道 | 结论 | 代表源 | 能否直接进第一版 |
| --- | --- | --- | --- |
| 天气数据 | **能拿到** | `https://api.open-meteo.com/v1/forecast`（200）、`https://archive-api.open-meteo.com/v1/archive`（200）、`https://wttr.in/Wuhan?format=j1`（200） | ✅ 可作外部特征因子（负荷/气温敏感度、需求侧响应触发条件） |
| 法定节假日 | **能拿到** | `https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm`（200，原文可静态抽）、`https://timor.tech/api/holiday/year/2026`（200 JSON） | ✅ 可作日历基准 |
| 市场公开信息（规则 / 细则原文） | **能拿到** | `http://www.gzpec.cn/law/jygz/...pdf`（200 直链 PDF）、`https://hzj.nea.gov.cn/...pdf`（200）、`https://www.ndrc.gov.cn/...`（200）、`https://www.nea.gov.cn/...`（200）、`https://www.gov.cn/...`（200） | ✅ 可作规则库 / 口径依据（文本解析全部成功） |
| 市场公开信息（湖北本地**数值**） | **拿不到（匿名）** | 湖北省发改委发用电栏目 412、湖北省能源局 DNS 不通、湖北交易平台 newpmos 412 | ❌ 需账号 / 需换网络环境，见 §5 |

### 1.2 第一版**不能**用的（匿名通道的现实约束）

1. **湖北本地披露数值**（实际负荷 6.56、新能源出力 6.54、省间联络线 6.60、现货出清 6.47 等）：湖北省发改委 `fgw.hubei.gov.cn` 的栏目与正文页全部 **HTTP 412**（WAF 拦截），`nyj.hubei.gov.cn`（湖北省能源局）**DNS 解析失败**，`tjj.hubei.gov.cn` 统计公报页无用电量口径（只有综合 / 农业 / 工业）。→ 匿名不可得。
2. **南方区域统一电力交易平台 / 国网新一代交易平台**：`ifdl.poweremarket.com`、`spot.poweremarket.com`、`sso.poweremarket.com`、`gp.poweremarket.com`、`pmos.{sgcc,sd,sx,gs}.sgcc.com.cn`、`www.jspec.com.cn`、`newpmos.hb.sgcc.com.cn` 全部是**纯前端 SPA 空壳**（响应体 534–6,501 B，正文与数据由 JS 拉取），**需已登录会话**。广州电力交易中心公告正文里也明写「详细交易结果请相关经营主体登录南方区域统一电力交易平台查看」→ 数值型结果**需账号**。
3. **国家统计局**：`data.stats.gov.cn/easyquery.htm` **403**（需 token），`www.stats.gov.cn/sj/ndsj/2025/indexch.htm` 只回 887 B frameset。→ 拿不到。
4. **中电联 `cec.org.cn`**：robots.txt 明确 `Disallow: /`，其统计年鉴 / 数据服务为会员付费产品 → **需付费**（本轮未注册、未付费）。
5. **北京电力交易中心 `www.bjpx.com.cn`**：DNS 可解析（43.160.235.235）但连接超时，域名已停售 → **拿不到**。
6. **国家数据开放平台 `data.gov.cn`**：连接失败（000）→ **拿不到**。

### 1.3 对第一版数据策略的直接含义

- **外部特征类**：天气（气温、降水）+ 法定节假日 + 交易日历规则文本 → **第一版就能落**，且全部是低频、单次、匿名可取的。
- **市场公开信息**：规则 / 细则 / 政策原文 → **第一版就能落**（PDF 直链 + 政策库），可作为智能体的规则问答与审计口径来源。
- **湖北本地市场数值**（出清价、出清量、负荷、出力、联络线、日清月结）→ **第一版匿名不可得**。第一版的负荷与交易数据主体必须来自**公司自有历史数据文件导入**（#24 数据导入器），市场数值侧只能先用公开统计口径（月度全社会用电量、全国电力统计、年度市场发展报告）做宏观锚点。
- **结论一句话**：**「天气 + 日历 + 规则文本」三条外部通道在匿名公开渠道内可用；「湖北本地披露数值」这一整类必须靠账号态或换网络环境，是第二版预留位。**

### 1.4 四态统计（§3 逐源实测表共 96 行源条目）

| 四态 | 条数 | 含义与典型 |
| --- | --- | --- |
| **能拿到** | **54** | 匿名可取且正文 / 附件可解析：天气 API ×3、节假日 ×2、规则与政策原文 ×20+、宏观统计 ×3、公告与栏目 ×若干 |
| **拿不到** | **33** | 412 / 403 / DNS 不通 / 404 / 空壳 / 口径不符；含 10 条 000、10 条 412、2 条 403 |
| **需账号** | **8** | 需注册或登录：南方区域统一电力交易平台（ifdl / spot / sso / gp 4 条）、国网 pmos 系列、jspec、data.cma.cn（2 条） |
| **需付费** | **1** | 中电联会员数据服务（`cec.org.cn` robots `Disallow: /`，会员付费；本轮未注册未付费）。另有 `data.cma.cn` 数据下载属「需账号 + 付费订阅」，因公开页为空壳，按需账号计 |

> 原始扫描分布（106 条批量 URL）：`200×83`、`412×10`、`000×10`、`403×2`、`404×1`；补充扫描 9 条：多数 200。完整明细见 `samples/可达性实测/扫描结果总表.tsv`。

---

## 2. 实测方法与边界

### 2.1 环境与工具

- Shell：MSYS2 bash 5.3.15；Python 3.10.4（`/i/python3.10/python`）+ requests 2.31.0。
- **Windows 自带 curl 不可用**：`/c/windows/system32/curl` 报 `schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS (0x8009030E)`，退出码 35；MSYS 环境无 `/usr/bin/curl`。→ 全部改用 Python requests。
- `/tmp` 不可写；临时工作区 `$TEMP/reach/`（`/c/Users/Administrator/AppData/Local/Temp/dsh-ao91v6/reach`），仓库外，不入库。
- 可用解析库：pypdf、pdfminer、pdfplumber、pymupdf(fitz)、olefile、beautifulsoup4、lxml、pandas；`antiword` 位于 `/ucrt64/bin/antiword`；无 LibreOffice / soffice。

### 2.2 扫描器参数（`scan.py`）

| 项 | 取值 |
| --- | --- |
| 方法 | 单次 `GET`，`allow_redirects=True`，`stream=True`，`verify=False`（仅本机自签 / 证书链异常时的取证手段，不改协议） |
| 超时 | connect 12 s / read 35 s |
| UA | 伪装 Chrome 126 桌面 UA（仅用于正常渲染公开页面，未携带任何凭据或 Cookie） |
| 单文件上限 | 40 MB（超出即截断并标注，例：gzpec 202611 PDF 实测 42,008,576 B 被截断） |
| 记录字段 | `http_code / size / ctype / time / redirects / dns(socket.getaddrinfo) / sha256前16位 / title / flags / final_url / error` |
| flags 语义 | `PDF` `OLE_DOC` `ZIP` `EXC`(非 HTML) `SPA_MARKER`(前端空壳) `AUTH_HINT`(登录页特征) `JS_RENDER_TEMPLATE`(正文由 JS 渲染) `BLOCK_4xx` `DOMAIN_FOR_SALE` `SMALL_HTML` |

### 2.3 DNS 解析的已知不可靠性（重要）

扫描器里的 `socket.getaddrinfo` 在本机对部分域名返回失败，但实际 HTTP 请求成功——已确认两例：

- `https://fgw.huangshi.gov.cn/...t20240402_1108080.html` → **HTTP 200**，但 DNS 检查误报 FAIL。
- `https://zfxxgk.ndrc.gov.cn/web/iteminfo.jsp?id=20581` → **HTTP 200**，但 DNS 检查误报 FAIL。

因此**本报告一律以实测 HTTP 状态码为准**，DNS 字段只作参考。

### 2.4 正文抽取方法（含一次关键修正）

- HTML → `BeautifulSoup(html, "html.parser")` 取可见文本。
  **关键修正**：首轮用 `lxml` 解析器时，`www.gov.cn` 政策页正文被整体丢弃（grep「第一条」0 命中，只剩 `yh_login` / `yh_favorite` / `yh_comment` 三个 iframe）；改用 `html.parser` 后，`div.pages_content#UCAP-CONTENT` 正文 100% 抽出（S8 4,344 字、S20 9,128 字、S18 1,162 字、F22 1,898 字、F19 3,062 字）。
- PDF → pymupdf 逐页取文本；扫描版（图片型）PDF 会得到极短文本 → 据此判定「不可解析」。
- DOC（老式 OLE 复合文档）→ olefile 读 WordDocument 流 + piece table 还原：9 号文附件 `.doc`（273,801 B）成功抽出 **14,673 字符 / 11,487 汉字**，并据此解出附表结构（单元格以 `\x07` 分隔、换页以 `\x0c`）。
- **不能解析的判据**：抽出文本 < 200 字且 0 汉字（例：`http://www.hb.sgcc.com.cn/...pdf` 抽出 170 字符、0 汉字 → 确认为**图片型 PDF**）。

### 2.5 robots.txt 检查方法

对 45 个 host 逐个 `GET https://host/robots.txt`，解析 `User-agent` 分组与 `allow / disallow / crawl-delay`，结果落 `samples/可达性实测/robots检查结果.tsv`。

### 2.6 抓取礼仪与合规边界

- **低频、单次**：每个 URL 只取一次，请求间隔 ≥ 0.6 s；arxiv 站点按其 `Crawl-delay: 15` 间隔 ≥ 15 s。
- **对 412 / 403 / 429 不重试、不换 IP、不换 UA 规避**——只记录状态码与响应特征。
- **不登录、不注册、不付费、不索要凭据**；不伪造 Cookie / Token / 会话。
- **不绕验证码**：遇到 WAF 拦截页（412）只取证不改写请求。
- 写入仓库的只有：Markdown / CSV / JSON / TSV 等文本样本。**PDF、DOC 等二进制只记录 URL + 字节数 + SHA256 前 16 位，不入库**（避免仓库膨胀）。

### 2.7 本票的边界

- 只覆盖外部特征类与市场公开信息类；**企业内部历史数据未实测**（走 #24 文件导入）。
- 单机单次实测，**不代表任何时点的可用性**；站点改版、WAF 策略、IP 信誉都可能改变结果。
- 页面级结论是「静态 HTML 是否含正文」，不覆盖浏览器渲染后的可见性。

---

## 3. 逐源实测表

> 下列全部条目访问日期均为 **2026-11-05**。
> 「四态」取值：**能拿到** / **拿不到** / **需账号** / **需付费**。
> 原始扫描全表：`samples/可达性实测/扫描结果总表.tsv`（106 行）、`samples/可达性实测/补充URL扫描结果.tsv`（9 行）、`samples/可达性实测/robots检查结果.tsv`（45 行）；分类编码见 `samples/可达性实测/分类编码说明.md`。

### 3.1 通道① 天气数据

| 源名 | URL | HTTP 状态 | 访问日期 | 需登录/注册 | 反爬 | 格式 | 可解析成规整表 | 覆盖范围与粒度 | 四态 | 绕行方案 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Open-Meteo 预报 API | `https://api.open-meteo.com/v1/forecast` | 200 | 2026-11-05 | 否 | 否 | JSON | ✅ 逐日字段 | 日级；本次取 2026-11-01~11-07，字段 `temperature_2m_max/min`、`precipitation_sum` | 能拿到 | 已作为第一版天气特征源 |
| Open-Meteo 历史归档 API | `https://archive-api.open-meteo.com/v1/archive` | 200 | 2026-11-05 | 否 | 否 | JSON | ✅ 逐日字段 | 日级历史（本次 2025-11-01~07）；可回溯多年，用于负荷模型的历史气温特征 | 能拿到 | 建模历史特征首选 |
| wttr.in | `https://wttr.in/Wuhan?format=j1` | 200（39,428 B） | 2026-11-05 | 否 | 否 | JSON | ⚠️ 结构嵌套（`current_condition` / `weather` 数组） | 3 日预报 + 当前实况 | 能拿到 | 作为 Open-Meteo 的冗余源 |
| 和风天气 devapi | `https://devapi.qweather.com/v7/weather/7d?location=42023` | 403 | 2026-11-05 | 否（但接口返回 `Invalid Host`） | 否 | JSON（错误体） | ❌ | — | 拿不到 | 换 Open-Meteo；如必须用和风需申请自己的 Key（第二版预留位） |
| 彩云天气 | `https://api.caiyunapp.com/v2.5/forecast/daily?location=...` | 404 | 2026-11-05 | 否 | 否 | HTML 错误页 | ❌ | — | 拿不到 | 换 Open-Meteo |
| 中国气象局门户 | `https://www.cma.gov.cn/` | 200（139,024 B） | 2026-11-05 | 否 | 否 | HTML | ⚠️ 资讯页可读，非结构化数据 | 新闻 / 公报 | 能拿到 | 只作新闻线索，不作数据源 |
| 中国气象数据网 | `https://data.cma.cn/` | 200（534 B，SPA_MARKER） | 2026-11-05 | **是**（数据下载需注册） | 否（前端空壳） | SPA 外壳 | ❌ | — | 需账号 | 匿名改用 Open-Meteo；下载权限需注册（第二版预留位） |
| 中央气象台 | `https://www.nmc.cn/` | 200（71,429 B） | 2026-11-05 | 否 | 否 | HTML | ⚠️ 门户可读，逐站数值需 JS | 站点级 | 能拿到 | 只作辅源 |

样本：`samples/天气数据/open-meteo-forecast.json`、`open-meteo-archive.json`、`wttr-in-plain.json`、`武汉逐日气温与降水_样本.csv`（14 行）、`可达性实测.json`。

### 3.2 通道② 节假日 / 交易日历

| 源名 | URL | HTTP 状态 | 访问日期 | 需登录/注册 | 反爬 | 格式 | 可解析成规整表 | 覆盖范围与粒度 | 四态 | 绕行方案 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 国办发明电〔2025〕7 号 2026 年节假日安排 | `https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm` | 200 | 2026-11-05 | 否 | 否 | HTML（正文在 `#UCAP-CONTENT`） | ✅ 人工结构化成 7 行 | 年级：元旦 / 春节 / 清明 / 劳动 / 端午 / 中秋 / 国庆，含调休上班日 | 能拿到 | 唯一权威口径，作为基准表 |
| 同上（国务院公报镜像） | `https://www.gov.cn/gongbao/2025/issue_12406/202511/content_7048922.html` | 200 | 2026-11-05 | 否 | 否 | HTML | ✅ | 同上 | 能拿到 | 与政策库互为备份 |
| timor.tech 节假日 API 2026 | `https://timor.tech/api/holiday/year/2026` | 200（3,428 B） | 2026-11-05 | 否 | 否 | JSON | ✅ 键为 `MM-DD`，含 `holiday/name/wage/rest/after/target` | 日级，标法定假日 / 调休 | 能拿到 | 作为机器可读日历 |
| timor.tech 节假日 API 2025 | `https://timor.tech/api/holiday/year/2025` | 200（2,909 B） | 2026-11-05 | 否 | 否 | JSON | ✅ | 同上（2025） | 能拿到 | 补历史年 |
| GitHub `chinese-calendar` 原始文件 | `https://raw.githubusercontent.com/LKI/chinese-calendar/master/.../holidays.json` | 404 | 2026-11-05 | 否 | 否 | — | ❌ | — | 拿不到 | 用 timor.tech；或改用 PyPI `chinesecalendar` 包 |
| GitHub `holiday-cn` 原始文件 | `https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/src/data/2026.json` | 404 | 2026-11-05 | 否 | 否 | — | ❌ | — | 拿不到 | 同上（仓库默认分支路径与本机假设不符） |
| 广州电力交易中心 通知公告栏目 | `http://www.gzpec.cn/information/zxtz/` | 200（19,983 B） | 2026-11-05 | 否 | 否 | HTML | ✅ 列表（标题 + 日期 + 链接） | 公告级；分页 `index_1.html`~`index_11.html` 全部 200，可回溯至 2024-08 | 能拿到 | 交易安排 / 规则的公开入口 |
| 广州电力交易中心 公告正文页 | `http://www.gzpec.cn/information/zxtz/202611/t20261103_37329.html` | 200 | 2026-11-05 | 否 | 否 | HTML | ✅ 正文 | 含 2027 年度五类交易时间安排 | 能拿到 | **但正文只给安排，不给数值结果** |
| 广州电力交易中心 规则栏目 | `http://www.gzpec.cn/law/jygz/` | 200（46,442 B） | 2026-11-05 | 否 | 否 | HTML | ⚠️ **规则列表由 JS 异步加载**，静态 HTML 只有导航框架 | — | 能拿到（需按 PDF 直链取） | 已知直链形态 `…/law/jygz/<年月>/P0202*.pdf`；见 §3.4 |
| 广州电力交易中心 政策文件栏目 | `http://www.gzpec.cn/law/zcwj/` | 200（43,578 B） | 2026-11-05 | 否 | 否 | HTML | ⚠️ 同上（JS 异步） | — | 能拿到 | 同上 |
| 广州电力交易中心 业务指南栏目 | `http://www.gzpec.cn/law/ywzn/` | 404 | 2026-11-05 | 否 | 否 | — | ❌ | — | 拿不到 | 栏目已下线，改从 `/law/jygz/` 与公告正文取 |
| 广州电力交易中心 公司公告 | `http://www.gzpec.cn/information/gsgg/` | 200（仅 172 B） | 2026-11-05 | 否 | 否 | HTML 空壳 | ❌ | — | 拿不到 | 栏目无内容 |
| 广州电力交易中心 交易信息 / 交易公告 | `http://www.gzpec.cn/information/jyxx/`、`/information/jygg/` | 404 | 2026-11-05 | 否 | 否 | — | ❌ | — | 拿不到 | 数值型结果只在交易平台内（需账号） |
| 南方区域统一电力交易平台 公示公告 | `https://ifdl.poweremarket.com/supt/sr/dmz/mm/info/infoDisclosureAppMain.html?exchangeCode=01` | 200（2,248 B，跳转后为 SPA） | 2026-11-05 | **是** | 否（前端空壳） | SPA 外壳 | ❌ | 披露口径本应覆盖 6.22~6.80 | 需账号 | 改用 gzpec 公告与规则文本（定性可用） |
| 南方区域现货系统 | `https://spot.poweremarket.com/uptspot` → `/uptspot/sr/pt/` | 302 → 200（23,290 B，SPA） | 2026-11-05 | **是** | 否 | SPA 外壳 | ❌ | 出清结果 | 需账号 | 第二版预留位 |
| 南方区域 SSO | `https://sso.poweremarket.com/` | 200（56,906 B，AUTH_HINT） | 2026-11-05 | **是** | 否 | 登录页 | ❌ | — | 需账号 | 第二版预留位 |
| 绿电交易系统 | `https://gp.poweremarket.com/` → `/rept/sr/mp/portaladmin/login.html` | 200 → 302 登录页（11,228 B） | 2026-11-05 | **是** | 否 | 登录页 | ❌ | 绿电交易 | 需账号 | 第二版预留位 |

样本：`samples/交易日历/2026年法定节假日安排.csv`（7 行）、`2026年逐日交易日标记表.csv`（365 行）、`timor-holiday-2026.json`、`timor-holiday-2025.json`、`节假日日历API可达性.json`、`南方区域公开交易安排日程_2025-2027.csv`（13 行）。

### 3.3 通道③ 市场公开信息（规则 / 细则 / 政策原文 / 统计公告）

#### 3.3.1 能拿到（正文或附件全部静态可取）

| 源名 | URL | HTTP 状态 | 访问日期 | 需登录/注册 | 反爬 | 格式 | 可解析成规整表 | 覆盖范围与粒度 | 四态 | 绕行方案 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 发改能源规〔2025〕1656 号《电力中长期市场基本规则》 | `https://www.ndrc.gov.cn/xxgk/zcfb/ghxwj/202512/t20251226_1402666.html` | 200 | 2026-11-05 | 否 | 否 | HTML + PDF 附件 | ⚠️ 正文页壳 + 附件 | 规则全文 | 能拿到 | 取同页 PDF 附件 `…/P020251226622166557290.pdf` |
| 发改能源规〔2025〕976 号《电力市场计量结算基本规则》 | `https://www.ndrc.gov.cn/xxgk/zcfb/ghxwj/202508/t20250806_1399686.html` | 200 | 2026-11-05 | 否 | 否 | HTML + PDF | ⚠️ 同上 | 规则全文 | 能拿到 | 同页 PDF |
| 发改能源规〔2025〕1171 号《电力现货连续运行地区市场建设指引》 | `https://www.nea.gov.cn/20250912/ebbfd999a23b440781f4e5a033abaf9e/c.html` | 200 | 2026-11-05 | 否 | 否 | HTML + PDF | ⚠️ 同上 | 规则全文 | 能拿到 | 同页 PDF |
| 发改能源规〔2023〕1217 号《电力现货市场基本规则（试行）》 | `https://zfxxgk.nea.gov.cn/2023-09/07/c_1310741791.htm` | 200 | 2026-11-05 | 否 | 否 | HTML + PDF | ⚠️ 同上 | 规则全文 | 能拿到 | 发改委镜像 PDF `https://www.ndrc.gov.cn/xxgk/zcfb/ghxwj/202309/P020230915357678894853.pdf`（SHA256 前 16 位与能源局版一致 `8c41aa22d5a2b06a`） |
| 《电力市场信息披露基本规则》国能发监管〔2024〕9 号 | `https://zfxxgk.nea.gov.cn/2024-01/31/c_1310763726.htm` | 200 | 2026-11-05 | 否 | 否 | HTML + **DOC 附件** | ✅ 附表已结构化（130 行 CSV） | 披露科目字典：6.22 交易日历、6.47 现货申报出清、5.4 代理购电、2.7 零售套餐 | 能拿到 | `.doc` 附件用 OLE piece table 解析（见 §2.4） |
| 9 号文答记者问 | `https://www.nea.gov.cn/2024-02/07/c_1310763802.htm` | 200 | 2026-11-05 | 否 | 否 | HTML | ✅ | 政策解读 | 能拿到 | — |
| 《南方区域电力现货市场实施细则（2026 年 V1.0 版）》 | `http://www.gzpec.cn/law/jygz/202611/t20261103_37339.html` | 200 | 2026-11-05 | 否 | 否 | HTML + PDF | ✅ 正文 136,399 字符 | 2.7 日前出清结果发布、3.6 实时出清 | 能拿到 | PDF 直链 `…/P020261103633894465723.pdf` |
| 《广西电力市场信息披露实施细则（2026 年 V1.0 版）》 | `http://www.gzpec.cn/law/jygz/202611/t20261103_37340.html` | 200 | 2026-11-05 | 否 | 否 | HTML + PDF | ✅ **附表 1 全部 128 行已结构化** | 6.22 / 6.39 / 6.47 / 6.49 / 6.54 / 6.56 / 6.60 等逐科目口径 | 能拿到 | PDF 直链 `…/P020261103634657707697.pdf` |
| 《广西电力市场现货结算实施细则（2026 年 V2.0 版）》 | `http://www.gzpec.cn/law/jygz/202611/P020261103634657295176.pdf` | 200 | 2026-11-05 | 否 | 否 | PDF | ✅ | 结算规则 | 能拿到 | 直链 |
| 《云南省电力需求响应市场交易方案（审定稿）》 | `http://www.gzpec.cn/law/jygz/202611/P020261103637169824316.pdf` | 200 | 2026-11-05 | 否 | 否 | PDF | ✅ | 需求响应 | 能拿到 | 直链 |
| 南方区域市场注册实施细则（2026 V1.0） | `http://www.gzpec.cn/law/jygz/202611/t20261103_37341.html` | 200 | 2026-11-05 | 否 | 否 | HTML | ⚠️ 壳 + 附件 | 注册规则 | 能拿到 | 同页 PDF |
| 云南 6 份细则及配套方案 | `http://www.gzpec.cn/law/jygz/202611/t20261103_37343.html`、`…_37344.html` | 200 | 2026-11-05 | 否 | 否 | HTML | ⚠️ 壳 + 附件 | 省级细则 | 能拿到 | 同页 PDF |
| 发改价格〔2021〕1093 号 分时电价机制 | `https://www.gov.cn/zhengce/zhengceku/2021-07/29/content_5628297.htm` | 200 | 2026-11-05 | 否 | 否 | HTML（`html.parser`） | ✅ 4,344 字 | 峰谷价差 ≥3:1 / ≥4:1、尖峰上浮 ≥20%、深谷适用范围 | 能拿到 | **必须用 `html.parser`**（lxml 会丢正文） |
| 《电力负荷管理办法（2023 年版）》 | `https://www.gov.cn/zhengce/zhengceku/202310/content_6907504.htm` | 200 | 2026-11-05 | 否 | 否 | HTML + PDF | ✅ | 政策全文 | 能拿到 | PDF 附件 `…/P020231006397081632369.pdf`（5,402 字符全文） |
| 《电力需求侧管理办法（2023 年版）》 | `https://www.gov.cn/zhengce/zhengceku/202310/content_6907311.htm` | 200 | 2026-11-05 | 否 | 否 | HTML + PDF | ✅ | 政策全文 | 能拿到 | PDF 附件 `…/P020231002407934261335.pdf`（6,618 字符全文） |
| 发改办体改〔2025〕394 号 | `https://www.gov.cn/zhengce/zhengceku/202505/content_7022299.htm` | 200 | 2026-11-05 | 否 | 否 | HTML | ✅ 3,062 字 | 现货市场建设部署 | 能拿到 | 发改委镜像 `https://www.ndrc.gov.cn/xxgk/zcfb/tz/202504/t20250429_1397488.html` |
| 《售电公司管理办法》1595 号 | `https://www.gov.cn/gongbao/content/2022/content_5672671.htm` | 200 | 2026-11-05 | 否 | 否 | HTML | ✅ 9,128 字 | 售电公司经营与监管 | 能拿到 | 国务院公报版 |
| 发改办价格〔2021〕809 号 代理购电 | `https://www.ndrc.gov.cn/xxgk/zcfb/tz/202110/t20211026_1300892.html` | 200 | 2026-11-05 | 否 | 否 | HTML | ✅ 4,143 字 | 代理购电机制 | 能拿到 | — |
| 发改办价格〔2022〕1047 号 代理购电价格形成机制 | `https://www.gov.cn/zhengce/zhengceku/2023-01/10/content_5736123.htm` | 200 | 2026-11-05 | 否 | 否 | HTML | ✅ 1,898 字 | 低价电量优先匹配、偏差考核、自 2023-01-01 执行 | 能拿到 | — |
| 第四监管周期省级电网输配电价 1077 号 | `https://www.ndrc.gov.cn/xxgk/zcfb/tz/202607/t20260710_1406431.html` | 200 | 2026-11-05 | 否 | 否 | HTML + PDF | ⚠️ 壳 + 附件 | 输配电价 | 能拿到 | 同页 PDF |
| 同意南方区域电力市场转入正式运行 715 号 | `https://www.ndrc.gov.cn/xxgk/zcfb/tz/202610/t20261028_1408423.html` | 200 | 2026-11-05 | 否 | 否 | HTML | ⚠️ 壳 + 附件 | 南方区域正式运行 | 能拿到 | 同页 PDF |
| 华中能源监管局 湖北中长期市场实施细则（2026-04 正式稿） | `https://hzj.nea.gov.cn/dtyw/tzgg/202604/P020260414530138531095.pdf` | 200 | 2026-11-05 | 否 | 否 | PDF | ✅ | 湖北省级中长期规则 | 能拿到 | 另一副本 `https://hzj.nea.gov.cn/xxgk/zcfg/202604/P020260414522292646714.pdf`（SHA256 前 16 位同为 `bf66396868a2a0d3`） |
| 华中能源监管局 通知公告 / 政策法规栏目 | `https://hzj.nea.gov.cn/dtyw/tzgg/`、`https://hzj.nea.gov.cn/xxgk/zcfg/` | 200 | 2026-11-05 | 否 | 否 | HTML | ✅ | 栏目级 | 能拿到 | 栏目页可达（此前记为 403，已复测解除） |
| 华中能源监管局 首页（湖北相关条目 3 条） | `https://hzj.nea.gov.cn/` | 200（50,075 B） | 2026-11-05 | 否 | 否 | HTML | ✅ 标题 + 日期 + 链接 | 监管动态 | 能拿到 | 含湖北电力市场主体与许可证类条目 |
| 华北能源监管局 首页 | `https://hbj.nea.gov.cn/` | 200（55,690 B） | 2026-11-05 | 否 | 否 | HTML | ✅ | 华北区域（京/津/冀/晋/蒙） | 能拿到 | **注意不是湖北**，误标风险点 |
| 《湖北省电力零售市场实施细则》鄂电交易〔2025〕9 号 | `https://newpmos.hb.sgcc.com.cn/px-settlement-infpubmeex/fileService/download?fileId=n474b336b154747969a7c7207d22f4387` | 200（1,102,522 B，`application/octet-stream`） | 2026-11-05 | 否 | 否 | PDF | ✅ | 零售套餐规则（第十八至二十五条） | 能拿到 | **文件直链匿名可取**（平台首页被 412 拦，但直链可下） |
| 2024 年湖北省电力市场交易实施方案 | `http://fgw.hubei.gov.cn/fbjd/xxgkml/jgzn/wgdw/nyj/dlddc/tzgg/202312/P020231211409441093743.pdf` | 200（899,676 B） | 2026-11-05 | 否 | 否 | PDF | ✅ | 湖北年度交易方案 | 能拿到 | **同为省发改委域下 PDF 直链可取**（栏目页 412） |
| 鄂发改价管〔2024〕77 号 湖北工商业分时电价机制 | `http://fgw.yichang.gov.cn/content-63074-983392-1.html` | 200（13,634 B） | 2026-11-05 | 否 | 否 | HTML | ✅ 1,701 字，时段表已结构化 | 尖/高/平/谷时段与 1.8:1.49:1:0.48 比例 | 能拿到 | 宜昌市发改委转载；黄石市发改委政策解读页 `https://fgw.huangshi.gov.cn/xxgk/zcwj/zcwjyj/202404/t20240402_1108080.html` 也 200 |
| 国网湖北代理购电工商业用户电价表 2022-07 | `http://www.hb.sgcc.com.cn/html/files/2022-07/29/20220729154457454933047.pdf` | 200（20,111,105 B） | 2026-11-05 | 否 | 否 | PDF | ❌ **图片型 PDF，抽出仅 170 字符、0 汉字** | 代理购电价格表 | 能拿到（文件）/ 拿不到（数据） | 需 OCR 或换可解析版本；湖北代理购电数值第一版从公司自有历史数据取 |
| 湖北现货细则 V3.0 征求意见稿（第三方镜像） | `http://cdn.iesplaza.com/uploads/files/20241115/1731634839759868.pdf` | 200（4,937,565 B） | 2026-11-05 | 否 | 否 | PDF | ✅ | 湖北现货细则 | 能拿到 | **第三方镜像，仅作交叉校验，不作权威引用** |
| 信息披露基本规则 9 号文（第三方镜像 PDF） | `https://eesia.cn/upload/files/2024/2/a158f270740388ca.pdf` | 200（1,242,093 B） | 2026-11-05 | 否 | 否 | PDF | ✅ | 同 9 号文 | 能拿到 | 镜像源；权威源仍走 zfxxgk.nea.gov.cn |
| 湖北现货细则镜像站 | `https://www.yuandian.space/` | 200（1,182,526 B） | 2026-11-05 | 否 | 否 | HTML | ⚠️ | 镜像 | 能拿到 | 镜像源，非权威 |
| 广东省发改委门户 | `https://drc.gd.gov.cn/` | 200（104,207 B） | 2026-11-05 | 否 | 否 | HTML | ⚠️ 门户 | 省级 | 能拿到 | 此前记为不可达，已复测解除 |
| 浙江省能源局转载 9 号文 | `https://fzggw.zj.gov.cn/art/2024/11/19/art_1229831137_2536154.html` | 200（34,430 B） | 2026-11-05 | 否 | 否 | HTML | ✅ 7,841 字 | 省级转载 | 能拿到 | 省级转载佐证 |
| 全国全社会用电量（1—9 月 2026） | `https://www.nea.gov.cn/20261023/b1ac9a8faea04deebf4c43a7dcfeef61/c.html` | 200 | 2026-11-05 | 否 | 否 | HTML | ✅ 17 行 CSV | 月度全社会用电量（三产分项） | 能拿到 | 宏观锚点，不含湖北细分 |
| 全国电力统计数据（1—9 月 2026） | `https://www.nea.gov.cn/20261026/7e48f52542c94c0f99c166262453627d/c.html` | 200 | 2026-11-05 | 否 | 否 | HTML | ✅ | 装机 / 利用小时 | 能拿到 | 宏观锚点 |
| 《2025 年度中国电力市场发展报告》 | `http://www.nea.gov.cn/20260609/2d1bf220b5a642e2b1199a015bd6cf13/202606092d1bf220b5a642e2b1199a015bd6cf13_073fd3822bbbd048df9c5e8a6d3befc423.pdf` | 200（3,126,825 B） | 2026-11-05 | 否 | 否 | PDF | ✅ 11 行关键指标 CSV | 全国市场化交易电量等 | 能拿到 | 宏观锚点 |
| 能源局新闻发布栏目 | `https://www.nea.gov.cn/xwfb/` | 200 | 2026-11-05 | 否 | 否 | HTML | ⚠️ | 资讯 | 能拿到 | — |
| 发改委信息公开库镜像（1656 号） | `https://zfxxgk.ndrc.gov.cn/web/iteminfo.jsp?id=20581` | 200（47,441 B） | 2026-11-05 | 否 | 否 | HTML | ✅ | 备查 | 能拿到 | — |
| 新华网 首个区域电力市场正式运行 | `https://www.xinhuanet.com/politics/20261024/bbb1526730cf422094ec174bc3c2abe1/c.html` | 200 | 2026-11-05 | 否 | 否 | HTML | ✅ | 资讯 | 能拿到 | — |
| arXiv 条目（负荷预测 / 电价预测） | `https://arxiv.org/abs/2408.05628`、`/1810.08418` | 200 | 2026-11-05 | 否 | 否 | HTML | ✅ 摘要 | 方法参考 | 能拿到 | 须遵守 `Crawl-delay: 15`；`/abs/2302.12168` 本次连接异常（EXC） |

#### 3.3.2 拿不到 / 需账号 / 需付费

| 源名 | URL | HTTP 状态 | 访问日期 | 需登录/注册 | 反爬 | 格式 | 可解析成规整表 | 覆盖范围与粒度 | 四态 | 绕行方案 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 湖北省发改委「能源—数据/发用电情况」栏目 | `https://fgw.hubei.gov.cn/fgjj/sjsfg/sjfx/lx/nyjs/` | 412 | 2026-11-05 | 否 | **是**（WAF 拦截页 3,128 B） | 拦截页 | ❌ | 月度发用电 | 拿不到 | 换网络环境重试；或以湖北统计公报 / 能源局全国口径替代 |
| 湖北 2025 年 12 月全省发用电情况 | `https://fgw.hubei.gov.cn/.../202601/t20260113_5854389.shtml` | 412 | 2026-11-05 | 否 | 是 | 拦截页 | ❌ | 月度 | 拿不到 | 同上 |
| 湖北 2026 年 9 月全省发用电情况 | `http://fgw.hubei.gov.cn/.../202610/t20261012_6030936.shtml` | 412 | 2026-11-05 | 否 | 是 | 拦截页 | ❌ | 月度 | 拿不到 | 同上 |
| 鄂发改价管〔2024〕77 号 省发改委原始页 | `https://fgw.hubei.gov.cn/fbjd/zc/gfwj/gf/202404/t20240401_5141591.shtml` | 412 | 2026-11-05 | 否 | 是 | 拦截页 | ❌ | 政策原文 | 拿不到 | **已由宜昌市发改委转载页 200 取得全文**（§3.3.1） |
| 湖北现货转正式运行通知 | `https://fgw.hubei.gov.cn/fbjd/zc/zcwj/tz/202506/t20250613_5691029.shtml` | 412 | 2026-11-05 | 否 | 是 | 拦截页 | ❌ | 政策 | 拿不到 | 已由华中监管局报道 `https://hzj.nea.gov.cn/dtyw/jgdt/202506/t20250610_282601.html`（200）取得 |
| 湖北代理购电通知 | `https://fgw.hubei.gov.cn/fbjd/xxgkml/jgzn/nsjg/jgglc/tzgg/202205/t20220519_4133488.shtml` | 412 | 2026-11-05 | 否 | 是 | 拦截页 | ❌ | 政策 | 拿不到 | 代理购电机制改用国家层面 809 / 1047 号文（200） |
| 湖北迎峰度夏负荷创新高 | `https://fgw.hubei.gov.cn/.../202508/t20250801_5736396.shtml` | 412 | 2026-11-05 | 否 | 是 | 拦截页 | ❌ | 资讯 | 拿不到 | 换网络环境 |
| 湖北省能源局 | `http://nyj.hubei.gov.cn/` | 000（DNS 解析失败 `NameResolutionError`） | 2026-11-05 | 否 | 否 | — | ❌ | 省级能源政策 | 拿不到 | 改用 `fgw.hubei.gov.cn`（需换网络）+ 宜昌 / 黄石转载页 |
| 湖北省统计局 统计公报（2025 年） | `https://tjj.hubei.gov.cn/tjsj/tjgb/ndtjgb/qstjgb/202603/t20260318_5894639.shtml` | 200（40,215 B，正文 7,038 字） | 2026-11-05 | 否 | 否 | HTML | ⚠️ **只有综合 / 农业 / 工业口径，无用电量分项** | 年度 | 拿不到（口径不符） | 用能源局全国用电量口径；湖北细分待账号态 |
| 湖北省统计局 统计年鉴 / 统计公报栏目 | `https://tjj.hubei.gov.cn/tjsj/sjkscx/tjnj/`、`/tjsj/tjgb/` | 200（**仅 71 B**） | 2026-11-05 | 否 | 否 | 空壳 | ❌ | — | 拿不到 | 栏目页由 JS 渲染 |
| 湖北电力交易平台 newpmos 首页 / 栏目 | `https://newpmos.hb.sgcc.com.cn/`、`/index` | 412 | 2026-11-05 | 否 | 是（2,456 / 2,462 B 拦截页） | 拦截页 | ❌ | 湖北交易与披露 | 拿不到 | **但该域文件直链匿名可取**（零售细则 PDF 已取得） |
| 国网湖北省电力公司门户 | `https://www.hb.sgcc.com.cn/` | 000（ConnectTimeout 12 s） | 2026-11-05 | 否 | 否 | — | ❌ | 代理购电 / 电价公告 | 拿不到 | 改用该域 PDF 直链；代理购电数值走公司自有数据 |
| 广东省交易平台 | `https://pm.gd.csg.cn/` | 412 | 2026-11-05 | 否 | 是（2,418 B） | 拦截页 | ❌ | 广东现货 | 拿不到 | 换网络环境；本票不依赖 |
| 北京电力交易中心 | `https://www.bjpx.com.cn/` | 000（DNS 通 43.160.235.235，ConnectTimeout） | 2026-11-05 | 否 | 否 | — | ❌ | 跨区交易 | 拿不到 | **域名已停售**；改用广州电力交易中心（南方区域） |
| 国家统计局 easyquery 接口 | `https://data.stats.gov.cn/easyquery.htm` | 403（124.232.185.71） | 2026-11-05 | 否 | 是 | 错误页 | ❌ | 分省用电量 | 拿不到 | 需 token（第二版预留位）；第一版用能源局口径 |
| 国家统计局《中国统计年鉴 2025》入口 | `https://www.stats.gov.cn/sj/ndsj/2025/indexch.htm` | 200（887 B，frameset） | 2026-11-05 | 否 | 否 | frameset | ❌ | 分省年度 | 拿不到 | 需逐帧子页 + 参数，本轮未展开 |
| 国家数据开放平台 | `https://data.gov.cn/` | 000（连接失败） | 2026-11-05 | 否 | 否 | — | ❌ | 开放数据 | 拿不到 | 不可达；改用已验证的部委直连源 |
| 能源局年度报告栏目 | `https://zfxxgk.nea.gov.cn/1310741791/gzbg.htm` | 404（548 B） | 2026-11-05 | 否 | 否 | 错误页 | ❌ | 年度报告 | 拿不到 | 栏目已变更；已知报告 PDF 直链仍 200（§3.4） |
| 广东省能源局 | `https://energy.gd.gov.cn/` | 000 | 2026-11-05 | 否 | 否 | — | ❌ | 省级 | 拿不到 | 本机不可达；换网络环境 |
| 山西省能源局 | `https://sxnyj.shanxi.gov.cn/` | 000 | 2026-11-05 | 否 | 否 | — | ❌ | 省级 | 拿不到 | 本机不可达 |
| 浙江省能源局 | `https://nyj.zj.gov.cn/` | 000 | 2026-11-05 | 否 | 否 | — | ❌ | 省级 | 拿不到 | 本机不可达；浙江政策改走 `fzggw.zj.gov.cn`（200） |
| 甘肃省能源局 | `https://nyj.gansu.gov.cn/` | 000 | 2026-11-05 | 否 | 否 | — | ❌ | 省级 | 拿不到 | 本机不可达 |
| 山东省能源局 | `https://nyj.shandong.gov.cn/` | 403（124.128.19.244） | 2026-11-05 | 否 | 是 | 错误页 | ❌ | 省级 | 拿不到 | 换网络环境 |
| 国网新一代电力交易平台 pmos（国网 / 山东 / 山西 / 甘肃） | `https://pmos.sgcc.com.cn/`、`pmos.sd`、`pmos.sx`、`pmos.gs.sgcc.com.cn/` | 200（6,501 B 外壳）；后三者本机 000 | 2026-11-05 | **是** | 否（前端空壳） | SPA 外壳 | ❌ | 交易与披露 | 需账号 | 第二版预留位 |
| 江苏电力交易中心 | `https://www.jspec.com.cn/` | 200（6,501 B 外壳） | 2026-11-05 | **是** | 否 | SPA 外壳 | ❌ | 交易 | 需账号 | 第二版预留位；本票聚焦湖北/南方区域 |
| 中电联统计栏目详情页 | `https://cec.org.cn/detail/index.html?3-306559` | 200（6,212 B，JS_RENDER_TEMPLATE） | 2026-11-05 | 否（正文需 JS） | 否 | 外壳 | ❌ | 行业统计 | 拿不到 | 门户 `https://www.cec.org.cn/` 200（22,336 B）可读；数值明细需会员 |
| 中电联会员数据服务 | `https://www.cec.org.cn/` | 200 | 2026-11-05 | **是** | 否 | HTML 门户 | ❌ | 年鉴 / 会员统计 | **需付费** | **robots.txt 为 `Disallow: /`（禁止抓取）**；只能人工查阅 / 采购，本轮未注册、未付费 |
| 中国气象数据网数据下载 | `https://data.cma.cn/` | 200（534 B 空壳） | 2026-11-05 | **是** | 否 | SPA | ❌ | 气象数值 | 需账号（数据服务付费） | 匿名改用 Open-Meteo；如需官方数据需注册 / 付费（第二版预留位） |
| 广州市政务 / 湖北黄石市发改委 | `https://www.hbqj.gov.cn/.../t20241125_5427299.html` | 000（连接失败） | 2026-11-05 | 否 | 否 | — | ❌ | 政策转载 | 拿不到 | 换网络环境；宜昌市发改委转载页可用 |
| 能源局无 www 主机名的稿件 | `https://nea.gov.cn/20261027/...`、`/20261024/...` | 000 | 2026-11-05 | 否 | 否 | — | ❌ | 全国交易电量稿 | 拿不到 | **改用 `www.nea.gov.cn` 同名主机**（S16 / S17 均 200） |

### 3.4 二进制源清单（只记录，不入库）

> PDF / DOC 等二进制**不复制进仓库**，仅记录 URL、字节数、SHA256 前 16 位，供后续完整性核对。

| 源 | URL | 字节数 | SHA256 前 16 位 | 能否解析 |
| --- | --- | --- | --- | --- |
| 《电力中长期市场基本规则》PDF | `https://www.ndrc.gov.cn/xxgk/zcfb/ghxwj/202512/P020251226622166557290.pdf` | 369,145 | `db9a7d6aad327251` | ✅ |
| 《电力市场计量结算基本规则》PDF | `https://www.ndrc.gov.cn/xxgk/zcfb/ghxwj/202508/P020250806612959537783.pdf` | 285,207 | `cd1b3bb9342ea3ae` | ✅ |
| 1171 号指引 PDF | `https://www.nea.gov.cn/20250912/ebbfd999a23b440781f4e5a033abaf9e/20250912ebbfd999a23b440781f4e5a033abaf9e_51b14a6ef20c954f42a16c9e3044fa5875.pdf` | 202,817 | `8cac172c979f4946` | ✅ |
| 1217 号文 PDF（能源局 / 发改委镜像同哈希） | `https://zfxxgk.nea.gov.cn/1310741791_16950032432671n.pdf` | 641,987 | `8c41aa22d5a2b06a` | ✅ |
| 9 号文附表 DOC | `https://zfxxgk.nea.gov.cn/1310763726_17072197518981n.doc` | 273,801 | `6aaf7a70f9a2c3b2` | ✅（OLE piece table，11,487 汉字） |
| 《电力负荷管理办法》PDF | `https://www.gov.cn/zhengce/zhengceku/202310/P020231006397081632369.pdf` | 225,804 | `fc8687c2f06e1a1e` | ✅ |
| 《电力需求侧管理办法》PDF | `https://www.gov.cn/zhengce/zhengceku/202310/P020231002407934261335.pdf` | 233,121 | `81a606c6ae537431` | ✅ |
| 南方区域现货实施细则 PDF | `http://www.gzpec.cn/law/jygz/202611/P020261103633894465723.pdf` | 2,171,367 | `978d7d7179e3629f` | ✅ |
| 广西信息披露实施细则 PDF | `http://www.gzpec.cn/law/jygz/202611/P020261103634657707697.pdf` | 312,729 | `b450611fc0c24830` | ✅ |
| 广西现货结算实施细则 PDF | `http://www.gzpec.cn/law/jygz/202611/P020261103634657295176.pdf` | 558,861 | `f186f6f9dc66ac9e` | ✅ |
| 云南需求响应交易方案 PDF | `http://www.gzpec.cn/law/jygz/202611/P020261103637169824316.pdf` | 527,436 | `63c4a852da5e8a39` | ✅ |
| gzpec 202611 另一 PDF 附件 | `http://www.gzpec.cn/law/jygz/202611/P020261103637165600112.pdf` | 42,008,576（被 40 MB 上限截断，实际更大） | `765fad8c4d50a96a` | ⚠️ 未完整下载 |
| 湖北中长期市场实施细则 PDF | `https://hzj.nea.gov.cn/dtyw/tzgg/202604/P020260414530138531095.pdf` | 282,052 | `bf66396868a2a0d3` | ✅ |
| 华中监管局 2026-01 通知 PDF | `https://hzj.nea.gov.cn/dtyw/tzgg/202601/P020260126765926019405.pdf` | 502,985 | `22564534ed6ed335` | ✅ |
| 《湖北省电力零售市场实施细则》PDF | `https://newpmos.hb.sgcc.com.cn/px-settlement-infpubmeex/fileService/download?fileId=n474b336b154747969a7c7207d22f4387` | 1,102,522 | `2cc763afc8c37e1d` | ✅ |
| 2024 年湖北省电力市场交易实施方案 PDF | `http://fgw.hubei.gov.cn/fbjd/xxgkml/jgzn/wgdw/nyj/dlddc/tzgg/202312/P020231211409441093743.pdf` | 899,676 | `914e6ca8cf9fdd3b` | ✅ |
| 湖北现货细则 V3.0 征求意见稿（镜像） | `http://cdn.iesplaza.com/uploads/files/20241115/1731634839759868.pdf` | 4,937,565 | `f050c0337a7b25ce` | ✅ |
| 9 号文镜像 PDF | `https://eesia.cn/upload/files/2024/2/a158f270740388ca.pdf` | 1,242,093 | `15d9736333aa0e9b` | ✅ |
| 《2025 年度中国电力市场发展报告》PDF | `http://www.nea.gov.cn/20260609/2d1bf220b5a642e2b1199a015bd6cf13/202606092d1bf220b5a642e2b1199a015bd6cf13_073fd3822bbbd048df9c5e8a6d3befc423.pdf` | 3,126,825 | `ccba4aba3e738090` | ✅ |
| 国网湖北代理购电价表（图片型 PDF） | `http://www.hb.sgcc.com.cn/html/files/2022-07/29/20220729154457454933047.pdf` | 20,111,105 | `c8264214e11e3bac` | ❌ 图片型（0 汉字） |

---

## 4. 三类外部通道细述

### 4.1 通道① 天气数据

**结论：能用。** 这是三条通道里最干净的一条——纯公开 JSON API，匿名、无 Cookie、响应结构固定、字段语义稳定（`temperature_2m_max` / `temperature_2m_min` / `precipitation_sum`）。

- **推荐组合**：`open-meteo forecast`（D+1 至未来 7–16 天）+ `open-meteo archive`（历史回溯，用于训练期特征对齐）+ `wttr.in`（冗余 / 交叉校验）。
- **粒度**：本轮实测只验证到**日级**。**小时级 / 分钟级接口未实测**（open-meteo 支持 `hourly` 参数，未在本轮验证），第一版若需小时级需补测。
- **坐标口径**：本次用武汉市中心 30.5928N, 114.3055E、`Asia/Shanghai` 时区。湖北全省跨 30°–33°N，实际应按公司主要供电范围取多点或分区坐标。
- **数据许可与署名**：Open-Meteo 为 CC-BY 4.0（要求署名），wttr.in 有非商业用途限制——**商用前需确认许可条款**，本轮未核实其具体条款文本。

### 4.2 通道② 节假日 / 交易日历

**结论：法定节假日能用；真正的「交易日历」匿名不可得，只有公告级的「交易安排预安排」可用。**

- **法定节假日**：gov.cn 原文（唯一权威）+ timor.tech API（机器可读）双源齐备，已生成 365 行逐日标记表（普通工作日 242 / 普通休息日 84 / 法定节假日 33 / 调休上班日 6）。
- **交易日历（6.22 口径：年 / 月 / 周 / 多日 / 日各类交易安排）**：
  - 权威口径在**南方区域统一电力交易平台**（`ifdl.poweremarket.com`），匿名访问只得到 2,248 B 的 SPA 外壳 → **需账号**。
  - 公开可得的替代是**广州电力交易中心公告里的年度 / 月度交易开市预安排**（本轮已结构化 13 条到 `samples/交易日历/南方区域公开交易安排日程_2025-2027.csv`），但其粒度是「交易环节 + 时间窗口」，**不是逐日交易日历**，且公告明确「具体开市安排请市场成员关注南方区域统一电力交易平台发布的跨省跨区交易日历、交易公告」。
  - 2026-08-28 公告已把南方区域跨省跨区中长期改为**按自然日连续组织**（公告正文原文：「自2026年8月28日起按自然日连续组织南方区域跨省跨区电力中长期交易…具体开市安排请市场成员关注南方区域统一电力交易平台发布的跨省跨区交易日历、交易公告」）→ 这意味着**逐日交易日历的重要性在上升**，而它恰好在需账号区。
  - 补充来源：《南方区域电力市场中长期电能量交易实施细则（2025 年 V1.0 版）》与《电力市场信息披露基本规则》9 号文 6.22 条（已结构化入样本 CSV）给出**披露义务口径**，可作为系统内的规则依据，但不能替代逐日日历数据。

### 4.3 通道③ 市场公开信息

**结论：分两半——「规则与文本」能拿到，「湖北本地数值」匿名拿不到。**

**(a) 规则 / 细则 / 政策原文 —— 能拿到，且可结构化**

- 国家层：发改能源规〔2025〕1656 号、〔2025〕976 号、〔2023〕1217 号、〔2025〕1171 号、信息披露 9 号文、《电力负荷管理办法》《电力需求侧管理办法》、发改办体改〔2025〕394 号、1595 号、1093 号、809 号、1047 号 —— 全部 200，正文或附件可解析。
- 区域 / 省级：南方区域现货实施细则（2026 V1.0）、南方区域市场注册实施细则（2026 V1.0）、广西信息披露细则（2026 V1.0，含附表 1 全部 128 行已结构化）、广西现货结算细则（2026 V2.0）、云南 6 份细则、湖北中长期市场实施细则（2026-04 正式稿，来自华中能源监管局）、湖北零售市场实施细则（鄂电交易〔2025〕9 号，来自湖北交易平台**文件直链**）、湖北工商业分时电价机制（鄂发改价管〔2024〕77 号，宜昌市发改委转载全文）。
- **披露科目字典已结构化**：`samples/披露科目结构/国家9号文_信息披露内容附表_结构化.csv`（130 行）与 `samples/披露科目结构/广西披露细则2026V1_附表1_信息披露内容.csv`（128 行），直接对应本项目要盯的 6.22 / 6.39 / 6.47 / 6.49 / 6.54 / 6.56 / 6.60 七个科目。
- **可解析性的实战结论**：披露类 PDF 基本都是文本层（`pymupdf` 直接出字），唯一的例外是国网湖北代理购电价表（图片型 PDF，0 汉字）。

**(b) 湖北本地市场数值 —— 匿名拿不到**

- 湖北省发改委 `fgw.hubei.gov.cn` 的**栏目页与正文页统一 412**（WAF），但**同域的 PDF 附件直链是 200**（2024 年湖北交易实施方案 899,676 B 已取到）→ 「栏目不可读、附件可直取」是本轮最有操作价值的发现。
- 湖北省能源局 `nyj.hubei.gov.cn` DNS 不通；`tjj.hubei.gov.cn` 统计公报无用电量口径。
- 南方区域 / 国网交易平台全是 SPA 空壳 + 登录；广州电力交易中心公告正文只给安排，数值结果一律「登录平台查看」。
- **结论**：出清价、出清量、实际负荷、出力、联络线、日清月结这些「交易事实类」数据，第一版**不能**依赖公开抓取。

**(c) 可用的宏观锚点（替代湖北细分的公开口径）**

- 全国全社会用电量月度（能源局，17 行 CSV）：1—9 月 80,734 亿千瓦时（+3.9%），9 月当月 8,997 亿千瓦时（+1.2%）。
- 全国电力统计（能源局）：截至 9 月底装机 41.3 亿千瓦（+11.2%），1—9 月发电设备平均利用 2,174 小时（同比 −194 小时）。
- 《2025 年度中国电力市场发展报告》（11 行关键指标 CSV）：2025 年市场化交易电量 6.64 万亿千瓦时（+7.4%，占全社会用电量 64.0%），跨省跨区 1.59 万亿千瓦时（+11.6%），省间现货 386 亿千瓦时。

---

## 5. 已知障碍清单与已测渠道

> 每条都写明「障碍现象 + 已查渠道 + 状态码」。**不重试、不绕过、不换 IP 规避。**

| # | 障碍 | 现象（2026-11-05） | 已查渠道 | 结论 / 绕行 |
| --- | --- | --- | --- | --- |
| O1 | 湖北省发改委 WAF | `fgw.hubei.gov.cn` 栏目与正文 **412**（拦截页 3,128–3,195 B），10 条 URL 全部被拦 | 直接 GET 栏目页、GET 正文页、GET robots.txt | 栏目不可读；**同域 PDF 直链 200** → 规则原文走直链 |
| O2 | 湖北省能源局域名不可达 | `nyj.hubei.gov.cn` **000 / NameResolutionError** | http 与 https 均测 | 不可达；湖北省级政策改用宜昌 / 黄石发改委转载页 + 华中监管局 |
| O3 | 交易平台 SPA + 需登录 | `ifdl` 2,248 B、`spot` 23,290 B、`sso` 56,906 B、`gp` 跳登录页、`pmos.*` 6,501 B、`jspec` 6,501 B | 直接 GET 首页 / 子路径 | 匿名不可得数值；第二版预留位 |
| O4 | 广东交易平台 412 | `pm.gd.csg.cn` **412**（2,418 B） | GET 首页 + robots.txt（robots 本身也 502） | 不可得；本票不依赖 |
| O5 | 国家统计局 403 | `data.stats.gov.cn/easyquery.htm` **403**（124.232.185.71）；年鉴入口仅 887 B frameset | GET 接口 + GET 年鉴入口 | 需 token；第一版用能源局口径 |
| O6 | 域名停售 | `www.bjpx.com.cn` DNS 通（43.160.235.235）但 ConnectTimeout | GET 首页 | 域名已停售；改用广州电力交易中心 |
| O7 | 省级政务站点本机不可达 | `energy.gd.gov.cn`、`sxnyj.shanxi.gov.cn`、`nyj.zj.gov.cn`、`nyj.gansu.gov.cn`、`nyj.hubei.gov.cn`、`data.gov.cn` 均 **000** | GET 首页（http + https） | 本机普遍不可达，**疑为网络环境问题而非站点下线**；需换网络环境复测 |
| O8 | 省级门户反爬 | `nyj.shandong.gov.cn` **403**（124.128.19.244） | GET 首页 + robots.txt（robots 也 403） | 需换网络环境 |
| O9 | 中电联禁止抓取 | `cec.org.cn` robots.txt = **`Disallow: /`** | GET robots.txt + GET 门户（200，22,336 B）+ GET 详情页（6,212 B，JS 渲染） | **不得抓取**；会员数据需付费；本轮未注册未付费 |
| O10 | 栏目已下线 / 404 | `zfxxgk.nea.gov.cn/1310741791/gzbg.htm` **404**；`gzpec.cn/law/ywzn/` **404**；`gzpec.cn/information/jyxx/`、`/jygg/` **404** | 直接 GET | 用已知 PDF 直链 / 替代栏目 |
| O11 | 空壳栏目页 | `tjj.hubei.gov.cn` 两个栏目页 **200 但仅 71 B**；`gzpec.cn/information/gsgg/` **200 但 172 B** | 直接 GET | JS 渲染，静态不可取 |
| O12 | 图片型 PDF | 国网湖北代理购电价表 20,111,105 B，`pymupdf` 抽出 170 字符 / 0 汉字 | PDF 直链 + pymupdf | 不可解析，需 OCR 或换版本 |
| O13 | JS 异步加载列表 | `gzpec.cn/law/jygz/`（46,442 B）、`/law/zcwj/`（43,578 B）静态 HTML 只有导航框架 | 直接 GET + web_fetch 交叉验证 | 需按 PDF 直链取；直链命名规律 `…/law/<栏目>/<年月>/P0202*.pdf` |
| O14 | 无 www 主机名 | `nea.gov.cn/...`（无 www）**000**；`www.nea.gov.cn/...` **200** | 两种主机名各测 | 一律用 `www.` 主机 |
| O15 | 第三方镜像可用但非权威 | `iesplaza`、`eesia`、`yuandian.space` 均 200 且可解析 | 直接 GET | 只作交叉校验，不作权威引用；必要时回官方源核对哈希 |
| O16 | 大文件 | gzpec 202611 PDF 42,008,576 B，被 40 MB 上限截断 | stream 下载 | 需要时提高上限重取，勿重复全量下载 |
| O17 | DNS 检查误报 | `fgw.huangshi.gov.cn`、`zfxxgk.ndrc.gov.cn` DNS 检查失败但 HTTP **200** | 两种方式对比 | **以 HTTP 状态码为准** |

---

## 6. robots.txt 与建议抓取频率

### 6.1 实测结果（45 个 host，2026-11-05）

| 站点 | robots 状态 | 判定 | 是否允许抓取 | 建议频率 |
| --- | --- | --- | --- | --- |
| `www.cec.org.cn` / `cec.org.cn` | 200 | **`Disallow: /`** | ❌ **禁止抓取** | 不得抓取；只能人工查阅 / 采购 |
| `www.gov.cn` | 200（811 B，PARTIAL） | `Allow: /1`；禁 `/2016gov/ /2016shuju/ /2016guoqing/ /2016zhengce/ /2016hudong/ /2016fuwu/ /2016xinwen/ /premier/ /2016guowuyuan/ /2016public/ /2016ducha/ /guowuyuan/yangjing/ /guowuyuan/yj.htm /c16629/gwyld_yj.htm /guowuyuan/yj_hy.htm` 等；声明 baidu / google / bing / sogou sitemap | ✅ 本票用到的 `/zhengce/zhengceku/` 与 `/gongbao/` **不在禁止清单** | 建议 ≥ 1 次 / 天，每次 ≤ 10 页 |
| `arxiv.org` | 200 | `User-agent: *`；`Crawl-delay: 15`；`Allow: /archive /year /list /abs /pdf /html /catchup`；禁 `/user /e-print /src /ps /dvi /cookies /form /find /view /ftp /refs /cits /format /PS_cache /Stats /seek-and-destroy /IgnoreMe /oai2 /auth /tb /tb-re` | ✅ `/abs` 允许 | **必须间隔 ≥ 15 s**；建议 ≤ 1 次 / 6 h |
| `www.xinhuanet.com` | 200 | `User-Agent: *` / `Allow: /` | ✅ 允许 | ≥ 1 次 / 天 |
| `www.yuandian.space` | 200 | `Allow: /`；禁 `/login/ /account/ /admin/ /ops/ /runs/share/`；另对 GPTBot / ClaudeBot / PerplexityBot / Google-Extended 允许 | ✅ 允许（避开禁止路径） | ≥ 1 次 / 天 |
| `www.gzpec.cn` | 200（35 B） | 只有 `User-agent: Baiduspider` / `Allow: /`，**无 `*` 组** | ✅ 无禁止项（按 robots 惯例） | 列表页 ≤ 1 次 / 天，分页 ≤ 3 页 / 次 |
| `www.ndrc.gov.cn`、`zfxxgk.ndrc.gov.cn`、`nyj.shandong.gov.cn` | **403** | robots 请求本身被拦 | ❓ 无法判定 | 不抓；等 WAF 放行或换环境 |
| `pm.gd.csg.cn` | **502** | WAF 拦截 robots | ❓ 无法判定 | 不抓 |
| `newpmos.hb.sgcc.com.cn`、`ifdl` / `spot` / `sso.poweremarket.com`、`pmos.sx` / `pmos.gs.sgcc.com.cn`、`www.jspec.com.cn`、`data.cma.cn`、`www.cma.gov.cn`、`www.nmc.cn`、`hzj.nea.gov.cn`、`fgw.hubei.gov.cn`、`tjj.hubei.gov.cn`、`www.stats.gov.cn`、`data.stats.gov.cn`、`drc.gd.gov.cn`、`fzggw.zj.gov.cn`、`fgw.yichang.gov.cn`、`fgw.huangshi.gov.cn`、`www.hbqj.gov.cn`、`cdn.iesplaza.com`、`eesia.cn` | 404 / 自定义错误页 / 直接回首页 | **无 robots.txt 声明** | ⚠️ 无声明不等于允许；按最保守处理 | ≥ 1 次 / 天，单次 ≤ 10 页 |
| `www.hb.sgcc.com.cn`、`pmos.sgcc.com.cn`、`pmos.sd.sgcc.com.cn`、`www.bjpx.com.cn`、`energy.gd.gov.cn`、`sxnyj.shanxi.gov.cn`、`nyj.zj.gov.cn`、`nyj.gansu.gov.cn`、`nyj.hubei.gov.cn`、`data.gov.cn` | 连接失败（code 0） | 未取得 | ❓ 无法判定 | 不抓 |

### 6.2 建议抓取频率（第一版默认）

1. **总量原则**：外部通道只取「低频、单次」。天气 API 可日更或按需调用；规则文本类（ndrc / nea / gov.cn / gzpec / hzj）按「版本变更才更新」——建议按发布年月做增量，**同一文档不再重复下载**。
2. **硬下限**：同站请求间隔 ≥ 1 s；arxiv ≥ 15 s（robots 规定）；单次会话对同站请求 ≤ 20。
3. **每日上限**：gzpec 列表页 ≤ 1 次 / 天、分页 ≤ 3 页 / 次；gov.cn 政策库 ≤ 10 页 / 天；nea / ndrc ≤ 10 页 / 天。
4. **缓存优先**：所有下载按 `URL → SHA256 前 16 位` 建索引，命中缓存直接复用，避免重复抓取（本轮已对 18 个二进制源记录哈希）。
5. **失败不重试**：412 / 403 / 429 一律记录并跳过，**不换 UA / 不换 IP / 不加验证码绕过**；同一 URL 每 7 天最多重试 1 次。
6. **明确禁抓**：`cec.org.cn`（`Disallow: /`）以及 robots 不可判定的站点（WAF 拦截），一律不抓。
7. **署名与许可**：gov.cn、ndrc、nea 公开文件可用于内部系统，但对外分发前需保留来源标注；Open-Meteo 为 CC-BY 4.0（需署名）；第三方镜像（iesplaza / eesia / yuandian.space）只作内部交叉校验，不复制入库、不对外引用。

---

## 7. 对 #24（数据导入器）与 #44（三阶段接入口径）的影响条目

### 7.1 对 #24（数据导入器）

| # | 影响条目 | 依据 |
| --- | --- | --- |
| I1 | **导入器需同时支持「外部公开源」与「公司历史文件」两条通道**，但两者形态不同：外部源是 JSON / CSV（结构固定、字段少），公司文件是 Excel / PDF / 标准模板（字段多、格式杂）。建议导入器抽象为「规范化适配层」：外部源走 API/CSV 适配器，公司文件走文件解析适配器 | §3.1 / §3.2 / §3.3 |
| I2 | **公司侧「负荷、交易、出清」主数据第一版只能来自文件导入**——公开渠道实测全部拿不到（湖北 412 / 能源局 DNS 不通 / 交易平台需账号） | §1.2、§3.3.2 |
| I3 | **天气与节假日应设计为「可选外部特征表」**，粒度先做日级；小时级未实测，需补测后再决定是否入库 | §4.1 |
| I4 | **导入器要能吃「披露科目字典」**：`samples/披露科目结构/` 两个 CSV 已把 9 号文附表（130 行）与广西披露细则附表 1（128 行）结构化，可直接作为字段映射表的骨架 | §4.3(a) |
| I5 | **解析器必须内建三类特例**：① 图片型 PDF（需 OCR 或标记为不可解析）；② 老式 `.doc`（需 OLE piece table 解析，本轮已验证可行）；③ JS 渲染页（须判定为空壳并记录，不做无意义的重试） | §2.4、§5 O11–O13 |
| I6 | **HTML 解析器统一用 `html.parser`**（`lxml` 会丢 `gov.cn` 正文），并配「正文长度 < 200 字且 0 汉字 → 判为不可解析」的自动断言 | §2.4 |
| I7 | **导入管道要留「附件直链」通道**：湖北发改委 / 湖北交易平台的栏目页被拦，但同域 PDF 直链可取——导入器应支持「从已验证直链清单取附件」，而不是「爬栏目页找链接」 | §5 O1、§3.3.1 |
| I8 | **每个入库对象都要带来源三元组**：`source_url` + `http_status` + `fetched_at`（本轮全部样本已按此格式带 YAML frontmatter） | §3.3.1 |
| I9 | **必须支持「原始件不入库」策略**：二进制只留 URL + 字节数 + SHA256 前 16 位，避免仓库膨胀 | §2.6、§3.4 |

### 7.2 对 #44（三阶段接入口径）

| # | 影响条目 | 依据 |
| --- | --- | --- |
| J1 | **第一阶段（公开信息）能覆盖的是「规则 / 文本 / 宏观统计」**：政策原文、细则附表、全国用电量与市场发展报告——这些是问答与口径解释的依据，**不是交易事实** | §4.3(a)(c) |
| J2 | **第二、三阶段（交易事实与策略）必须依赖公司自有历史数据（文件导入）+ 账号态披露数据**，公开渠道在这两阶段**没有替代品** | §1.2、§3.3.2 |
| J3 | **外部特征（天气 / 节假日）的接入位置**：适合放在「负荷预测解释」「需求响应触发」「价格合理性归因」三类环节；交易日历用于「交易时点校验 / 结算日推算」 | §4.1、§4.2 |
| J4 | **公开渠道的可验证性边界要写进口径文档**：任何对外表述里出现的数字，必须能指回 `source_url + http_status + fetched_at`，否则不得入模型上下文 | §3.4、§6.2 |
| J5 | **规则文本的可引用切分**：已结构化的披露科目（6.22 / 6.47 / 5.4 / 2.7 等）可直接作为「审计点 → 规则依据」的映射表 | §4.3(a) |
| J6 | **需账号 / 需付费源必须显式标注为「预留位」**：南方区域统一电力交易平台（ifdl / spot / sso / gp）、国网 pmos 系列、`data.stats.gov.cn`（token）、`data.cma.cn`（注册）、中电联（付费）——写进接口契约但不实现匿名调用 | §3.2、§3.3.2 |
| J7 | **注意「HBJ」命名陷阱**：`hbj.nea.gov.cn` 是**华北**能源监管局（京/津/冀/晋/蒙），不是湖北；湖北相关监管信息在 `hzj.nea.gov.cn`（华中监管局）。规则库入库时须按站点实体而非域名字符串归类 | §3.3.1 |
| J8 | **抓取失败要能优雅降级**：本轮 106 条里有 10 条 000 / 10 条 412 / 2 条 403，任一外部源失败不应阻塞主流程 | §5 |

---

## 8. 未解决事项

> 每条按「未查到（已查：××渠道）」格式记录。所有条目访问日期均为 2026-11-05。

| # | 事项 | 状态 |
| --- | --- | --- |
| N1 | **湖北逐日出清价 / 出清量 / 各类电源出清电量（附表 6.47 / 6.52）** | 未查到（已查：`https://fgw.hubei.gov.cn/fgjj/sjsfg/sjfx/lx/nyjs/` 栏目 412、`https://newpmos.hb.sgcc.com.cn/` 首页 412、`https://spot.poweremarket.com/uptspot/sr/pt/` SPA 空壳、`https://ifdl.poweremarket.com/` SPA 空壳、`http://www.gzpec.cn/information/jyxx/` 404）。匿名渠道无解，需账号态或换网络环境 |
| N2 | **湖北实际负荷（附表 6.56）** | 未查到（已查：湖北发改委发用电栏目 412、湖北省能源局 `nyj.hubei.gov.cn` DNS 不通、`https://tjj.hubei.gov.cn/tjsj/tjgb/ndtjgb/qstjgb/202603/t20260318_5894639.shtml` 200 但只有综合 / 农业 / 工业口径无用电量分项） |
| N3 | **湖北新能源实际 / 预测出力（附表 6.39 / 6.54）** | 未查到（已查：湖北发改委 412、湖北省能源局 DNS 不通、南方区域披露平台 SPA 需账号）。**仅取得披露口径文本**（广西披露细则附表 1 的 6.39 / 6.54 条，已结构化入样本 CSV） |
| N4 | **省间联络线输电情况（附表 6.50 / 6.60）** | 未查到数值（已查：同 N1 全部渠道）。已取得披露口径文本（6.50 省间交易计划月披露、6.60 省间联络线输电情况日披露） |
| N5 | **湖北月度全社会用电量（附表 5.7）** | 未查到湖北口径（已查：`https://fgw.hubei.gov.cn/fgjj/sjsfg/sjfx/lx/nyjs/` 412、`https://tjj.hubei.gov.cn/tjsj/tjgb/ndtjgb/qstjgb/202603/t20260318_5894639.shtml` 无该口径、`https://data.stats.gov.cn/easyquery.htm` 403）。**只有全国口径可用**（能源局 1—9 月 80,734 亿千瓦时，已存样本 CSV） |
| N6 | **逐日交易日历（附表 6.22 的「日」粒度）** | 未查到（已查：`https://ifdl.poweremarket.com/supt/sr/dmz/mm/info/infoDisclosureAppMain.html?exchangeCode=01` SPA 空壳需登录、`http://www.gzpec.cn/information/zxtz/` 公告只给年度 / 月度交易安排预安排、`http://www.gzpec.cn/law/jygz/` 规则列表 JS 异步加载）。**已有替代**：法定节假日 365 行标记表 + 13 条交易安排日程（性质不同，不能等同逐日交易日历） |
| N7 | **各省分时电价峰谷尖峰深谷目录（逐省全量）** | 只拿到湖北一份（已查：`http://fgw.yichang.gov.cn/content-63074-983392-1.html` 200 取得鄂发改价管〔2024〕77 号全文含时段表）。**其余省份未逐省取得**（已查：国家层 1093 号文只给原则不给各省时段表；`energy.gd.gov.cn`、`nyj.zj.gov.cn`、`nyj.gansu.gov.cn`、`nyj.shandong.gov.cn`、`nyj.hubei.gov.cn` 分别为 000 / 000 / 000 / 403 / DNS 不通） |
| N8 | **湖北代理购电套餐与零售价公告（数值）** | 未查到数值（已查：`https://fgw.hubei.gov.cn/.../t20220519_4133488.shtml` 412、`https://newpmos.hb.sgcc.com.cn/index` 412、`http://www.hb.sgcc.com.cn/html/files/2022-07/29/20220729154457454933047.pdf` 200 但为图片型 PDF 抽出 0 汉字）。**已取得**：湖北零售市场实施细则规则文本（`https://newpmos.hb.sgcc.com.cn/px-settlement-infpubmeex/fileService/download?fileId=n474b336b154747969a7c7207d22f4387`，零售套餐定义 / 下单与邀约两种方式 / 最小签约周期自然月）+ 国家层 809 号、1047 号代理购电机制全文 |
| N9 | **日清月结公告与结算数值** | 未查到（已查：`http://www.gzpec.cn/information/jyxx/`、`/information/jygg/` 404；gzpec 公告正文明写「详细交易结果请相关经营主体登录南方区域统一电力交易平台查看」；`https://sso.poweremarket.com/` 登录页 200；`https://gp.poweremarket.com/` 跳登录页） |
| N10 | **现货市场实施细则与信息披露实施细则原文（湖北 / 南方区域口径）** | 部分取得。已取得：南方区域现货实施细则（2026 V1.0，PDF 直链 200，2,171,367 B，正文 136,399 字符）、广西信息披露细则（2026 V1.0，附表 1 全 128 行已结构化）、广西现货结算细则（2026 V2.0）、南方区域市场注册实施细则（2026 V1.0）、湖北中长期市场实施细则（2026-04 正式稿，来自华中监管局 PDF 直链）。**未取得**：湖北本省现货市场实施细则正式全文——`https://fgw.hubei.gov.cn/fbjd/zc/zcwj/tz/202506/t20250613_5691029.shtml` 412；只拿到镜像件 `http://cdn.iesplaza.com/uploads/files/20241115/1731634839759868.pdf`（征求意见稿 V3.0，非权威、非正式稿）与 `https://www.yuandian.space/` 镜像站首页 |
| N11 | **负荷管理办法、分时电价等政策原文（国家层）** | 已取得（`https://www.gov.cn/zhengce/zhengceku/202310/content_6907504.htm`、`…/content_6907311.htm`、`https://www.gov.cn/zhengce/zhengceku/2021-07/29/content_5628297.htm` 等全部 200 且正文可解析，样本见 `samples/政策原文/`）。**残留**：湖北实施细则原文 412 未取得（见 §3.3.2） |
| N12 | **湖北省能源局站点是否真的下线** | 未查到（已查：`http://nyj.hubei.gov.cn/` 与 `https://nyj.hubei.gov.cn/` 均 NameResolutionError；`https://fgw.hubei.gov.cn/fbjd/xxgkml/jgzn/nsjg/` 412 无法交叉验证）。DNS 失败可能是本机网络环境所致，**需换网络环境复测**才能判定 |
| N13 | **小时级 / 分钟级气象数据** | 未测（已查：本轮只验证了 open-meteo 与 wttr.in 的日级字段 `daily`）。open-meteo 支持 `hourly` 参数，但本轮未请求、未验证 |
| N14 | **gzpec 202611 超大 PDF（42,008,576 B）完整内容** | 未取完（已查：受 40 MB 单文件上限截断，实际文件更大）。如需该件须提高上限重取一次 |
| N15 | **arXiv `2302.12168` 条目** | 未取到（已查：`https://arxiv.org/abs/2302.12168` 本次连接异常 EXC；同站 `2408.05628`、`1810.08418` 均 200）。按 robots 需间隔 ≥ 15 s 重试一次 |
| N16 | **交易平台「需账号」路径的具体可得字段** | 未查（本轮按硬约束未注册、未登录、未索要凭据）。第二版拿到账号后需重新实测：披露平台实际暴露哪些 6.x 科目、频率与粒度如何、是否可导出 |

---

## 9. 样本文件索引

> 全部位于 `docs/research/samples/`，只含文本 / CSV / JSON / TSV / MD；**PDF / DOC 二进制未入库**（URL、字节数、SHA256 前 16 位见 §3.4）。

| 目录 | 文件 | 说明 |
| --- | --- | --- |
| `可达性实测/` | `扫描结果总表.tsv`（106 行）、`补充URL扫描结果.tsv`（9 行）、`robots检查结果.tsv`（45 行）、`分类编码说明.md` | 本轮原始实测记录与编码说明 |
| `天气数据/` | `open-meteo-forecast.json`、`open-meteo-archive.json`、`wttr-in-plain.json`、`武汉逐日气温与降水_样本.csv`（14 行）、`可达性实测.json` | 通道① 天气 |
| `交易日历/` | `2026年法定节假日安排.csv`（7 行）、`2026年逐日交易日标记表.csv`（365 行）、`timor-holiday-2026.json`、`timor-holiday-2025.json`、`节假日日历API可达性.json`、`南方区域公开交易安排日程_2025-2027.csv`（13 行） | 通道② 日历 |
| `市场公开信息/` | `gzpec公告栏目可达性.json`、`gzpec通知公告正文可达性.json`、`gzpec公告正文样本.json`、`gzpec2026公告与附件可达性.json`、`gzpec公告分页扫描.json`、`交易日历与公告栏目可达性.json`、`公告分页与日历栏目可达性.json`、`交易平台域名可达性补充.json`、`华中监管局可达性与湖北条目.json` | 通道③ 平台与公告探测原始记录 |
| `披露科目结构/` | `国家9号文_信息披露内容附表_结构化.csv`（130 行）、`广西披露细则2026V1_附表1_信息披露内容.csv`（128 行） | 披露科目字典（6.22 / 6.39 / 6.47 / 6.49 / 6.54 / 6.56 / 6.60 等） |
| `规则节选/` | `南方区域现货实施细则_出清结果条款节选.md`（13,557 B）、`湖北零售市场实施细则_零售套餐条款节选.md`（3,477 B） | 规则原文节选 |
| `政策原文/` | 8 份 Markdown（1093 号、809 号、1047 号、394 号、1595 号、负荷管理办法、需求侧管理办法、鄂发改价管〔2024〕77 号） | 每份带 YAML frontmatter：`title / source_url / source_id / http_status / fetched: 2026-11-05 / format / license_note` |
| `统计与用电量/` | `全国全社会用电量_2026年1-9月.csv`（17 行）、`全国电力统计_2026年1-9月.csv`（4 行）、`2025年度中国电力市场发展报告_关键指标.csv`（11 行） | 宏观锚点 |
| `分时电价/` | `湖北工商业分时电价时段与浮动比例.csv`（1,231 B） | 峰 / 高 / 平 / 谷 / 尖峰时段与比例 |

---

**收尾说明**：本稿所有结论均来自 2026-11-05 的单机单次实测；未登录、未注册、未付费、未索要凭据、未绕过任何验证码或访问控制。所有 URL 与状态码均可按 §9 的样本文件复核。
