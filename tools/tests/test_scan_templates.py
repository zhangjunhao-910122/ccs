#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/scan_templates.py 的测试（GitHub Issue #19）。

纪律：
  * 机构中立模板集必须 0 缺陷；
  * 机构名、表单号、真实日期、相对时限必须被抓出来；
  * 标准号不得被误判（ISO 9001:2015 干净，ISO 90{{时间}}15 是误伤）。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import scan_templates as st  # noqa: E402


def scan_text(text: str):
    """用默认机构差异配置与登记表扫一段文本。"""
    scanner = st.Scanner(
        profile=st.DEFAULT_PROFILE_PATH,
        registry=st.DEFAULT_REGISTRY_PATH,
    )
    return scanner.scan_text(text, Path("sample.md"))


def categories(text: str):
    return [f.category for f in scan_text(text)]


class TestScanner(unittest.TestCase):
    """扫描器行为。"""

    def test_selftest_passes(self):
        """内置自检通过。"""
        self.assertEqual(st.selftest(), 0)

    def test_template_family_set_is_clean(self):
        """12 类模板族本身 0 缺陷。"""
        scanner = st.Scanner(
            profile=st.DEFAULT_PROFILE_PATH,
            registry=st.DEFAULT_REGISTRY_PATH,
        )
        result = scanner.scan_paths([st.DEFAULT_TARGET_DIR])
        self.assertEqual(result.defect_total(), 0, result.findings)

    def test_agency_name_is_detected(self):
        """机构名硬编码被抓出来。"""
        self.assertIn(st.CATEGORY_AGENCY_NAME, categories("本表由示例认证机构出具。"))

    def test_agency_form_number_is_detected(self):
        """机构表单号被抓出来。"""
        self.assertIn(
            st.CATEGORY_AGENCY_FORM_NUMBER, categories("表单编号：XF-R-MS-001")
        )

    def test_residual_real_date_is_detected(self):
        """残留真实日期被抓出来。"""
        self.assertIn(
            st.CATEGORY_RESIDUAL_REAL_DATE, categories("签署日期：2026-11-05")
        )

    def test_internal_deadline_is_detected(self):
        """机构内部相对时限口径被抓出来。"""
        self.assertIn(
            st.CATEGORY_INTERNAL_DEADLINE,
            categories("整改资料提交期限：整改资料应在 15 个工作日内提交。"),
        )

    def test_cleaning_damage_is_detected(self):
        """标准号被占位符切断被判为清洗误伤。"""
        self.assertIn(
            st.CATEGORY_CLEANING_DAMAGE, categories("审核依据 ISO 90{{时间}}15")
        )

    def test_standard_number_is_not_flagged(self):
        """干净的标准号不产生误伤，也不算残留日期。"""
        cats = categories("审核依据：GB/T 19001-2016，ISO 9001:2015，CNCA-QMS-01-2025。")
        self.assertNotIn(st.CATEGORY_CLEANING_DAMAGE, cats)
        self.assertNotIn(st.CATEGORY_RESIDUAL_REAL_DATE, cats)

    def test_placeholder_in_template_is_not_residual(self):
        """模板里的合法占位符不算残留。"""
        cats = categories("任务标识：{{任务标识}}　受审核方：{{受审核方名称}}")
        self.assertNotIn(st.CATEGORY_RESIDUAL_PLACEHOLDER, cats)


class TestProfileSwitching(unittest.TestCase):
    """换机构差异配置后，模板集仍然机构中立。"""

    def test_both_example_profiles_keep_templates_clean(self):
        profiles = st.TEMPLATES_DIR / "agency_profiles"
        for profile in sorted(profiles.glob("*.yaml")):
            scanner = st.Scanner(
                profile=profile,
                registry=st.DEFAULT_REGISTRY_PATH,
            )
            result = scanner.scan_paths([st.DEFAULT_TARGET_DIR])
            self.assertEqual(
                result.defect_total(),
                0,
                "配置 %s 下模板集出现缺陷：%s" % (profile.name, result.findings),
            )


if __name__ == "__main__":
    unittest.main()
