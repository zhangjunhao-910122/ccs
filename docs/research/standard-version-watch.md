# 三体系标准版本现状与换版时间线（QMS / EMS / OHSMS）

> 研究日期：2026-11-05。本文所有「访问日期」均为 **2026-11-05**（除另行标注）。
> 范围：只记录**版本状态、换版时间线、条款号与标题层级变化**；不复制标准正文（ISO/GB 版权）。
> 状态标签：**一手已确认** / **二手标注** / **未找到**。

## 0. 方法、证据等级与抓取限制

**标签定义**

- **一手已确认**：取自标准发布方或主管机构官方网站页面、官方文件原文（ISO 官网页面快照、ISO 出版物本体预览件、CNIS 官方文件、std.samr.gov.cn、openstd.samr.gov.cn）。
- **二手标注**：来自转载、检索快照或分销渠道的说明性文字，文中注明来源名称与 URL。
- **未找到**：已尝试查证但无法取得一手证据；下文同时列出已查过的源。

**抓取限制（直接影响证据等级，务必知悉）**

1. `www.iso.org/standard/<编号>.html` 在本机被 Cloudflare 拦截（直连、web_fetch、多个公共代理均返回 403 或「请稍候…」挑战页）。因此 ISO **数字目录号**一类的字段多以「未找到」收口。
2. `web.archive.org`（Wayback）本机连接超时，无法用存档页补证。
3. GB/T 19001 修订计划的「征求意见稿」附件 PDF 在 std.samr.gov.cn、cnis.ac.cn、www.cnis.ac.cn、www.sac.gov.cn、openstd.samr.gov.cn 等 6 个主机均返回 404，未取得征求意见稿正文。
4. ISO 9001:2026 正文通过 ISO 成员机构 SIST 授权分销渠道 iTeh Standards 提供的官方预览件（「Sample Document」）取得；预览件含完整目录页与前言，故**条款号与标题可直接采信为 ISO 本体信息**，但要求条文本身不在预览范围内。

### 0.1 结论速览（8 条事实 + 映射表）

| # | 事实 | 结论 | 证据（URL，访问日期 2026-11-05） | 标签 |
| --- | --- | --- | --- | --- |
| 1 | ISO 9001:2015 是否仍现行 | **不再现行**，已于 2026-09-16 撤回，被 ISO 9001:2026 取代 | https://www.iso.org/standard/88431.html ；https://www.cnis.ac.cn/gjbzh/gjdt/202609/t20260917_63681.html | 一手已确认 |
| 2 | ISO 9001:2015/Amd 1:2024 状态 | **已撤回**（Withdrawn，阶段 95.99，2026-09-16）；内容并入 ISO 9001:2026 | https://www.iso.org/standard/88431.html ；ISO 9001:2026 出版物前言 | 一手已确认 |
| 3 | ISO 9001:2026 是否已发布 | **已发布**：2026-09-16，第六版（出版月 2026-09），取代 2015 版；官方参考号 ISO 9001:2026，**数字目录号 88464 未找到**；**无「实施日期」字段**，过渡安排见 ISO/TC 176 指引（2027-03-31 / 2027-06-30 / 2027-09-30 / 2028-03-31 / 2029-09-30） | https://committee.iso.org/sites/tc176/home/news/content-left-area/news-and-updates/news.html ；https://committee.iso.org/home/tc176sc2 ；https://cdn.standards.iteh.ai/sist-preview/iso/iso-9001-2026/f019c820593640a0a84140a0acb8be6d/iso-9001-2026.pdf | 一手已确认（发布/过渡）＋未找到（数字目录号、实施日期字段） |
| 4 | GB/T 19001-2016 状态与修订计划 | **现行**；修订计划 **20264412-T-469**，SAC/TC 151 归口，等同采用 ISO 9001:2026，代替 GB/T 19001-2016，**正在征求意见**，**2027-02-28 前报批**；**「征求意见截止 2026-10-21」未找到** | https://std.samr.gov.cn/gb/search/gbDetailed?id=5A7BF1C508FA8432E06397BE0A0A8F1E ；https://www.cnis.ac.cn/bydt/bzyjzq/gbyjzq/202609/P020260922713471823744.pdf ；https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno=8211B23BE58F6076946628F7E83CB1BC | 一手已确认（现行/计划）＋未找到（征求意见截止日） |
| 5 | ISO 14001:2015 与 ISO 14001:2026 | **ISO 14001:2015 已于 2026-04-15 撤回（Stage 95.99）**，被 **ISO 14001:2026** 取代；后者已发布：第四版，出版月 2026-04，参考号 ISO 14001:2026，目录号 92300 | https://www.iso.org/standard/60857.html ；https://www.iso.org/standard/14001 | 一手已确认 |
| 6 | GB/T 24001-2016 状态与修订计划 | **现行**；修订计划 **20261483-T-469**（周期 12 个月，2026-03-31 下达，TC207/SC 1 归口），等同采用 ISO 14001:2026，代替 GB/T 24001-2016，**正在征求意见** | https://std.samr.gov.cn/gb/search/gbDetailed?id=4E644F8290F2704CE06397BE0A0A146E ；https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno=61011DD84D5D3AB7F5BCFEA911460D3D | 一手已确认 |
| 7 | ISO 45001:2018 状态与修订在途 | **现行**（2024 年复审确认）；修订在途：**ISO/DIS 45001**（Draft）预计取代，计划以 **ISO 45001:2027** 发布 | https://www.iso.org/standard/63787.html | 一手已确认 |
| 8 | GB/T 45001-2020 状态与修订计划 | **现行**（2020-03-06 发布并实施，等同采用 ISO 45001:2018）；**未检索到修订计划** | https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno=A509BECF074159862C3E3E2DEC2487E6 ；https://std.samr.gov.cn/gb/search/gbDetailed?id=71F772D7E326D3A7E05397BE0A0AB82A | 一手已确认（现行）＋未找到（修订计划） |
| 9 | ISO 9001:2015 → 2026 条款号映射表 | **已完成**（以 SS ISO 9001:2015 与 ISO 9001:2026 两份官方目录页逐条比对）。两版骨架高度一致：结构差异仅 0.3.3、5.2.x、6.1.x、9.2.x、10.1/10.3 重编号、附录 B 删除、第 3 章术语编号化；4.4、6.2、7.5.3、8.1、8.2.3、10.2 两版均无编号子条款 | 见 §5 | 一手已确认 |

