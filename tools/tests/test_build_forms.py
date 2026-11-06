#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/build_forms.py 的测试（GitHub Issue #19）。

纪律：
  * 12 类模板族必须全部能渲染成填写副本；
  * 填写副本的双花括号残留数与裸花括号残留数必须为 0；
  * 未提供的占位符必须显式列出（不得静默消失）。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import agency_isolation as ai  # noqa: E402
import build_forms  # noqa: E402


class TestBuildForms(unittest.TestCase):
    """填写副本构建器。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="build_forms_test_"))
        cls.out_dir = cls.tmp / "filled"
        cls.code, cls.summary = build_forms.build(
            out_dir=cls.out_dir,
            values_path=build_forms.DEFAULT_VALUES,
            dict_md=cls.tmp / "placeholder-dictionary.md",
            readme=cls.tmp / "README.md",
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_exit_code_zero(self):
        """构建成功返回 0。"""
        self.assertEqual(self.code, 0)

    def test_twelve_families_rendered(self):
        """12 类模板族全部渲染。"""
        self.assertEqual(self.summary["family_count"], 12)
        self.assertEqual(len(self.summary["families"]), 12)

    def test_markdown_and_draft_written(self):
        """每族一份 .md 填写副本 + 一份 .json 底稿 + 一份摘要。"""
        mds = sorted(self.out_dir.glob("*.md"))
        jsons = sorted(self.out_dir.glob("*.json"))
        self.assertEqual(len(mds), 12)
        self.assertEqual(len(jsons), 13)  # 12 份底稿 + _build-summary.json
        self.assertTrue((self.out_dir / "_build-summary.json").exists())

    def test_no_residual_placeholders(self):
        """双花括号残留数必须为 0（§9.2）。"""
        self.assertEqual(self.summary["residual_total"], 0)
        for path in sorted(self.out_dir.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            self.assertEqual(
                ai.find_residual_placeholders(text, strict=True),
                [],
                "填写副本存在占位符残留：%s" % path.name,
            )

    def test_no_bare_braces(self):
        """裸花括号残留数必须为 0（§9.2）。"""
        self.assertEqual(self.summary["bare_brace_total"], 0)
        for path in sorted(self.out_dir.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            self.assertEqual(
                ai.count_bare_braces(text), 0, "填写副本存在裸花括号：%s" % path.name
            )

    def test_missing_placeholders_explicitly_listed(self):
        """未提供的占位符必须显式列出。"""
        self.assertGreater(self.summary["missing_placeholder_total"], 0)
        listed = []
        for row in self.summary["families"]:
            listed.extend(row["missing_placeholders"])
        self.assertTrue(listed)
        dict_md = (self.tmp / "placeholder-dictionary.md").read_text(encoding="utf-8")
        self.assertIn("未提供值的占位符清单", dict_md)
        for token in listed[:5]:
            self.assertIn(token, dict_md)

    def test_unfilled_marker_used(self):
        """未提供的占位符填「（待填）」，不得静默消失。"""
        marker = self.summary["unfilled_marker"]
        hits = 0
        for path in sorted(self.out_dir.glob("*.md")):
            hits += path.read_text(encoding="utf-8").count(marker)
        self.assertGreater(hits, 0)

    def test_summary_json_fields(self):
        """摘要字段齐全。"""
        summary = json.loads(
            (self.out_dir / "_build-summary.json").read_text(encoding="utf-8")
        )
        for key in (
            "schema_version",
            "generated_by",
            "values_source",
            "family_count",
            "families",
            "residual_total",
            "bare_brace_total",
            "missing_placeholder_total",
        ):
            self.assertIn(key, summary)
        for row in summary["families"]:
            for key in ("family_id", "markdown", "draft", "missing_placeholders"):
                self.assertIn(key, row)

    def test_draft_records_sections(self):
        """底稿记录区块结构，便于下游回溯。"""
        drafts = sorted(self.out_dir.glob("F*.json"))
        self.assertEqual(len(drafts), 12)
        data = json.loads(drafts[0].read_text(encoding="utf-8"))
        self.assertIn("template_family", data)
        self.assertIn("sections", data)
        self.assertTrue(data["sections"])

    def test_selftest_passes(self):
        """自检入口返回 0。"""
        self.assertEqual(build_forms.selftest(), 0)


if __name__ == "__main__":
    unittest.main()
