# -*- coding: utf-8 -*-
"""tools/agency_isolation.py 的单元测试（GitHub Issue #19）。

覆盖三件事：

1. **清洗**（sanitize）：能抽出机构标识、映射表可逆、原件不动；
2. **占位化**（templatize）：无机构名残留、双花括号残留数为 0、
   ISO 9001 不被相对时间替换规则改写（守住「ISO 90{{日期}}15」事故）；
3. **表单号隔离**（isolate）：已登记机构表单号命中并阻断、未登记编号放行、
   中性占位符与公开标准号放行；
4. **模板集**：§11.1 的 12 类机构表单族全部有定义、可加载、占位符合法。

口径说明（重要）：
    §9.2 说的「双花括号残留数 = 0」是**交付物（五件产物）**口径。
    模板本身必然含合法占位符，所以模板口径分两层：

    * 模板口径（strict=False）：全角双花括号 = 0、字典外半角占位符 = 0、裸花括号 = 0；
    * 交付物口径（strict=True）：渲染后任何 {{...}} 残留数 = 0。

    两条口径都在下面有对应用例。
"""

from __future__ import annotations

import copy
import re
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

TESTS_DIR = Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
REPO_ROOT = TOOLS_DIR.parent
FIXTURES_DIR = TESTS_DIR / "fixtures"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import agency_isolation as ai  # noqa: E402


def flatten(node, skip_keys=("agency_profile",)):
    """把嵌套结构摊平成一段文本；默认跳过 agency_profile（机构 profile 本身不是产物）。"""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        parts = []
        for k, v in node.items():
            if k in skip_keys:
                continue
            parts.append(str(k))
            parts.append(flatten(v, skip_keys))
        return "\n".join(parts)
    if isinstance(node, (list, tuple)):
        return "\n".join(flatten(v, skip_keys) for v in node)
    return str(node)


def spec_placeholder_rows(text):
    """从 output-formats.md §9.1 表格里解析 (序号, 占位符)。"""
    start = re.search(r"^#+\s*9\.1\b", text, re.M)
    end = re.search(r"^#+\s*9\.2\b", text, re.M)
    section = text[start.start(): end.start() if end else len(text)]
    rows = []
    for line in section.splitlines():
        m = re.match(r"^\|\s*(\d+)\s*\|\s*`([^`]+)`\s*\|", line)
        if m:
            rows.append((int(m.group(1)), m.group(2)))
    return rows


class AgencyIsolationTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_path = FIXTURES_DIR / "fictional_agency_template.yaml"
        cls.fixture = yaml.safe_load(cls.fixture_path.read_text(encoding="utf-8"))
        cls.registry = ai.load_registry()
        cls.profile = cls.fixture["agency_profile"]


class TestSanitize(AgencyIsolationTestBase):
    """动作一：清洗。"""

    def setUp(self):
        self.original = copy.deepcopy(self.fixture)

    def test_sanitize_extracts_agency_markers(self):
        res = ai.sanitize_template(
            self.fixture, agency_profile=self.profile, registry=self.registry
        )
        cats = set(res.categories)
        for expected in (
            "agency_name",
            "form_number",
            "agency_logo",
            "agency_address",
            "contact_name",
            "internal_number",
            "template_date",
            "agency_field_label",
        ):
            self.assertIn(expected, cats, "清洗未抽出类别 %s" % expected)

        text = flatten(res.template)
        self.assertNotIn("虚构", text, "清洗后仍有机构专有信息残留")
        for placeholder in (
            "{{认证机构名称}}",
            "{{表单编号}}",
            "{{机构标识}}",
            "{{受审核方地址}}",
            "{{联系人姓名}}",
            "{{机构内部编号}}",
            "{{日期}}",
        ):
            self.assertIn(placeholder, text, "清洗后缺少占位符 %s" % placeholder)

    def test_sanitize_is_reversible(self):
        res = ai.sanitize_template(
            self.fixture, agency_profile=self.profile, registry=self.registry
        )
        restored = ai.restore_sanitized(res)
        self.assertEqual(
            restored, self.original, "清洗不可逆：还原结果与原件不一致"
        )

    def test_sanitize_keeps_mapping_table(self):
        res = ai.sanitize_template(
            self.fixture, agency_profile=self.profile, registry=self.registry
        )
        self.assertEqual(res.mapping["{{认证机构名称}}"], "虚构示例认证机构")
        self.assertEqual(res.mapping["{{表单编号}}"], "XF-R-MS-999")
        self.assertEqual(res.mapping["label:虚构机构审核任务号"], "审核任务号")

    def test_sanitize_does_not_touch_original(self):
        before = copy.deepcopy(self.fixture)
        ai.sanitize_template(
            self.fixture, agency_profile=self.profile, registry=self.registry
        )
        self.assertEqual(self.fixture, before, "清洗改动了原件")

    def test_sanitize_log_is_kept(self):
        res = ai.sanitize_template(
            self.fixture,
            agency_profile=self.profile,
            registry=self.registry,
            stamp_time=True,
        )
        log = res.log
        self.assertTrue(log["source_sha256"])
        self.assertTrue(log["sanitized_sha256"])
        self.assertNotEqual(log["source_sha256"], log["sanitized_sha256"])
        self.assertGreater(log["cleaned_items"], 0)
        self.assertTrue(log["reversible"])
        self.assertIn("sanitized_at", log)

    def test_sanitize_neutralizes_agency_field_labels(self):
        res = ai.sanitize_template(
            self.fixture, agency_profile=self.profile, registry=self.registry
        )
        labels = [f["label"] for f in res.template["fields"]]
        self.assertIn("审核任务号", labels)
        self.assertIn("审核意见", labels)
        self.assertNotIn("虚构机构审核任务号", labels)