## 1. 质量管理体系（QMS）

### 1.1 ISO 9001:2015 —— 已不再是现行版本

- **结论：ISO 9001:2015 已于 2026-09-16 撤回（不再是现行版本），被 ISO 9001:2026 取代。［一手已确认］**
- 证据 1：ISO 官方条目页「ISO 9001:2015/Amd 1:2024」（参考号 ISO 9001:2015/Amd 1:2024，编号 88431，Edition 5，2024-02）显示状态 **Withdrawn**、阶段 **95.99**，life cycle 记录 **95.99 Withdrawal of International Standard, 2026-09-16**，并标注 **Revised by: Published ISO 9001:2026**。URL: https://www.iso.org/standard/88431.html （访问日期 2026-11-05；页面快照缓存 .research-tmp/iso_9001_amd1_2024_page_flat.txt）
- 证据 2：ISO 9001:2026 出版物本体前言原文：**「This sixth edition cancels and replaces the fifth edition (ISO 9001:2015), which has been technically revised. It also incorporates the Amendment ISO 9001:2015/Amd 1:2024.」**（来源见 §1.3）
- 证据 3：CNIS 新闻（2026-09-17）：「2026年9月16日，国际标准化组织（ISO）正式发布ISO 9001:2026《质量管理体系 要求》，替代ISO 9001:2015」。URL: https://www.cnis.ac.cn/gjbzh/gjdt/202609/t20260917_63681.html （访问日期 2026-11-05）
- 证据 4：ISO/TC 176 官方新闻（2026-09-23，作者 Mark Finewood）：「ISO 9001:2026 was published on 16 September 2026!!」。URL: https://committee.iso.org/sites/tc176/home/news/content-left-area/news-and-updates/news.html （访问日期 2026-11-05）
- 限定说明：ISO 9001:2015 本体条目页（https://www.iso.org/standard/62085.html ）本机不可达（403），2015 版本体条目的「状态」字段未直接读取；上述结论由其修正案条目页的 life cycle、ISO 出版物前言与 CNIS 官方报道共同支撑。

### 1.2 ISO 9001:2015/Amd 1:2024 —— 已撤回

- **结论：Amd 1:2024（气候行动修正案）状态为 Withdrawn，阶段 95.99，撤回日 2026-09-16；其内容已被 ISO 9001:2026 吸收（2026 版前言明确 writes「It also incorporates the Amendment ISO 9001:2015/Amd 1:2024」）。［一手已确认］**
- URL: https://www.iso.org/standard/88431.html （访问日期 2026-11-05）；同一事实另见 ISO 9001:2026 预览件前言（§1.3）。

### 1.3 ISO 9001:2026 —— 已发布

- **结论：ISO 9001:2026 已于 2026-09-16 发布，为第六版（Sixth edition，出版月份 2026-09），取代 ISO 9001:2015。［一手已确认］**
- 官方参考号：**ISO 9001:2026**（出版物封面标注「Reference number ISO 9001:2026(en) © ISO 2026」）；**纯数字目录号 88464 未找到一手证据 →［未找到］**（iso.org 编号页 403，详见 §7）。
- 出版物本体（预览件）：https://cdn.standards.iteh.ai/sist-preview/iso/iso-9001-2026/f019c820593640a0a84140a0acb8be6d/iso-9001-2026.pdf （访问日期 2026-11-05，经 ISO 成员机构 SIST 授权分销渠道 iTeh Standards 提供；封面标注「Sixth edition 2026-09」，页脚指向 SIST 官方电商 https://ecommerce.sist.si/catalog/standards/iso/2aa96449-a8f0-4e8a-b2dd-09e30a94b05f/iso-9001-2026 ）
- 委员会侧佐证：ISO/TC 176/SC 2 页面「Our Standards」首项即「ISO 9001: 2026 — Quality management systems - Requirements」。URL: https://committee.iso.org/home/tc176sc2 （访问日期 2026-11-05）
- 二手标注：ISO 官网条目页标题为「ISO 9001:2026 - Quality management systems — Requirements」（https://www.iso.org/standard/9001 ，检索快照，2026-11-05）；ISO 洞察页摘要提到对领导力、质量文化与道德行为的更强要求（https://www.iso.org/quality-management/iso-9001-2026 ，检索快照，2026-11-05）。
- 实施日期：ISO 标准本身不设「实施日期」字段；与落地实施相关的官方时间安排来自 ISO/TC 176 过渡指引（下表）。［「标准实施日期」未找到］
- 2026 版前言列出的主要变化（仅记录条目名，不复制条文）：① 第 3 章纳入核心管理体系术语与定义（ISO 9000 仍为全部质量管理术语与定义的规范性引用文件）；② 引入质量文化与道德行为要求（涉及领导作用、意识、过程运行环境）；③ 风险与机遇分离处理；④ 强化体系变更管理；⑤ 附录 A 扩充为解释性内容且不引入新要求；⑥ 删除附录 B（原附录 B 提供 ISO/TC 176 其他标准的索引信息）。［一手已确认，来源同预览件前言］

#### ISO/TC 176 官方过渡指引（公告日 2026-09-23，原文要点）

| 时间节点 | 内容 |
| --- | --- |
| 2027-03-31 | 认证机构须具备评估 ISO 9001:2026 的能力 |
| 2027-06-30 | 获认可认证机构须提交过渡声明及支撑材料 |
| 2027-09-30 | 认证机构完成过渡决定 |
| 2028-03-31 | 起仅可向 ISO 9001:2026 签发新证书与初次认证 |
| 2029-09-30 | 已按 ISO 9001:2015 认证的组织完成过渡的截止日（可在监督审核/再认证审核中或通过单独的过渡审核完成） |

URL: https://committee.iso.org/sites/tc176/home/news/content-left-area/news-and-updates/news.html （访问日期 2026-11-05；［一手已确认］）

## 2. GB/T 19001-2016 与修订国家标准计划

### 2.1 现行状态

