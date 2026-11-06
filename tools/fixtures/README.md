# 体系文件样本（fixtures）

本目录存放 9001 资料导入与要素抽取的测试样本，内容全部为虚构的示例公司材料。

## 生成方式

```bash
export PYTHONIOENCODING=utf-8
/i/python3.10/python.exe tools/fixtures/build_fixtures.py
```

脚本可重复执行，覆盖同名文件。二进制样本（xlsx / docx / pdf）由脚本生成，不手工编辑。

## 样本清单

| 文件 | 类型 | 用途 |
| --- | --- | --- |
| `quality_manual_A1.md` | 合格材料 | 覆盖 Markdown 解析、条款号抽取、过程/场所/设备/记录四类要素 |
| `process_site_register.xlsx` | 合格材料 | 覆盖表格解析、行级与单元格两种定位；含一个越界条款号 9.9.9 |
| `internal_audit_report_2026Q2.docx` | 合格材料 | 覆盖 Word 解析：标题分节、正文段落、表格单元格 |
| `procedure_traceability_A0.md` | 矛盾版本 | 同一条款 8.5.2 出现互斥陈述，并带显式 `|` conflict` 标记 |
| `supplier_cert_scan.pdf` | 不可读材料 | 无文本层的扫描件，只有图形 |
| `garbled_scan.txt` | 不可读材料 | 私用区字符乱码 |

## 期望的导入判定

- `quality_manual_A1.md`：要素全部落在条款树内（4.4 / 8.1 / 8.5.2 / 7.5.3 / 10.2 / 9.1），版本 A/1.0。
- `process_site_register.xlsx`：9.9.9 不在条款树中，校验报告出现 CLAUSE_NOT_IN_TREE。
- `procedure_traceability_A0.md`：校验报告出现 conflict 类问题，元素标签含 `conflict_declared`。
- `supplier_cert_scan.pdf` 与 `garbled_scan.txt`：归入 unreadable（不可读）问题类。

## 注意

本目录下的 .md / .xlsx / .docx / .pdf / .txt 均会被导入器当成待导入材料；`build_fixtures.py` 与本说明文件属于脚本与文档，不属于体系材料。