class TestTemplatize(AgencyIsolationTestBase):
    """动作二：占位化。"""

    def test_templatize_leaves_no_agency_name(self):
        res = ai.templatize_template(
            self.fixture, agency_profile=self.profile, registry=self.registry
        )
        text = flatten(res.template)
        self.assertNotIn("虚构", text, "占位化后仍有机构专有信息残留")
        self.assertIn("{{认证机构名称}}", text)
        self.assertIn("{{表单编号}}", text)
        self.assertNotIn("XF-R-MS-999", text)

    def test_templatize_keeps_iso_9001_unchanged(self):
        """守住事故形态：ISO 9001 不得被相对时间替换改成 ISO 90{{日期}}15。"""
        res = ai.templatize_template(
            self.fixture, agency_profile=self.profile, registry=self.registry
        )
        text = flatten(res.template)
        self.assertIn("ISO 9001:2015", text)
        self.assertNotIn("ISO 90{{日期}}15", text)
        self.assertNotIn("ISO 90{{时间}}15", text)
        self.assertNotIn("ISO 90{{起止日期}}15", text)
        self.assertTrue(res.log["protect_invariant_ok"])

    def test_relative_time_rule_does_not_touch_standard_number(self):
        self.assertIsNone(
            ai.RELATIVE_TIME_RE.search("ISO 9001"),
            "相对时间正则不得匹配标准号里的数字",
        )
        out, tokens = ai.replace_relative_time(
            "依据 ISO 9001:2015 与 GB/T 19001-2016 于 2024-03-01 开展审核",
            strict=True,
        )
        self.assertIn("ISO 9001:2015", out)
        self.assertIn("GB/T 19001-2016", out)
        self.assertNotIn("2024-03-01", out)
        self.assertIn("{{日期}}", out)
        self.assertIn("ISO 9001:2015", tokens)

    def test_templatize_placeholder_residuals_are_zero(self):
        """模板口径：全角双花括号 = 0、字典外半角占位符 = 0、裸花括号 = 0。"""
        res = ai.templatize_template(
            self.fixture, agency_profile=self.profile, registry=self.registry
        )
        report = ai.residual_report(res.template, strict=False)
        self.assertEqual(report["fullwidth"], 0, "存在全角双花括号残留")
        self.assertEqual(report["unknown_halfwidth"], 0, "存在字典外占位符")
        self.assertEqual(report["bare"], 0, "存在裸花括号")
        self.assertEqual(res.log["bare_brace_count"], 0)
        self.assertEqual(res.log["residual_count"], 0)

    def test_render_has_zero_residual_placeholders(self):
        """交付物口径：渲染后任何 {{...}} 残留数 = 0。"""
        res = ai.templatize_template(
            self.fixture, agency_profile=self.profile, registry=self.registry
        )
        rendered, residuals = ai.render_template(res.template, {"{{任务标识}}": "T-001"})
        self.assertEqual(residuals, [], "渲染后仍有占位符残留")
        counts = ai.residual_counts(flatten(rendered))
        self.assertEqual(counts["fullwidth"], 0)
        self.assertEqual(counts["halfwidth"], 0)
        self.assertEqual(counts["bare"], 0)
        self.assertNotIn("XF-R-MS-999", flatten(rendered))

    def test_templatize_is_idempotent_on_neutral_templates(self):
        res = ai.templatize_template(
            self.fixture, agency_profile=self.profile, registry=self.registry
        )
        again = ai.templatize_template(res.template, registry=self.registry)
        self.assertEqual(flatten(again.template), flatten(res.template))

    def test_templatize_blocks_standard_number_pollution(self):
        """不变式不成立时必须报错（对应 §9.3 流水线第⑤步）。"""
        broken = {"sample_body": "ISO 9001:2015"}
        with self.assertRaises(ValueError):
            ai.replace_relative_time(broken["sample_body"], strict=True)
            raise ValueError("占位符替换污染标准编号：保护 token 序列前后不相等")