- **结论：GB/T 19001-2016 仍为现行标准（尚未被替代）。［一手已确认］**
- 国家标准全文公开系统条目页：状态「现行」，发布 2016-12-30，实施 2017-07-01，类别「推标」，采标。URL: https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno=8211B23BE58F6076946628F7E83CB1BC （访问日期 2026-11-05）
- 全国标准信息公共服务平台条目页：状态现行；发布 2016-12-30、实施 2017-07-01；上次复审 2023-12-28 结论「继续有效」；代替 GB/T 19001-2008；采标 ISO 9001:2015；页面显示「修订计划 20264412-T-469 正在征求意见」。URL: https://std.samr.gov.cn/gb/search/gbDetailed?id=71F772D814AED3A7E05397BE0A0AB82A （访问日期 2026-11-05）
- 同一检索结果中 GB/T 19001-2008 状态为「废止」。URL: https://openstd.samr.gov.cn/bzgk/std/std_list?r=0.87654321&page=1&pageSize=50&p.p1=0&p.p2=19001&p.p90=circulation_date&p.p91=desc （访问日期 2026-11-05）

### 2.2 修订国家标准计划 20264412-T-469

- **结论：GB/T 19001 修订计划号 20264412-T-469；归口/执行 SAC/TC 151（全国质量管理和质量保证标准化技术委员会）；等同采用 ISO 9001:2026；代替 GB/T 19001-2016（全部代替）；当前阶段「正在征求意见」；计划 2027-02-28 前完成报批。［一手已确认］**
- 计划详情页字段：计划号 20264412-T-469；计划类型 修订；项目周期 6 个月；下达日期 2026-08-28；归口/执行单位 SAC/TC 151；主管部门 国家标准委；采标关系「本标准等同采用ISO国际标准：ISO 9001:2026」；代替标准 GB/T 19001-2016（全部代替）；计划状态「正在征求意见」（STAGE_CODE 30.20）；附件栏提供「征求意见稿.pdf」「编制说明.pdf」。URL: https://std.samr.gov.cn/gb/search/gbDetailed?id=5A7BF1C508FA8432E06397BE0A0A8F1E （访问日期 2026-11-05）
- CNIS《GB/T 19001 国家标准编制说明》原文要点：「根据《国家标准委关于下达 2026 年第八批推荐性国家标准计划及相关标准外文版计划的通知》，由全国质量管理和质量保证标准化技术委员会（SAC/TC 151）提出并归口的推荐性国家标准《质量管理体系 要求》修订项目正式下达，计划号为 20264412-T-469，项目周期为 6 个月，计划于 2027 年 2 月 28 日前完成报批。」「2026 年 9 月 16 日，ISO 9001:2026 正式发布，为保持我国国家标准与国际标准同步，有必要及时修订 GB/T 19001…」。URL: https://www.cnis.ac.cn/bydt/bzyjzq/gbyjzq/202609/P020260922713471823744.pdf （访问日期 2026-11-05）
- 起草过程（编制说明）：2026-05-31 SAC/TC 151 提出修订立项申请 → 2026-07 成立标准起草组与顾问组 → 7-21 预研研讨会（讨论 ISO 9001:2026 与 ISO 9001:2015 差异）→ 8-13 至 8-14 第一次研讨会（对 ISO/FDIS 9001:2026 逐章逐条研究）→ 9-10 至 9-11 修订启动会暨第二次研讨会 → 形成征求意见稿。
- 编制原则（编制说明）：一致性（等同采用 ISO 9001:2026）、继承性（2016 版未变表述沿用）、协调性（与 GB/T 19000 族协调）、规范性（按 GB/T 1.1—2020、GB/T 1.2—2020）。
- 新国标结构（编制说明）：前言、引言、范围、规范性引用文件、术语和定义、组织环境、领导作用、策划、支持、运行、绩效评价、改进、附录 A（结构、术语和条款说明）、参考文献；**删除原附录 B**。
- 主要技术变化（编制说明，相对 GB/T 19001—2016）：增加 23 个管理体系标准核心术语和定义；增加质量文化与道德行为相关要求（领导作用、人员意识、过程运行环境）；拆分风险与机遇表述并分别考虑应对措施；强化与变更相关的要求；扩充附录 A 解释性内容、删除原有附录 B。
- 过渡期（编制说明第九条）：「建议在标准发布后设置 6 个月过渡期，同时做好标准的宣贯培训和技术咨询工作，引导各类组织平稳实施标准转换。具体过渡期和实施日期以国家标准化管理委员会发布公告为准。」
- **「征求意见截止 2026-10-21」：［未找到］**。已查源：std.samr.gov.cn 计划详情页（全文检索「截止」「2026-10」「10-21」「报批」「2027-02」均无命中）、CNIS 编制说明全文（无该日期）、CNIS 国际动态栏目（未检索到 GB/T 19001 征求意见公告）。注意：计划页附件「征求意见稿.pdf」在 6 个主机均 404，未能由稿件正文反证截止日期。
- 线索中的「ISO 9001:2026 标准号 88464」同属 §1.3 的未找到项，不作为结论使用。

## 3. 环境管理体系（EMS）

### 3.1 ISO 14001:2015 —— 已撤回

- **结论：ISO 14001:2015 已于 2026-04-15 撤回（Stage 95.99），「2026-04-15 撤回」一说属实。［一手已确认］**
- ISO 条目页字段：参考号 ISO 14001:2015，编号 60857，Edition 3，2015-09；状态 **Withdrawn**，阶段 **95.99**；life cycle：90.93（2025-06-18 confirmed）→ 90.92（to be revised）→ **95.99 Withdrawal of International Standard, 2026-04-15**；Amendments 栏「Withdrawn ISO 14001:2015/Amd 1:2024」；**Revised by: Published ISO 14001:2026**；TC ISO/TC 207/SC 1。URL: https://www.iso.org/standard/60857.html （访问日期 2026-11-05）
- 修正案条目：ISO 14001:2015/Amd 1:2024，编号 88209，Edition 3，2024-02，状态 Withdrawn，阶段 95.99，撤回日 2026-04-15，被 ISO 14001:2026 取代。URL: https://www.iso.org/standard/88209.html （访问日期 2026-11-05）

### 3.2 ISO 14001:2026 —— 已发布

