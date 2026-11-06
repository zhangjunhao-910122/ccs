#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""填写副本构建器（GitHub Issue #19）。

把 tools/templates/families/*.yaml 的 12 类机构成果模板渲染成
docs/forms/filled/ 下的填写副本，并产出：

  * 占位符表      docs/forms/placeholder-dictionary.md
  * 构建摘要      docs/forms/filled/_build-summary.json
  * 说明          docs/forms/README.md

纪律（docs/spec/output-formats.md §9.2）：

  * 填写副本里双花括号残留数必须为 0；
  * 裸花括号残留数必须为 0；
  * 未提供的占位符填「（待填）」，并在摘要与占位符表中**显式列出**，
    不得静默消失。

用法：

    python tools/build_forms.py
    python tools/build_forms.py --selftest
    python tools/build_forms.py --out-dir <目录> --values <yaml>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent
TEMPLATES_DIR = TOOLS_DIR / "templates"
FAMILIES_DIR = TEMPLATES_DIR / "families"
DEFAULT_VALUES = TEMPLATES_DIR / "sample_values.yaml"
DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "forms" / "filled"
DEFAULT_DICT_MD = REPO_ROOT / "docs" / "forms" / "placeholder-dictionary.md"
DEFAULT_README = REPO_ROOT / "docs" / "forms" / "README.md"

FILL_MARK = "（待填）"
PLACEHOLDER_RE = re.compile(r"\{\{[^{}]+\}\}")
FENCE = chr(96) * 3

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import agency_isolation as ai  # noqa: E402  (必须在 sys.path 调整之后)


# --------------------------------------------------------------------------
# 输入
# --------------------------------------------------------------------------


def load_values(path: Any) -> Dict[str, str]:
    """读取填写值。样本文件结构：{schema_version, values: {占位符键: 值}}。"""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    values = data.get("values") or {}
    return {str(k): ("" if v is None else str(v)) for k, v in values.items()}


def placeholder_key(token: str) -> str:
    """{{任务标识}} -> 任务标识"""
    t = str(token).strip()
    if t.startswith("{{") and t.endswith("}}"):
        return t[2:-2]
    return t


def used_placeholders(template: Any) -> List[str]:
    """模板中出现的全部占位符（去重、排序）。"""
    text = json.dumps(template, ensure_ascii=False, sort_keys=True)
    found: List[str] = []
    for m in PLACEHOLDER_RE.finditer(text):
        token = m.group(0)
        if token not in found:
            found.append(token)
    return sorted(found)


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------


def field_value(field: Dict[str, Any], values: Dict[str, str]) -> str:
    """字段取值：按 placeholder 对应的键取值；未提供则用「（待填）」。"""
    key = placeholder_key(str(field.get("placeholder") or ""))
    if key and key in values and values[key] != "":
        return values[key]
    return FILL_MARK


def build_sections(family: Dict[str, Any], values: Dict[str, str]) -> List[Dict[str, Any]]:
    """按模板的 sections 结构生成已填值的区块。"""
    field_index = {str(f.get("key")): f for f in (family.get("fields") or [])}
    sections: List[Dict[str, Any]] = []
    for sec in family.get("sections") or []:
        rows: List[Dict[str, Any]] = []
        for key in sec.get("fields") or []:
            f = field_index.get(str(key))
            if f is None:
                continue
            rows.append(
                {
                    "key": str(key),
                    "label": str(f.get("label") or key),
                    "value": field_value(f, values),
                    "required": bool(f.get("required")),
                    "source": str(f.get("source") or ""),
                    "dictionary_ref": str(f.get("dictionary_ref") or ""),
                    "placeholder": str(f.get("placeholder") or ""),
                }
            )
        sections.append(
            {
                "order": sec.get("order"),
                "id": str(sec.get("id") or ""),
                "title": str(sec.get("title") or ""),
                "rows": rows,
            }
        )
    return sections


def family_markdown(
    family: Dict[str, Any],
    rendered: Dict[str, Any],
    sections: List[Dict[str, Any]],
    values: Dict[str, str],
) -> str:
    """生成填写副本的 Markdown 正文。"""
    fid = str(family.get("family_id") or "")
    lines: List[str] = []
    lines.append("# %s %s" % (fid, str(family.get("name") or "")))
    lines.append("")
    lines.append(
        "> 机构成果模板的填写副本（GitHub Issue #19 产出物）。"
        "模板本体机构中立，机构差异只来自 tools/templates/agency_profiles/ 下的配置。"
    )
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("| --- | --- |")
    lines.append("| 模板族 | %s |" % fid)
    lines.append("| 模板名称 | %s |" % str(family.get("name") or ""))
    lines.append("| 模板简称 | %s |" % str(family.get("short_name") or ""))
    lines.append("| 表单编号 | %s |" % (values.get("表单编号") or FILL_MARK))
    lines.append("| 表单名称 | %s |" % (values.get("表单名称") or FILL_MARK))
    lines.append("| 机构内部编号 | %s |" % (values.get("机构内部编号") or FILL_MARK))
    lines.append("| 任务标识 | %s |" % (values.get("任务标识") or FILL_MARK))
    lines.append("| 成果状态 | %s |" % (values.get("成果状态") or FILL_MARK))
    lines.append("| 成果版本 | %s |" % (values.get("版本号") or FILL_MARK))
    lines.append("")
    for sec in sections:
        lines.append("## %s" % sec["title"])
        lines.append("")
        for row in sec["rows"]:
            lines.append("- **%s**：%s" % (row["label"], row["value"]))
        lines.append("")
    lines.append("## 正文")
    lines.append("")
    lines.append(str(rendered.get("sample_body") or "").rstrip())
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "本副本由 tools/build_forms.py 自动生成；重新生成请运行 "
        "python tools/build_forms.py。占位符口径见 "
        "docs/forms/placeholder-dictionary.md。"
    )
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 构建
# --------------------------------------------------------------------------


def build(
    out_dir: Any = DEFAULT_OUT_DIR,
    values_path: Any = DEFAULT_VALUES,
    dict_md: Any = DEFAULT_DICT_MD,
    readme: Any = DEFAULT_README,
    families_dir: Any = FAMILIES_DIR,
) -> Tuple[int, Dict[str, Any]]:
    """构建全部填写副本。返回 (退出码, 构建摘要)。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    values = load_values(values_path)
    families = ai.load_family_templates(families_dir=families_dir)

    summary: Dict[str, Any] = {
        "schema_version": 1,
        "generated_by": "tools/build_forms.py",
        "values_source": str(values_path),
        "family_count": len(families),
        "families": [],
        "residual_total": 0,
        "bare_brace_total": 0,
        "missing_placeholder_total": 0,
        "unfilled_marker": FILL_MARK,
    }
    dictionary_rows: List[Dict[str, Any]] = []

    for fid in sorted(families):
        family = families[fid]
        rendered, template_residuals = ai.render_template(family, values)
        sections = build_sections(family, values)
        md = family_markdown(family, rendered, sections, values)

        used = used_placeholders(family)
        missing = [
            p
            for p in used
            if placeholder_key(p) not in values or values.get(placeholder_key(p), "") == ""
        ]

        residuals = ai.find_residual_placeholders(md, strict=True)
        bare = ai.count_bare_braces(md)
        summary["residual_total"] += len(residuals)
        summary["bare_brace_total"] += bare
        summary["missing_placeholder_total"] += len(missing)

        stem = Path(str(family.get("_path") or (fid + ".yaml"))).stem
        md_path = out / (stem + ".md")
        json_path = out / (stem + ".json")
        md_path.write_text(md, encoding="utf-8")

        draft = {
            "schema_version": 1,
            "artifact": "过程底稿",
            "template_family": {
                "family_id": fid,
                "name": str(family.get("name") or ""),
                "short_name": str(family.get("short_name") or ""),
                "source_of_list": str(family.get("source_of_list") or ""),
                "form_number": family.get("form_number") or {},
            },
            "artifacts": family.get("artifacts") or [],
            "sections": sections,
            "used_placeholders": used,
            "missing_placeholders": missing,
            "residual_placeholders": residuals,
            "bare_braces": bare,
            "unfilled_count": sum(
                1 for s in sections for r in s["rows"] if r["value"] == FILL_MARK
            ),
        }
        json_path.write_text(
            json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        summary["families"].append(
            {
                "family_id": fid,
                "name": str(family.get("name") or ""),
                "markdown": str(md_path),
                "draft": str(json_path),
                "used_placeholders": used,
                "missing_placeholders": missing,
                "residual_count": len(residuals),
                "bare_brace_count": bare,
                "template_residual_count": len(template_residuals),
            }
        )
        dictionary_rows.append(
            {
                "family_id": fid,
                "name": str(family.get("name") or ""),
                "used": used,
                "missing": missing,
            }
        )

    summary_path = out / "_build-summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    _write_dictionary(Path(dict_md), values, dictionary_rows, summary)
    _write_readme(Path(readme), summary)

    code = 0 if summary["residual_total"] == 0 and summary["bare_brace_total"] == 0 else 2
    return code, summary


def _write_dictionary(
    path: Path,
    values: Dict[str, str],
    rows: List[Dict[str, Any]],
    summary: Dict[str, Any],
) -> None:
    """写占位符表：字典总表 + 每族使用矩阵 + 未提供清单。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    lines.append("# 占位符表（机构成果模板填写副本）")
    lines.append("")
    lines.append(
        "本表由 tools/build_forms.py 自动生成，来源 "
        "tools/agency_isolation.py 的 PLACEHOLDER_DICT 与 "
        "AGENCY_DIFF_PLACEHOLDERS，口径同 "
        "docs/spec/output-formats.md §9.1。"
    )
    lines.append("")
    lines.append("## 1 字典总表")
    lines.append("")
    lines.append("| 占位符 | 含义 | 来源 |")
    lines.append("| --- | --- | --- |")
    for token, meaning in sorted(ai.PLACEHOLDER_DICT.items()):
        lines.append("| %s | %s | §9.1 通用字典 |" % (token, meaning))
    for token, meaning in sorted(ai.AGENCY_DIFF_PLACEHOLDERS.items()):
        lines.append("| %s | %s | 模板层扩展字典 |" % (token, meaning))
    lines.append("")
    lines.append("## 2 本次构建使用的值")
    lines.append("")
    lines.append("| 占位符键 | 值 |")
    lines.append("| --- | --- |")
    for key in sorted(values):
        lines.append("| %s | %s |" % (key, values[key]))
    lines.append("")
    lines.append("## 3 各模板族占位符使用矩阵")
    lines.append("")
    for row in rows:
        lines.append("### %s %s" % (row["family_id"], row["name"]))
        lines.append("")
        if not row["used"]:
            lines.append("（无占位符）")
            lines.append("")
            continue
        for token in row["used"]:
            key = placeholder_key(token)
            state = "已提供" if values.get(key, "") else "**未提供（填「%s」）**" % FILL_MARK
            lines.append("- %s —— %s" % (token, state))
        lines.append("")
    lines.append("## 4 未提供值的占位符清单")
    lines.append("")
    total = summary["missing_placeholder_total"]
    if total == 0:
        lines.append("（本次构建没有未提供的占位符）")
    else:
        lines.append("以下占位符在本次样例值中未提供，填写副本中一律填「%s」：" % FILL_MARK)
        lines.append("")
        for row in rows:
            if not row["missing"]:
                continue
            lines.append(
                "- **%s %s**：%s"
                % (row["family_id"], row["name"], "、".join(row["missing"]))
            )
    lines.append("")
    lines.append("## 5 残留检查")
    lines.append("")
    lines.append(
        "- 双花括号残留合计：**%d**（口径：strict，任何 {{...}} 都算残留）"
        % summary["residual_total"]
    )
    lines.append("- 裸花括号残留合计：**%d**" % summary["bare_brace_total"])
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_readme(path: Path, summary: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    lines.append("# 机构成果表单（填写副本）")
    lines.append("")
    lines.append(
        "本目录存放 **12 类认证机构成果表单的填写副本**，由 "
        "tools/build_forms.py 从机构中立模板 "
        "tools/templates/families/*.yaml 生成（GitHub Issue #19）。"
    )
    lines.append("")
    lines.append("## 目录")
    lines.append("")
    lines.append("| 路径 | 内容 |")
    lines.append("| --- | --- |")
    lines.append("| tools/templates/families/ | 12 类模板族定义（机构中立，唯一真源） |")
    lines.append("| tools/templates/agency_profiles/ | 机构差异配置（换机构只改这里） |")
    lines.append("| tools/templates/form_number_registry.yaml | 表单号登记表（只登记形态，不登记原文） |")
    lines.append("| tools/templates/sample_values.yaml | 填写样例值 |")
    lines.append("| tools/build_forms.py | 填写副本构建器 |")
    lines.append("| tools/scan_templates.py | 模板缺陷扫描器 |")
    lines.append("| docs/forms/filled/ | 本次构建的填写副本（.md 副本 + .json 底稿） |")
    lines.append("| docs/forms/placeholder-dictionary.md | 占位符表 |")
    lines.append("| docs/forms/defect-report.md | 模板缺陷扫描报告 |")
    lines.append("")
    lines.append("## 重新生成")
    lines.append("")
    lines.append(FENCE + "bash")
    lines.append("python tools/build_forms.py")
    lines.append("python tools/scan_templates.py --dir tools/templates/families \\")
    lines.append("    --json docs/forms/_scan.json --out docs/forms/_scan.md")
    lines.append(FENCE)
    lines.append("")
    lines.append("## 纪律")
    lines.append("")
    lines.append("1. **模板本体机构中立**：模板里不得出现机构名、表单号原文、机构内部编号。")
    lines.append("2. **换机构只改配置**：改 tools/templates/agency_profiles/<profile>.yaml，模板与扫描器不动。")
    lines.append("3. **残留必须为 0**：填写副本的双花括号残留数与裸花括号残留数都必须为 0。")
    lines.append("4. **未提供就留空并显式列出**：未提供的占位符填「%s」，并在占位符表第 4 节列出。" % FILL_MARK)
    lines.append("5. **不代签**：本目录只生成表单内容，签署栏由人签（见 docs/spec/audit-process-state-machine.md §11.2）。")
    lines.append("")
    lines.append("## 本次构建结果")
    lines.append("")
    lines.append("- 模板族数量：%d" % summary["family_count"])
    lines.append("- 双花括号残留合计：%d" % summary["residual_total"])
    lines.append("- 裸花括号残留合计：%d" % summary["bare_brace_total"])
    lines.append("- 未提供值的占位符合计：%d" % summary["missing_placeholder_total"])
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def selftest() -> int:
    """自检：12 族全部能渲染，残留为 0。"""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="build_forms_") as tmp:
        code, summary = build(
            out_dir=Path(tmp) / "filled",
            values_path=DEFAULT_VALUES,
            dict_md=Path(tmp) / "dict.md",
            readme=Path(tmp) / "README.md",
        )
        if code != 0:
            print("自检失败：残留不为 0", file=sys.stderr)
            return 1
        if summary["family_count"] != 12:
            print("自检失败：模板族数量 %d != 12" % summary["family_count"], file=sys.stderr)
            return 1
    print("[OK] selftest 通过：12 族全部渲染，残留为 0")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="build_forms",
        description="机构成果模板填写副本构建器（GitHub Issue #19）",
    )
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), metavar="DIR",
                   help="填写副本输出目录（默认 %s）" % DEFAULT_OUT_DIR)
    p.add_argument("--values", default=str(DEFAULT_VALUES), metavar="YAML",
                   help="填写值文件（默认 %s）" % DEFAULT_VALUES)
    p.add_argument("--dict-md", default=str(DEFAULT_DICT_MD), metavar="MD",
                   help="占位符表输出路径（默认 %s）" % DEFAULT_DICT_MD)
    p.add_argument("--readme", default=str(DEFAULT_README), metavar="MD",
                   help="说明文件输出路径（默认 %s）" % DEFAULT_README)
    p.add_argument("--selftest", action="store_true", help="只跑自检")
    p.add_argument("--quiet", action="store_true", help="只输出汇总")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.selftest:
        return selftest()

    code, summary = build(
        out_dir=args.out_dir,
        values_path=args.values,
        dict_md=args.dict_md,
        readme=args.readme,
    )
    if not args.quiet:
        print("模板族数量：%d" % summary["family_count"])
        print("双花括号残留合计：%d" % summary["residual_total"])
        print("裸花括号残留合计：%d" % summary["bare_brace_total"])
        print("未提供值的占位符合计：%d" % summary["missing_placeholder_total"])
        for row in summary["families"]:
            print(
                "  %-4s %-28s 残留 %d，裸括号 %d，未提供 %d"
                % (
                    row["family_id"],
                    row["name"][:28],
                    row["residual_count"],
                    row["bare_brace_count"],
                    len(row["missing_placeholders"]),
                )
            )
    if code != 0:
        print("构建失败：残留不为 0", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
