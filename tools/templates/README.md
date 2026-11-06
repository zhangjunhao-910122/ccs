# 机构成果模板集（Issue #19）

本目录是**机构中立**的成果模板集：模板里**不写任何真实机构表单号、机构名、地址或内部编号**，
机构差异一律以占位符出现（output-formats.md §9.4、§11.1、§11.2②）。

## 目录结构

    tools/templates/
      README.md                     本文件
      form_number_registry.yaml     机构表单号登记表（只登记编号形态与出处）
      families/F01..F12_*.yaml      §11.1 的 12 类机构表单族模板定义

## 12 类机构表单族（output-formats.md §11.1）

| 编号 | 表单族 | 模板文件 |
| ---- | ------ | -------- |
| F01 | 审核计划书 | F01_audit_plan_book.yaml |
| F02 | 审核计划表 | F02_audit_plan_schedule.yaml |
| F03 | 首末次会议记录 | F03_opening_closing_meeting_record.yaml |
| F04 | 首末次会议签到表 | F04_opening_closing_meeting_signin.yaml |
| F05 | 现场审核检查表（管理层） | F05_checklist_management.yaml |
| F06 | 现场审核检查表（部门） | F06_checklist_department.yaml |
| F07 | 现场审核不符合报告 | F07_nonconformity_report.yaml |
| F08 | 审核报告 | F08_audit_report.yaml |
| F09 | 第一阶段审核报告 | F09_stage1_audit_report.yaml |
| F10 | 管理体系文件评审报告 | F10_document_review_report.yaml |
| F11 | 审核人员到达离开受审核方信息确认表 | F11_arrival_departure_confirmation.yaml |
| F12 | 审核组工作情况反馈表 | F12_audit_team_feedback.yaml |

每个模板都写明：模板编号占位（只有 {{表单编号}} / {{表单名称}}）、
字段清单（key / label / 必填标记 / 占位符 / 填充来源 / 字典出处）、
章节顺序（order / id / title / 引用字段）、
与四种产物形态（xlsx / docx / json / md）的对应关系。

## 占位符口径

* §9.1 占位符字典 30 条：`{{任务标识}}`、`{{认证机构名称}}`、`{{表单编号}}` 以外的常用项……
  完整清单见 docs/spec/output-formats.md §9.1，代码里是 `agency_isolation.PLACEHOLDER_DICT`。
* 机构差异占位符（模板层扩展，4 条）：`{{表单编号}}`、`{{表单名称}}`、`{{机构内部编号}}`、`{{机构标识}}`。
* 模板字段占位符（模板层扩展，17 条）：审核点编号、条款号、条款标题、条款引用、审核问题、
  判定结论、判定依据、证据锚点、评审人、确认状态、不符合编号、不符合类别、事实陈述、
  整改要求、完成时限、不符合状态、缺口说明。

模板层占位符**只允许出现在模板与清洗中间产物里**；渲染进五件交付物前必须全部填掉，
否则违反 §9.2「双花括号残留数 = 0」。

## 表单号登记纪律

form_number_registry.yaml **只登记编号形态与来源出处，不复制任何真实表单号原文**
（公开标准号除外）。登记表的条目形如：

    - id: FN-UICC-MS
      agency: AGENCY-UICC
      form_number_template: "UI-R-MS-<三位数字>"
      pattern: '^UI-R-MS-\d{3}$'
      source: "docs/research/external-package-and-uicc-review.md §2.7"

判定顺序：中性占位符形态 → 公开标准号 → 已登记机构表单号（**命中即报冲突并阻断**）→ 未登记编号（放行）。

## 用法

    import sys
    sys.path.insert(0, "tools")
    import agency_isolation as ai

    reg = ai.load_registry()                      # 加载登记表
    tpl = ai.load_family_template("F01")           # 取模板族定义
    res = ai.sanitize_template(机构模板, agency_profile=profile)   # 清洗：抽机构标识，可逆
    mid = ai.templatize_template(机构模板, agency_profile=profile)  # 占位化：转机构中立模板
    ai.isolate_form_numbers(文本, registry=reg)    # 表单号隔离：命中已登记编号即阻断

三个动作**互相独立、可单独调用**。