- **结论：ISO 14001:2026 已发布，为第四版，出版月份 2026-04，ISO 官方参考号 ISO 14001:2026，目录号 92300，状态 Published。［一手已确认］**
- ISO 官方主题页字段：Reference number ISO 14001:2026；Edition 4；2026-04；92300；Published (Edition 4, 2026)；价格 CHF 196；TC ISO/TC 207/SC 1；FAQ 段「What does the publication of the new edition in 2026 mean for organizations certified to ISO 14001:2015?」并指向认证机构咨询过渡安排。URL: https://www.iso.org/standard/14001 （访问日期 2026-11-05）
- 二手标注：ISO 新闻页《ISO 14001:2026 published》地址为 https://www.iso.org/news/2026/04/iso-14001-2026-published （本机 403，仅由转载方确认标题与发布月份 2026-04）；中文转载：标准信息服务网《ISO 14001:2026发布——全面拔高环境绩效标杆》https://zwpt.sacinfo.org.cn/cnf/html/ISO/111532.jhtml （发布时间 2026-09-16，文中来源标注为上述 ISO 新闻页；访问日期 2026-11-05）。
- ISO 14001:2026 的过渡期具体日期：［未找到］（ISO 页面仅指向认证机构，未给出日期；本轮未取得 ISO 14001 专属过渡公告）。

### 3.3 GB/T 24001-2016 与修订计划

- **结论：GB/T 24001-2016 现行；已有修订计划 20261483-T-469（等同采用 ISO 14001:2026），当前阶段「正在征求意见」。［一手已确认］**
- 国家标准全文公开系统条目页：状态「现行」，发布 2016-10-13，实施 2017-05-01，采标。URL: https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno=61011DD84D5D3AB7F5BCFEA911460D3D （访问日期 2026-11-05）
- 全国标准信息公共服务平台条目页：现行；上次复审 2023-12-28「继续有效」。URL: https://std.samr.gov.cn/gb/search/gbDetailed?id=71F772D81112D3A7E05397BE0A0AB82A （访问日期 2026-11-05）
- 修订计划页字段：计划号 **20261483-T-469**；类型 修订；项目周期 **12 个月**；下达日期 **2026-03-31**；归口 TC207、执行 TC207/SC 1；主管部门 国家标准委；采标「本标准等同采用ISO国际标准：ISO 14001:2026」；代替 GB/T 24001-2016（全部代替）；计划状态 **正在征求意见**（STAGE_CODE 30.20）；附件栏含征求意见稿与编制说明 PDF。URL: https://std.samr.gov.cn/gb/search/gbDetailed?id=4E644F8290F2704CE06397BE0A0A146E （访问日期 2026-11-05）
- 关联动态：GB/T 24002.2-2026 已于 2026-07-02 发布、2026-11-01 实施（openstd 检索结果，访问日期 2026-11-05）。
- 报批日/实施日：［未找到］（计划页只给出项目周期与下达日期）。

## 4. 职业健康安全管理体系（OHSMS）

### 4.1 ISO 45001:2018 —— 现行，修订在途

- **结论：ISO 45001:2018 仍为现行版本（2024 年复审确认继续有效）；修订在途，ISO 官网明示「预计由 ISO/DIS 45001 取代」，并计划以 ISO 45001:2027 发布。［一手已确认］**
- ISO 条目页字段：参考号 ISO 45001:2018，编号 63787，Edition 1，2018-03，状态 Published，TC ISO/TC 283，价格 CHF 196；正文「This publication was last reviewed and confirmed in 2024. Therefore this version remains current.」「This standard has 1 amendment.」「Expected to be replaced by ISO/DIS 45001 within the coming months.」「The upcoming revision, planned for publication as ISO 45001:2027」；页面内 ISO/DIS 45001 条目：参考号 ISO/DIS 45001，Edition 1，© ISO 2026，Draft，标题「Occupational health and safety management systems — Requirements with guidance for use」。URL: https://www.iso.org/standard/63787.html （访问日期 2026-11-05）
- 修正案：ISO 45001:2018/Amd 1:2024，编号 88428，2024-02，Published，阶段 60.60（同一 ISO 条目页）。
- ISO/DIS 45001 的数字参考号与「预计 2027 年上半年替代 ISO 45001:2018」的原句：［未找到/本轮未复核］（本轮缓存中无对应字样，DIS 独立条目页未取得）。

### 4.2 GB/T 45001-2020 —— 现行，未见修订计划

- **结论：GB/T 45001-2020 现行（发布 2020-03-06，实施 2020-03-06，等同采用 ISO 45001:2018）；在 std.samr.gov.cn 与 openstd 均未检索到对应的修订计划。［一手已确认（现行状态）］［未找到（修订计划）］**
- 国家标准全文公开系统条目页：状态「现行」。URL: https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno=A509BECF074159862C3E3E2DEC2487E6 （访问日期 2026-11-05）
- 全国标准信息公共服务平台条目页快照：STATE='现行'；发布 2020-03-06；实施 2020-03-06；采标「本标准等同采用ISO国际标准：ISO 45001:2018」；采标中文名「职业健康安全管理体系 要求及使用指南」；归口 TC310（全国风险管理标准化技术委员会）；主管部门 国家标准委；页面内检索「计划号」「修订计划」「正在征求意见」「项目周期」「下达日期」均无命中（缓存 .research-tmp/std_samr_45001.html）。
- openstd 检索（关键字 45001）只返回 GB/T 45001-2020（现行）与 GB/T 45007-2024（现行，2024-03-15）。
- 结论限定：以上只能证明「截至 2026-11-05 在公开平台检索不到修订计划」，不能排除尚未登记的新计划。

## 5. ISO 9001:2015 → ISO 9001:2026 条款号映射表

### 5.1 方法、证据来源与核实结论

**证据来源（均为一手）**