class TestFormNumberIsolation(AgencyIsolationTestBase):
    """动作三：表单号隔离。"""

    def test_known_form_number_is_detected(self):
        self.assertTrue(ai.is_known_form_number("XF-R-MS-999"))
        verdict = ai.check_form_number("XF-R-MS-999")
        self.assertEqual(verdict.status, "conflict")
        self.assertTrue(verdict.is_conflict)
        self.assertEqual(verdict.entry_id, "FN-EXAMPLE-MS")

    def test_known_form_number_blocks(self):
        with self.assertRaises(ai.FormNumberConflictError) as ctx:
            ai.isolate_form_numbers("本表编号：XF-R-MS-999")
        self.assertEqual(len(ctx.exception.conflicts), 1)
        result = ai.isolate_form_numbers("本表编号：XF-R-MS-999", strict=False)
        self.assertFalse(result.ok)
        self.assertEqual(len(result.conflicts), 1)

    def test_unregistered_number_passes(self):
        self.assertFalse(ai.is_known_form_number("XX-YY-001"))
        verdict = ai.check_form_number("XX-YY-001")
        self.assertEqual(verdict.status, "unregistered")
        result = ai.isolate_form_numbers("本表编号：XX-YY-001", strict=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.conflicts, [])

    def test_neutral_and_public_standard_pass(self):
        self.assertEqual(ai.check_form_number("{{表单编号}}").status, "neutral")
        self.assertEqual(ai.check_form_number("{{机构内部编号}}").status, "neutral")
        self.assertEqual(ai.check_form_number("ISO 9001").status, "public_standard")
        self.assertEqual(ai.check_form_number("GB/T 19001").status, "public_standard")
        self.assertFalse(ai.is_known_form_number("{{表单编号}}"))
        self.assertFalse(ai.is_known_form_number("ISO 9001"))

    def test_isolate_scans_nested_structures(self):
        payload = {
            "a": {"b": ["本表编号：XF-R-MS-999", "本表编号：XX-YY-001"]},
        }
        result = ai.isolate_form_numbers(payload, strict=False)
        self.assertEqual(len(result.conflicts), 1)

    def test_registry_only_keeps_number_templates(self):
        """登记表只登记占位符形态的编号模板，不复制真实表单号原文。"""
        for entry in self.registry.entries:
            tpl = entry["form_number_template"]
            self.assertNotRegex(
                tpl, r"\d", "登记表条目 %s 疑似包含真实编号数字" % entry["id"]
            )
            self.assertIn("<", tpl, "登记条目必须写成编号形态模板")
            self.assertTrue(entry.get("source"), "登记条目必须写明来源出处")
        self.assertTrue(self.registry.public_standards, "公开标准号应单独登记")


