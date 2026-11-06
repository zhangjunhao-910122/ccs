# 模板集机构中立缺陷报告

GitHub Issue #19「模板集与机构差异隔离」的验收证据。

## 1. 目的与口径

tools/templates/families 下的 12 类审核表单模板必须与任何一家认证机构解绑：模板本身不含机构名、机构表单号、机构内部相对时限口径，也不含残留的真实日期；标准号（如 GB/T 19001-2016、ISO 9001:2015、CNCA-QMS-01-2025）不得被清洗流程误伤。

扫描工具：tools/scan_templates.py（918 行，7 类缺陷检测，有缺陷时退出码 2）。

## 2. 复现命令

    /i/python3.10/python.exe tools/scan_templates.py --dir tools/templates/families --json docs/forms/_scan.json --out .research-tmp/scan_final.md

    /i/python3.10/python.exe tools/scan_templates.py --verify-profiles tools/templates/agency_profiles --dir tools/templates/families

两次运行退出码均为 0（无缺陷时退出码 0）。机器可读结果见 docs/forms/_scan.json。

## 3. 缺陷类别定义

| 类别 | 含义 |
|---|---|
| agency_form_number | 模板中出现机构专属表单号前缀 |
| agency_name | 模板中硬编码机构名称 |
| internal_deadline | 出现机构内部相对时限口径（如「15 个工作日内提交」） |
| residual_real_date | 残留未经占位符化的真实日期 |
| cleaning_damage | 清洗误伤：标准号被占位符切断 |
| residual_placeholder | 残留未替换的合法占位符 |
| exempt_config | 机构差异配置文件自身的问题（默认排除） |

## 4. 扫描结果

扫描目标：tools/templates/families　扫描文件数：12　豁免：0　机构差异配置：tools/templates/agency_profiles/example.yaml（profile_id AGENCY-EXAMPLE，机构名「示例认证机构」）。

| 缺陷类别 | 计数 |
|---|---|
| agency_form_number | 0 |
| agency_name | 0 |
| internal_deadline | 0 |
| residual_real_date | 0 |
| cleaning_damage | 0 |
| residual_placeholder | 0 |
| **合计** | **0** |

结论：模板集在默认配置下 0 缺陷。

## 5. 机构差异配置切换验证

同一套模板在不同机构差异配置下必须同样保持机构中立：

| 配置 | profile_id | 表单号前缀 | 机构名残留 | 表单号残留 | 缺陷合计 |
|---|---|---|---|---|---|
| alt-example.yaml | AGENCY-ALT-EXAMPLE | XFZ-R-MS | 0 | 0 | 0 |
| example.yaml | AGENCY-EXAMPLE | XF-R-MS | 0 | 0 | 0 |

验证通过：各配置下机构名称与机构表单号残留均为 0。

## 6. 填写副本构建结果（build_forms.py）

tools/build_forms.py 以 templates/sample_values.yaml 为样例值，把 12 类模板渲染为可交付的填写副本，产出在 docs/forms/filled/（12 份 .md 填写副本 + 12 份 .json 底稿 + _build-summary.json）。

| 指标 | 值 |
|---|---|
| 渲染族数 | 12 / 12 |
| 双花括号占位符残留 | 0 |
| 裸花括号残留 | 0 |
| 未提供值的占位符（显式列为「（待填）」并登记进字典） | 34 处（F01 1、F05 8、F06 8、F07 10、F10 7） |

## 7. 遗留与后续

- 未提供的 34 处占位符属样例值未覆盖，不是模板缺陷；补齐 templates/sample_values.yaml 即可全部填实。
- 缺陷检测随模板族增减自动扩展，新增族后需重跑本报告第 2 节两条命令。
- 机构名与表单号改为运行时按机构差异配置注入，见 docs/spec/output-formats.md。