- **2015 版**：`SS ISO 9001:2015` 官方预览件目录页（缓存 `.research-tmp/ss9001.pdf`，1781628 字节）。其 National Foreword 自述该新加坡标准 "is identical with ISO 9001 : 2015"（预览件第 260 行原文），故其目录页即 **ISO 9001:2015 官方目录**，含 0～10 章全部条款号与标题（pp.4–7）。
- **2026 版**：ISO 9001:2026 官方预览件目录页（iii–iv 页），取自 ISO 成员机构 SIST 授权分销渠道 iTeh Standards 提供的官方预览件（https://cdn.standards.iteh.ai/sist-preview/iso/iso-9001-2026/f019c820593640a0a84140a0acb8be6d/iso-9001-2026.pdf ，访问日期 2026-11-05）。
- **交叉印证**：三标准条款对照表 `.research-tmp/cmp1.pdf`（仅作辅助，不单独作依据）。
- 本表只写条款号、标题与层级/存在性变化，**不复制任何要求条文**。

**对本文件前一版结论的更正（重要）**

前一版依据一份被误当作「ISO OBP 2015 目录」的缓存文件得出结论，称 2015 版存在 4.4.1/4.4.2、6.1.1/6.1.2、6.2.1/6.2.2、7.5.3.1/7.5.3.2、8.2.3.1/8.2.3.2、10.2.1/10.2.2。**这些编号不属于 ISO 9001:2015，它们是 ISO 9001:2008 的旧版编号**（4.4.1 质量管理体系文件、4.4.2 质量手册、6.2.1 质量目标、6.2.2 策划、7.5.1/7.5.2、8.2.3.1/8.2.3.2 等），当时被错读为 2015 版目录。以 SS ISO 9001:2015 官方目录为准，逐条核实结论如下：

| 争议点 | 核实结论 | 依据 |
| --- | --- | --- |
| 4.4 子条款 | 两版 **均无** 编号子条款（2015 标题 "Quality management system and its processes"，2026 改为 "Quality management system"） | 两版目录原文 |
| 6.1 子条款 | 2015 **无**；2026 **新增** 6.1.1、6.1.2、6.1.3 | 两版目录原文 |
| 6.2 子条款 | 两版 **均无** 编号子条款 | 两版目录原文 |
| 7.1 Resources | 两版 **均为本级条款**，7.1.1–7.1.6 相同 | 两版目录原文 |
| 7.5.3 子条款 | 两版 **均无** 编号子条款 | 两版目录原文 |
| 8.1 子条款 | 两版 **均无** 编号子条款 | 两版目录原文 |
| 8.2.3 子条款 | 两版 **均无** 编号子条款 | 两版目录原文 |
| 9.2 子条款 | 2015 **无**；2026 **新增** 9.2.1 General、9.2.2 Internal audit programme | 两版目录原文 |
| 10.2 子条款 | 两版 **均无** 编号子条款 | 两版目录原文 |

**总判断：ISO 9001:2015 与 ISO 9001:2026 的条款骨架高度一致**，结构差异集中在 0.3.3、5.2.x、6.1.x、9.2.x、10.1/10.3 重编号、附录 B 删除与第 3 章术语编号化。

### 5.2 映射表（0～6 章）

| 2015 版条款号 | 2015 版标题 | 2026 版条款号 | 2026 版标题 | 变化 |
| --- | --- | --- | --- | --- |
| 0.1 | General | 0.1 | General | 不变 |
| 0.2 | Quality management principles | 0.2 | Quality management principles | 不变 |
| 0.3 | Process approach | 0.3 | Process approach | 不变 |
| 0.3.1 | General | 0.3.1 | General | 不变 |
| 0.3.2 | Plan-Do-Check-Act cycle | 0.3.2 | Plan-Do-Check-Act cycle | 不变 |
| 0.3.3 | Risk-based thinking | — | — | **删除**（0.3 下不再有第三级条目） |
| 0.4 | Relationship with other management system standards | 0.4 | Relationship with other management system standards | 不变 |
| 1 | Scope | 1 | Scope | 不变 |
| 2 | Normative references | 2 | Normative references | 不变 |
| 3 | Terms and definitions（目录未展开条目） | 3 | Terms and definitions（引入编号条目，预览可见 3.1–3.6、3.4.1、3.5.1） | 结构变化：术语条目重新编号并新增编号子条目 |
| 4 | Context of the organization | 4 | Context of the organization | 不变 |
| 4.1 | Understanding the organization and its context | 4.1 | Understanding the organization and its context | 不变 |
| 4.2 | Understanding the needs and expectations of interested parties | 4.2 | Understanding the needs and expectations of interested parties | 不变 |
| 4.3 | Determining the scope of the quality management system | 4.3 | Determining the scope of the quality management system | 不变 |
| 4.4 | Quality management system and its processes | 4.4 | Quality management system | 标题改（两版均无子条款） |
| 5 | Leadership | 5 | Leadership | 不变 |
| 5.1 | Leadership and commitment | 5.1 | Leadership and commitment | 不变 |
| 5.1.1 | General | 5.1.1 | General | 不变 |
| 5.1.2 | Customer focus | 5.1.2 | Customer focus | 不变 |
| 5.2 | Policy | 5.2 | Quality policy | 标题改；**取消子条款** |
| 5.2.1 | Establishing the quality policy | — | — | **删除**（并入 5.2） |
| 5.2.2 | Communicating the quality policy | — | — | **删除**（并入 5.2） |
| 5.3 | Organizational roles, responsibilities and authorities | 5.3 | Roles, responsibilities and authorities | 标题改 |
| 6 | Planning | 6 | Planning | 不变 |
| 6.1 | Actions to address risks and opportunities | 6.1 | Actions to address risks and opportunities | 标题不变；**2026 新增子条款** |
| — | — | 6.1.1 | Determining risks and opportunities | **新增** |
| — | — | 6.1.2 | Actions to address risks | **新增** |
| — | — | 6.1.3 | Actions to address opportunities | **新增** |
| 6.2 | Quality objectives and planning to achieve them | 6.2 | Quality objectives and planning to achieve them | 不变（两版均无编号子条款） |
| 6.3 | Planning of changes | 6.3 | Planning of changes | 不变 |

### 5.2 映射表（续：7～10 章与附录）