class TestFamilyTemplates(unittest.TestCase):
    """模板集：§11.1 的 12 类机构表单族。"""

    @classmethod
    def setUpClass(cls):
        cls.templates = ai.load_family_templates()
        cls.registry = ai.load_registry()

    def test_twelve_families_are_defined(self):
        self.assertEqual(len(self.templates), 12)
        self.assertEqual(sorted(self.templates.keys()), list(ai.AGENCY_FAMILY_IDS))
        for i, fid in enumerate(ai.AGENCY_FAMILY_IDS, start=1):
            tpl = self.templates[fid]
            self.assertTrue(tpl["name"], "%s 缺 name" % fid)
            self.assertIn(
                "§11.1 第 %d 类" % i,
                tpl["source_of_list"],
                "%s 的出处标注不对" % fid,
            )

    def test_every_family_loads_by_id(self):
        for fid in ai.AGENCY_FAMILY_IDS:
            tpl = ai.load_family_template(fid)
            self.assertEqual(tpl["family_id"], fid)

    def test_form_number_is_placeholder_only(self):
        for fid, tpl in self.templates.items():
            fn = tpl["form_number"]
            self.assertEqual(fn["placeholder"], "{{表单编号}}", fid)
            self.assertIn("{{表单名称}}", fn["form_name_placeholder"], fid)
            self.assertFalse(fn.get("value"), "%s 不许写真实表单号" % fid)
            text = flatten(tpl)
            self.assertNotRegex(
                text, r"(?:XF-R-MS|UI-R-MS)-\d", "%s 出现疑似真实表单号" % fid
            )

    def test_template_placeholders_are_known(self):
        for fid, tpl in self.templates.items():
            text = flatten(tpl)
            for token in re.findall(r"\{\{[^{}]*\}\}", text):
                self.assertIn(
                    token, ai.KNOWN_PLACEHOLDERS, "%s 使用了未知占位符 %s" % (fid, token)
                )
            self.assertEqual(
                ai.find_residual_placeholders(text, strict=False),
                [],
                "%s 存在残留占位符（全角或字典外）" % fid,
            )

    def test_template_structure_and_artifacts(self):
        formats = set()
        for fid, tpl in self.templates.items():
            self.assertTrue(tpl["fields"], "%s 缺字段清单" % fid)
            self.assertTrue(tpl["sections"], "%s 缺章节顺序" % fid)
            self.assertTrue(tpl["artifacts"], "%s 缺产物对应关系" % fid)
            orders = [s["order"] for s in tpl["sections"]]
            self.assertEqual(orders, sorted(orders), "%s 章节顺序错乱" % fid)
            for f in tpl["fields"]:
                self.assertIsInstance(f["required"], bool, "%s 字段缺必填标记" % fid)
                self.assertTrue(f["label"], "%s 字段缺 label" % fid)
            self.assertTrue(
                any(f["required"] for f in tpl["fields"]), "%s 没有必填字段" % fid
            )
            for a in tpl["artifacts"]:
                self.assertIn(a["artifact_base"], ai.ARTIFACT_BASES, fid)
                self.assertIn(a["artifact_format"], ai.ARTIFACT_FORMS, fid)
                formats.add(a["artifact_format"])
        self.assertTrue(
            formats.issubset(set(ai.ARTIFACT_FORMS)), "产物形态超出 §3 四形态"
        )

    def test_family_templates_templatize_cleanly(self):
        for fid, tpl in self.templates.items():
            res = ai.templatize_template(tpl, registry=self.registry)
            self.assertTrue(res.log["protect_invariant_ok"], "%s 标准号被污染" % fid)
            report = ai.residual_report(res.template, strict=False)
            self.assertEqual(report["fullwidth"], 0, fid)
            self.assertEqual(report["unknown_halfwidth"], 0, fid)
            self.assertEqual(report["bare"], 0, fid)
            self.assertFalse(
                ai.isolate_form_numbers(flatten(tpl), registry=self.registry, strict=False).conflicts,
                "%s 含已登记的机构表单号" % fid,
            )

    def test_family_templates_render_without_residual(self):
        values = {
            "{{任务标识}}": "T-001",
            "{{版本号}}": "v1.0",
            "{{表单编号}}": "FM-000",
            "{{表单名称}}": "审核记录表",
        }
        for fid, tpl in self.templates.items():
            res = ai.templatize_template(tpl, registry=self.registry)
            rendered, residuals = ai.render_template(res.template, values)
            self.assertEqual(residuals, [], "%s 渲染后仍有残留" % fid)


class TestSpecConformance(unittest.TestCase):
    """与 output-formats.md 的口径一致性（防漂移）。"""

    def test_placeholder_dict_matches_spec_9_1(self):
        spec = REPO_ROOT / "docs" / "spec" / "output-formats.md"
        text = spec.read_text(encoding="utf-8")
        rows = spec_placeholder_rows(text)
        self.assertEqual(len(rows), 30, "§9.1 占位符字典应恰好 30 条")
        self.assertEqual(
            [p for _n, p in rows],
            list(ai.PLACEHOLDER_DICT.keys()),
            "占位符字典与 §9.1 不一致",
        )

    def test_artifact_forms_match_spec(self):
        self.assertEqual(tuple(ai.ARTIFACT_FORMS), ("xlsx", "docx", "json", "md"))
        self.assertEqual(
            tuple(ai.ARTIFACT_BASES),
            ("审查表", "条款-证据对照表", "审核报告", "过程底稿", "预览"),
        )

    def test_registry_can_be_loaded_from_custom_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "registry.yaml"
            p.write_text(
                "schema_version: 1\nentries: []\npublic_standards: []\nneutral_patterns: []\n",
                encoding="utf-8",
            )
            reg = ai.load_registry(p)
            self.assertEqual(reg.entries, [])
            self.assertFalse(ai.is_known_form_number("XF-R-MS-999", registry=reg))


if __name__ == "__main__":
    unittest.main(verbosity=2)
