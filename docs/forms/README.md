# 机构成果表单（填写副本）

本目录存放 **12 类认证机构成果表单的填写副本**，由 tools/build_forms.py 从机构中立模板 tools/templates/families/*.yaml 生成（GitHub Issue #19）。

## 目录

| 路径 | 内容 |
| --- | --- |
| tools/templates/families/ | 12 类模板族定义（机构中立，唯一真源） |
| tools/templates/agency_profiles/ | 机构差异配置（换机构只改这里） |
| tools/templates/form_number_registry.yaml | 表单号登记表（只登记形态，不登记原文） |
| tools/templates/sample_values.yaml | 填写样例值 |
| tools/build_forms.py | 填写副本构建器 |
| tools/scan_templates.py | 模板缺陷扫描器 |
| docs/forms/filled/ | 本次构建的填写副本（.md 副本 + .json 底稿） |
| docs/forms/placeholder-dictionary.md | 占位符表 |
| docs/forms/defect-report.md | 模板缺陷扫描报告 |

## 重新生成

```bash
python tools/build_forms.py
python tools/scan_templates.py --dir tools/templates/families \
    --json docs/forms/_scan.json --out docs/forms/_scan.md
```

## 纪律

1. **模板本体机构中立**：模板里不得出现机构名、表单号原文、机构内部编号。
2. **换机构只改配置**：改 tools/templates/agency_profiles/<profile>.yaml，模板与扫描器不动。
3. **残留必须为 0**：填写副本的双花括号残留数与裸花括号残留数都必须为 0。
4. **未提供就留空并显式列出**：未提供的占位符填「（待填）」，并在占位符表第 4 节列出。
5. **不代签**：本目录只生成表单内容，签署栏由人签（见 docs/spec/audit-process-state-machine.md §11.2）。

## 本次构建结果

- 模板族数量：12
- 双花括号残留合计：0
- 裸花括号残留合计：0
- 未提供值的占位符合计：34