| 2015 版条款号 | 2015 版标题 | 2026 版条款号 | 2026 版标题 | 变化 |
| --- | --- | --- | --- | --- |
| 7 | Support | 7 | Support | 不变 |
| 7.1 | Resources | 7.1 | Resources | 不变（两版均为本级条款） |
| 7.1.1–7.1.6 | General／People／Infrastructure／Environment for the operation of processes／Monitoring and measuring resources／Organizational knowledge | 7.1.1–7.1.6 | 同名同序 | 不变 |
| 7.2 | Competence | 7.2 | Competence | 不变 |
| 7.3 | Awareness | 7.3 | Awareness | 不变 |
| 7.4 | Communication | 7.4 | Communication | 不变 |
| 7.5 | Documented information | 7.5 | Documented information | 不变 |
| 7.5.1 | General | 7.5.1 | General | 不变 |
| 7.5.2 | Creating and updating | 7.5.2 | Creating and updating documented information | 标题明确化 |
| 7.5.3 | Control of documented information | 7.5.3 | Control of documented information | 不变（两版均无子条款） |
| 8 | Operation | 8 | Operation | 不变 |
| 8.1 | Operational planning and control | 8.1 | Operational planning and control | 不变（两版均无编号子条款） |
| 8.2 | Requirements for products and services | 8.2 | Requirements for products and services | 不变 |
| 8.2.1 | Customer communication | 8.2.1 | Customer communication | 不变 |
| 8.2.2 | Determining the requirements for products and services | 8.2.2 | Determining requirements for products and services | 去掉定冠词 |
| 8.2.3 | Review of the requirements for products and services | 8.2.3 | Review of requirements for products and services | 去掉定冠词；两版均无子条款 |
| 8.2.4 | Changes to requirements for products and services | 8.2.4 | Changes to requirements for products and services | 不变 |
| 8.3 | Design and development of products and services | 8.3 | Design and development of products and services | 不变 |
| 8.3.1–8.3.6 | General／Design and development planning／inputs／controls／outputs／changes | 8.3.1–8.3.6 | 同名同序 | 不变 |
| 8.4 | Control of externally provided processes, products and services | 8.4 | Control of externally provided processes, products and services | 不变 |
| 8.4.1–8.4.3 | General／Type and extent of control／Information for external providers | 8.4.1–8.4.3 | 同名同序 | 不变 |
| 8.5 | Production and service provision | 8.5 | Production and service provision | 不变 |
| 8.5.1–8.5.6 | Control of production and service provision／Identification and traceability／Property belonging to customers or external providers／Preservation／Post-delivery activities／Control of changes | 8.5.1–8.5.6 | 同名同序 | 不变 |
| 8.6 | Release of products and services | 8.6 | Release of products and services | 不变 |
| 8.7 | Control of nonconforming outputs | 8.7 | Control of nonconforming outputs | 不变 |
| 9 | Performance evaluation | 9 | Performance evaluation | 不变 |
| 9.1 | Monitoring, measurement, analysis and evaluation | 9.1 | Monitoring, measurement, analysis and evaluation | 不变 |
| 9.1.1–9.1.3 | General／Customer satisfaction／Analysis and evaluation | 9.1.1–9.1.3 | 同名同序 | 不变 |
| 9.2 | Internal audit | 9.2 | Internal audit | 标题不变；**2026 新增子条款** |
| — | — | 9.2.1 | General | **新增** |
| — | — | 9.2.2 | Internal audit programme | **新增** |
| 9.3 | Management review | 9.3 | Management review | 不变 |
| 9.3.1 | General | 9.3.1 | General | 不变 |
| 9.3.2 | Management review inputs | 9.3.2 | Management review inputs | 不变 |
| 9.3.3 | Management review outputs | 9.3.3 | Management review results | 标题改 |
| 10 | Improvement | 10 | Improvement | 不变 |
| 10.1 | General | — | — | **删除**（其编号位置由 10.3 上移而来） |
| 10.2 | Nonconformity and corrective action | 10.2 | Nonconformity and corrective action | 不变（两版均无编号子条款） |
| 10.3 | Continual improvement | 10.1 | Continual improvement | **重编号**（上移） |
| 附录 A（资料性） | Clarification of new structure, terminology and concepts | 附录 A（资料性） | Clarification of structure, terminology and clauses | 标题改（不引入新要求） |
| 附录 B（资料性） | Other International Standards on quality management and quality management systems developed by ISO/TC 176 | — | — | **删除** |
| 参考文献 | Bibliography | Bibliography（第 36 页） | Bibliography | 保留 |

### 5.3 结构变化小结

- **新增**：6.1.1–6.1.3（风险与机遇判定、风险应对、机遇应对）、9.2.1–9.2.2（内部审核总则、内部审核方案）。
- **删除**：0.3.3 Risk-based thinking、5.2.1、5.2.2、附录 B。
- **重编号**：2015 版 10.3 Continual improvement 上移为 2026 版 10.1；2015 版 10.1 General 删除。
- **标题变更**：4.4、5.2、5.3、7.5.2、8.2.2、8.2.3、9.3.3、附录 A。
- **第 3 章**：由目录不展开条目 → 引入编号术语条目（预览可见 3.1–3.6、3.4.1、3.5.1）。
- **专项结论**：4.4、6.2、7.5.3、8.1、8.2.3、10.2 在两版中**均无编号子条款**；只有 6.1 与 9.2 是 2026 版才有编号子条款。

## 6. 时间线（QMS / EMS / OHSMS 合并）

| 日期 | 事件 | 证据 |
| --- | --- | --- |
| 2015-09 | ISO 14001:2015 第三版发布 | ISO 条目页（一手） |
| 2016-10-13 / 2017-05-01 | GB/T 24001-2016 发布 / 实施 | openstd（一手） |
| 2016-12-30 / 2017-07-01 | GB/T 19001-2016 发布 / 实施 | openstd（一手） |
| 2018-03 | ISO 45001:2018 第一版发布（2024 年复审确认继续有效） | ISO 条目页（一手） |
| 2020-03-06 | GB/T 45001-2020 发布并实施 | openstd（一手） |
| 2024-02 | 三项 ISO 修正案发布：ISO 9001:2015/Amd 1:2024、ISO 14001:2015/Amd 1:2024、ISO 45001:2018/Amd 1:2024 | ISO 条目页（一手） |
| 2025-06-18 | ISO 14001:2015 阶段 90.93 复审确认有效，随后转入修订 | ISO 条目页（一手） |
| 2026-03-31 | GB/T 24001 修订计划 20261483-T-469 下达 | std.samr（一手） |
| 2026-04 | ISO 14001:2026 第四版发布 | ISO 主题页（一手）＋转载（二手） |
| 2026-04-15 | ISO 14001:2015 与 ISO 14001:2015/Amd 1:2024 撤回（Stage 95.99） | ISO 条目页（一手） |
| 2026-05-31 | SAC/TC 151 提出 GB/T 19001 修订立项申请 | CNIS 编制说明（一手） |
| 2026-07 | 成立标准起草组与顾问组；7-21 预研研讨会 | CNIS 编制说明（一手） |
| 2026-08-13～14 | 第一次研讨会（对 ISO/FDIS 9001:2026 逐章逐条研究） | CNIS 编制说明（一手） |
| 2026-08-28 | GB/T 19001 修订计划 20264412-T-469 下达（周期 6 个月） | std.samr（一手） |
| 2026-09-10～11 | 修订启动会暨第二次研讨会，形成征求意见稿 | CNIS 编制说明（一手） |
| 2026-09-16 | **ISO 9001:2026 发布（第六版）**；同日 ISO 9001:2015 与其 Amd 1:2024 撤回 | ISO/TC 176 公告＋ISO 条目页（一手） |
| 2026-09-17 | CNIS 发布 ISO 9001:2026 相关新闻 | CNIS（一手） |
| 2026-09-23 | ISO/TC 176 发布公告与过渡指引（5 个时间节点） | committee.iso.org（一手） |
| 2026-11-05（现状） | 三本 GB/T 均现行；GB/T 19001 与 GB/T 24001 修订计划均「正在征求意见」；ISO 45001 修订处 DIS 阶段；ISO 14001:2015、ISO 9001:2015 均已撤回 | 本次核查（一手） |
| 2027-02-28 | 计划完成 GB/T 19001 报批 | CNIS 编制说明（一手） |
| 2027-03-31 | 认证机构须具备评估 ISO 9001:2026 的能力 | ISO/TC 176（一手） |
| 2027-06-30 | 认证机构提交过渡声明及支撑材料 | ISO/TC 176（一手） |
| 2027-09-30 | 认证机构完成过渡决定 | ISO/TC 176（一手） |
| 2027 年内 | ISO 45001:2027 预计发布（官网措辞为「planned for publication as ISO 45001:2027」） | ISO 条目页（一手，具体日期未核实） |
| 2028-03-31 | 起仅可签发 ISO 9001:2026 新证书与初次认证 | ISO/TC 176（一手） |
| 2029-09-30 | 已按 ISO 9001:2015 认证的组织完成过渡的截止日 | ISO/TC 176（一手） |

## 7. 本文件未解决的问题

1. **ISO 9001:2026 的 ISO 数字目录号（线索称 88464）未获一手证据 → 未找到。** 已查：https://www.iso.org/standard/88464.html （403，8 次重试）、https://www.iso.org/standard/9001 （403）、https://www-iso-org.translate.goog/... （超时）、https://iso.org/standard/88464.html （403）、ANSI webstore（Cloudflare 挑战页）、SIST/iTeh 产品页（超时，页面未含数字目录号）。官方参考号只能确认为 **ISO 9001:2026**。
2. **ISO 9001:2026 的「实施日期」→ 未找到。** ISO 标准条目不设该字段；ISO/TC 176 过渡指引的 5 个节点（2027-03-31 / 2027-06-30 / 2027-09-30 / 2028-03-31 / 2029-09-30）是否等同于「实施日期」未获官方说明。
3. **GB/T 19001 修订计划「征求意见截止 2026-10-21」→ 未找到。** 已查：std.samr.gov.cn 计划详情页（全文无「截止」「2026-10」「10-21」字样）、CNIS 编制说明全文、CNIS 国际动态栏目；且征求意见稿 PDF 在 6 个主机 404，无法由稿件正文反证。
4. **GB/T 19001 与 GB/T 24001 的征求意见稿正文未取得**（std.samr.gov.cn / cnis.ac.cn / www.cnis.ac.cn / www.sac.gov.cn / openstd.samr.gov.cn / http 版均 404），因此两份国标的条款级新文本未能逐条核实，本文只覆盖计划字段与阶段。
5. **ISO/DIS 45001 的数字参考号与「预计 2027 年上半年替代 ISO 45001:2018」的原句 → 未找到／本轮未复核。** 本轮缓存中无该参考号字样，DIS 独立条目页未取得；能确认的只有 ISO 45001:2018 条目页上的「Expected to be replaced by ISO/DIS 45001 within the coming months」与「planned for publication as ISO 45001:2027」。
6. **GB/T 45001-2020 是否存在尚未登记的修订计划 → 未找到。** 仅能证明公开平台检索不到；不能排除未登记的新计划。
7. **ISO 14001:2026 与 ISO 45001:2027 的过渡期具体日期 → 未找到。** ISO 14001 页面仅指向认证机构咨询，OHSMS 侧无过渡公告。
8. **ISO 9001:2026 的条款内容层面变化未核实。** 预览件只含目录页、前言与第 3 章部分术语条目，要求条文不在预览范围；本文对 2026 版只做编号与标题层级的映射。
9. **2026 版第 3 章术语清单未逐一核实。** CNIS 编制说明称国标侧「增加 23 个核心术语和定义」，但预览目录未展开 3.x，无法逐条对应到 ISO 版的术语条目。
10. **GB/T 19001-2016「2023-12-28 复审继续有效」的原始复审结论文件未单独取证**，本文依赖 std.samr.gov.cn 条目页字段。
11. **ISO 9001:2015 本体条目页（62085）状态字段未直接读取**（403），结论由其修正案条目页 life cycle + ISO 出版物前言 + CNIS 报道共同支撑。
12. **Wayback（web.archive.org）本机不可达**，无法用历史快照补齐 ISO 编号页上的数字目录号与状态字段。

## 8. 已查过的源清单（含结果）

| URL | 类型 | 结果（访问日期 2026-11-05） |
| --- | --- | --- |
| https://www.iso.org/standard/88431.html | ISO 条目页 | 成功（快照）：ISO 9001:2015/Amd 1:2024，Withdrawn，95.99，2026-09-16，Revised by ISO 9001:2026 |
| https://www.iso.org/standard/62085.html | ISO 条目页 | 403 不可达 |
| https://www.iso.org/standard/88464.html | ISO 条目页 | 403 不可达（8 次重试） |
| https://www.iso.org/standard/92300.html | ISO 条目页 | 403 不可达 |
| https://www.iso.org/standard/60857.html | ISO 条目页 | 成功（快照）：ISO 14001:2015，Withdrawn，95.99，2026-04-15，Revised by ISO 14001:2026 |
| https://www.iso.org/standard/88209.html | ISO 条目页 | 成功（快照）：ISO 14001:2015/Amd 1:2024，Withdrawn，95.99，2026-04-15 |
| https://www.iso.org/standard/14001 | ISO 主题页 | 成功（快照）：ISO 14001:2026，Edition 4，2026-04，92300，Published |
| https://www.iso.org/standard/63787.html | ISO 条目页 | 成功（快照）：ISO 45001:2018 remains current；ISO/DIS 45001；ISO 45001:2027 |
| https://www.iso.org/standard/9001 | ISO 条目页 | 403 直连；经检索快照取得标题「ISO 9001:2026 - Quality management systems — Requirements」（二手） |
| https://www.iso.org/quality-management/iso-9001-2026 | ISO 洞察页 | 403 直连；经检索快照取得摘要（二手） |
| https://www.iso.org/news/2026/04/iso-14001-2026-published | ISO 新闻页 | 403 直连；经中文转载确认标题与发布月份（二手） |
| https://committee.iso.org/home/tc176sc2 | ISO 委员会页 | 成功：Our Standards 首项为 ISO 9001: 2026 |
| https://committee.iso.org/sites/tc176/home/news/content-left-area/news-and-updates/news.html | ISO/TC 176 新闻 | 成功：发布日 2026-09-16 + 5 个过渡节点 |
| https://www.iso.org/obp/rest/standard/iso:std:iso:9001:ed-6:v1:en | OBP REST | 403（此前会话曾取到 ed-5 缓存，但其内容被误读，本文件 §5 已改用 SS ISO 9001:2015 预览件目录） |
| SS ISO 9001:2015 官方预览件（singaporestandardseshop.sg 直链，缓存 .research-tmp/ss9001.pdf） | 国家标准等同件预览 | 成功：1781628 字节，目录页 pp.4–7 完整，National Foreword 自述 identical with ISO 9001:2015 |
| https://cdn.standards.iteh.ai/sist-preview/iso/iso-9001-2026/f019c820593640a0a84140a0acb8be6d/iso-9001-2026.pdf | ISO 出版物预览件 | 成功：898,835 字节，含目录页与前言 |
| https://ecommerce.sist.si/catalog/standards/iso/2aa96449-a8f0-4e8a-b2dd-09e30a94b05f/iso-9001-2026 | SIST 官方电商 | 超时 |
| https://www.cnis.ac.cn/gjbzh/gjdt/202609/t20260917_63681.html | CNIS 新闻 | 成功：ISO 9001:2026 替代 9001:2015 |
| https://www.cnis.ac.cn/bydt/bzyjzq/gbyjzq/202609/P020260922713471823744.pdf | CNIS 编制说明 | 成功：计划号、周期、报批时间表、结构与技术变化 |
| https://std.samr.gov.cn/gb/search/gbDetailed?id=71F772D814AED3A7E05397BE0A0AB82A | GB/T 19001-2016 条目页 | 成功：现行、复审、采标、修订计划挂接 |
| https://std.samr.gov.cn/gb/search/gbDetailed?id=5A7BF1C508FA8432E06397BE0A0A8F1E | 20264412-T-469 计划页 | 成功：计划字段全量；附件下载 404 |
| https://std.samr.gov.cn/gb/search/gbDetailed?id=71F772D81112D3A7E05397BE0A0AB82A | GB/T 24001-2016 条目页 | 成功：现行、复审 |
| https://std.samr.gov.cn/gb/search/gbDetailed?id=4E644F8290F2704CE06397BE0A0A146E | 20261483-T-469 计划页 | 成功：计划字段全量 |
| https://std.samr.gov.cn/zxd/2026003416/20_标准起草/20_WD_SIDS_2026003416_质量管理体系 要求.pdf | 征求意见稿附件 | 404（6 主机） |
| https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno=8211B23BE58F6076946628F7E83CB1BC | GB/T 19001-2016 详情 | 成功：现行 |
| https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno=61011DD84D5D3AB7F5BCFEA911460D3D | GB/T 24001-2016 详情 | 成功：现行 |
| https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno=A509BECF074159862C3E3E2DEC2487E6 | GB/T 45001-2020 详情 | 成功：现行 |
| https://openstd.samr.gov.cn/bzgk/std/std_list?…&p.p2=19001 / 24001 / 45001 | openstd 检索列表 | 成功：逐条状态与日期 |
| https://std.samr.gov.cn/gj | 全国标准信息公共服务平台·国际标准动态 | 成功：ISO 14001:2026 等动态 |
| https://zwpt.sacinfo.org.cn/cnf/html/ISO/111532.jhtml | ISO 14001:2026 中文转载 | 成功（二手） |
| https://webstore.ansi.org/standards/iso/iso90012026 | ANSI webstore | Cloudflare 挑战页 |
| https://web.archive.org/ | Wayback | 连接超时 |

---

**附：本文件引用的一手缓存件位置（工作区内，未入库）**：.research-tmp/iso_9001_amd1_2024_page_flat.txt、.research-tmp/iso_14001_2015_page_flat.txt、.research-tmp/iso_14001_hub_flat.txt、.research-tmp/iso_std_full.html（ISO 45001:2018）、.research-tmp/ss9001.pdf（SS ISO 9001:2015 官方预览件，其目录即 ISO 9001:2015 官方目录）、.research-tmp/ss9001_full.txt（其全文提取）、.research-tmp/iteh_9001_2026.pdf 与 .research-tmp/iteh_9001_2026.txt（ISO 9001:2026 预览件与提取文本）、.research-tmp/cnis_9001_2026_notice.pdf / .txt（CNIS 编制说明）、.research-tmp/samr_plan_19001_live.html、.research-tmp/std_samr_45001.html。




