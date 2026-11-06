"""tools/importer.py 的 unittest 测试。

覆盖范围（对应 GitHub Issue #7 验收要求）：
  * 导入计数正确：手册 / 程序文件 / XLSX / DOCX 各文件的要素条数与要素类型
  * 幂等：同一份材料重复导入不新增要素；元素指纹命中时只复用不新增
  * 条款号越界（如 9.9.9）被校验报告捕获（CLAUSE_NOT_IN_TREE）
  * 不可读文件（扫描件 PDF、乱码 TXT）归入「不可读」问题类
  * 缺件文件归入「缺件」问题类
  * 同一材料内互斥陈述归入「冲突」问题类
  * 要素字段齐全、摘录不超长、定位具体
  * 校验报告与要素清单落盘命名符合 output-formats.md §2.2

所有临时产物（状态文件、报告、CSV）都写进 tempfile 临时目录，不污染仓库。
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import importer  # noqa: E402

FIXTURES = TOOLS_DIR / "fixtures"
MANUAL = FIXTURES / "quality_manual_A1.md"
PROCEDURE = FIXTURES / "procedure_traceability_A0.md"
REGISTER = FIXTURES / "process_site_register.xlsx"
AUDIT_DOCX = FIXTURES / "internal_audit_report_2026Q2.docx"
SCAN_PDF = FIXTURES / "supplier_cert_scan.pdf"
GARBLED = FIXTURES / "garbled_scan.txt"


class ImporterTestBase(unittest.TestCase):
    """每个用例一套独立的临时目录与导入状态。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsh_importer_test_")
        self.tmpdir = Path(self.tmp)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def make_state(self, name="state.json"):
        return importer.ImportState(self.tmpdir / name)

    def run_import(self, paths, state=None, expected=None):
        state = state or self.make_state()
        imp = importer.Importer(REPO_ROOT, state)
        return imp.import_paths([Path(p) for p in paths], expected=expected)


class TestImportCounts(ImporterTestBase):
    def test_manual_import_counts(self):
        """体系手册：13 条要素，条款号 6 个，类型覆盖要求/过程/记录/场所/设备。"""
        result = self.run_import([MANUAL])
        self.assertEqual(result.stats["files_seen"], 1)
        self.assertEqual(result.stats["files_imported"], 1)
        self.assertEqual(result.stats["files_failed"], 0)
        self.assertEqual(len(result.elements), 13)
        self.assertEqual(result.stats["elements_new"], 13)

        clauses = sorted({e["clause_no"] for e in result.elements if e["clause_no"]})
        self.assertEqual(clauses, ["10.2", "4.4", "7.5.3", "8.1", "8.5.2", "9.1"])

        types = {}
        for element in result.elements:
            types[element["element_type"]] = types.get(element["element_type"], 0) + 1
        self.assertEqual(types["process"], 1)
        self.assertEqual(types["site"], 1)
        self.assertEqual(types["equipment"], 1)
        self.assertEqual(types["record"], 1)

    def test_element_ids_are_sequential(self):
        result = self.run_import([MANUAL])
        self.assertEqual([e["element_id"] for e in result.elements[:3]],
                         ["E-0001", "E-0002", "E-0003"])

    def test_procedure_import_counts(self):
        """程序文件：6 条要素，同一条款 8.5.2 两处陈述 → 冲突问题。"""
        result = self.run_import([PROCEDURE])
        self.assertEqual(len(result.elements), 6)
        conflicts = [p for p in result.problems if p["problem_class"] == "conflict"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["reason_code"], "EVIDENCE_CONFLICT")
        self.assertIn("8.5.2", conflicts[0]["subject"])

    def test_xlsx_import_counts_and_locator(self):
        """XLSX：9 条要素，行级定位（登记表!A3:E3）与单元格定位并存。"""
        result = self.run_import([REGISTER])
        self.assertEqual(len(result.elements), 9)
        locators = {e["locator"] for e in result.elements}
        self.assertIn("登记表!A3:E3", locators)
        self.assertIn("登记表!A8:E8", locators)
        self.assertIn("单值表!A2", locators)

    def test_docx_import_counts_and_locator(self):
        """DOCX：11 条要素，段落定位 p.N 与表格行定位 t1 rN 并存。"""
        result = self.run_import([AUDIT_DOCX])
        self.assertEqual(len(result.elements), 11)
        locators = {e["locator"] for e in result.elements}
        self.assertIn("p.6", locators)
        self.assertIn("t1 r2", locators)


class TestIdempotency(ImporterTestBase):
    def test_reimport_same_file_adds_no_element(self):
        """同一份材料重复导入：不新增要素，文件级 sha256 命中跳过。"""
        state = self.make_state()
        first = self.run_import([MANUAL], state=state)
        self.assertEqual(len(first.elements), 13)

        second = self.run_import([MANUAL], state=state)
        self.assertEqual(second.stats["files_skipped_duplicate"], 1)
        self.assertEqual(second.stats["files_imported"], 0)
        self.assertEqual(second.stats["elements_new"], 0)
        self.assertEqual(len(second.elements), 13)

    def test_reimport_same_content_different_path_adds_no_element(self):
        """内容相同、文件名不同：仍按 sha256 判重复，不新增要素。"""
        copy_path = self.tmpdir / "手册副本.md"
        shutil.copyfile(MANUAL, copy_path)

        state = self.make_state()
        first = self.run_import([MANUAL], state=state)
        second = self.run_import([copy_path], state=state)
        self.assertEqual(second.stats["files_skipped_duplicate"], 1)
        self.assertEqual(second.stats["elements_new"], 0)
        self.assertEqual(len(second.elements), len(first.elements))

    def test_element_fingerprint_reuse_when_document_record_lost(self):
        """元素指纹命中：材料记录丢失但要素仍在时，重导入只复用不新增。"""
        state_a = self.make_state("a.json")
        first = self.run_import([MANUAL], state=state_a)
        self.assertEqual(len(first.elements), 13)

        # 模拟状态文件只丢了 documents 段、要素仍在的情况
        raw = json.loads((self.tmpdir / "a.json").read_text(encoding="utf-8"))
        raw["documents"] = {}
        raw["doc_counter"] = 0
        (self.tmpdir / "b.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8")

        state_b = importer.ImportState(self.tmpdir / "b.json")
        second = self.run_import([MANUAL], state=state_b)
        self.assertEqual(second.stats["elements_new"], 0)
        self.assertGreater(second.stats["elements_reused"], 0)
        self.assertEqual(len(second.elements), 13)

    def test_element_fingerprint_is_deterministic(self):
        a = importer.element_fingerprint("D-0001", "L11", "原文摘录")
        b = importer.element_fingerprint("D-0001", "L11", "原文摘录")
        c = importer.element_fingerprint("D-0001", "L12", "原文摘录")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


class TestValidationProblems(ImporterTestBase):
    def test_clause_not_in_tree_is_captured(self):
        """条款号 9.9.9 不在条款树 → 校验报告捕获 CLAUSE_NOT_IN_TREE。"""
        result = self.run_import([REGISTER])
        problems = [p for p in result.problems if p["reason_code"] == "CLAUSE_NOT_IN_TREE"]
        self.assertEqual(len(problems), 1)
        problem = problems[0]
        self.assertEqual(problem["problem_class"], "pending_check")
        self.assertIn("9.9.9", problem["detail"])
        self.assertTrue(problem["element_ids"])

        # 该要素本身仍被导入，只是不给 current 依据
        bad = [e for e in result.elements if e["clause_no"] == "9.9.9"]
        self.assertEqual(len(bad), 1)

    def test_unreadable_pdf_is_unreadable_problem(self):
        """扫描件 PDF（无文本层）→ 「不可读」问题类，不产生要素。"""
        result = self.run_import([SCAN_PDF])
        self.assertEqual(result.stats["files_failed"], 1)
        self.assertEqual(result.stats["files_imported"], 0)
        self.assertEqual(result.elements, [])
        problems = [p for p in result.problems if p["problem_class"] == "unreadable"]
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["reason_code"], "EVIDENCE_UNREADABLE")

    def test_unreadable_garbled_text_is_unreadable_problem(self):
        """乱码 TXT → 「不可读」问题类。"""
        result = self.run_import([GARBLED])
        self.assertEqual(result.stats["files_failed"], 1)
        self.assertEqual(result.elements, [])
        problems = [p for p in result.problems if p["problem_class"] == "unreadable"]
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["reason_code"], "EVIDENCE_UNREADABLE")

    def test_missing_file_is_missing_problem(self):
        """文件不存在 → 「缺件」问题类。"""
        result = self.run_import([self.tmpdir / "根本没交的材料.pdf"])
        problems = [p for p in result.problems if p["problem_class"] == "missing"]
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["reason_code"], "EVIDENCE_MISSING")

    def test_expected_but_absent_is_missing_problem(self):
        """expected 清单里的材料未提交 → 「缺件」问题类。"""
        result = self.run_import([], expected=[self.tmpdir / "未提交的材料.pdf"])
        problems = [p for p in result.problems if p["problem_class"] == "missing"]
        self.assertEqual(len(problems), 1)

    def test_problem_counts_cover_four_classes(self):
        """问题分类固定为缺件/不可读/待核/冲突四类。"""
        result = self.run_import([MANUAL, SCAN_PDF, self.tmpdir / "缺失.pdf"])
        counts = result.problem_counts()
        self.assertEqual(set(counts), set(importer.PROBLEM_CLASSES))
        self.assertEqual(counts["unreadable"], 1)
        self.assertEqual(counts["missing"], 1)
        self.assertGreater(counts["pending_check"], 0)
        self.assertEqual(counts["conflict"], 0)


class TestElementFields(ImporterTestBase):
    REQUIRED = (
        "element_id", "source_doc_id", "source_ref", "locator", "locator_kind",
        "quote", "element_type", "element_type_label", "clause_no",
        "applicability", "applicability_label", "material_version", "tags",
        "fingerprint", "doc_sha256", "imported_at",
    )

    def test_all_required_fields_present(self):
        result = self.run_import([MANUAL])
        for element in result.elements:
            for field in self.REQUIRED:
                self.assertIn(field, element, f"{element.get('element_id')} 缺字段 {field}")

    def test_quote_is_truncated_to_200_chars(self):
        long_line = "8.5.2 " + ("很长的要求描述内容。" * 60)
        path = self.tmpdir / "长行.md"
        path.write_text(long_line + "\n", encoding="utf-8")
        result = self.run_import([path])
        self.assertTrue(result.elements)
        for element in result.elements:
            self.assertLessEqual(len(element["quote"]), importer.MAX_QUOTE_CHARS)

    def test_locator_is_specific(self):
        """定位必须具体到行/页/单元格，不允许只写文件名。"""
        result = self.run_import([MANUAL, REGISTER, AUDIT_DOCX])
        for element in result.elements:
            self.assertTrue(element["locator"])
            self.assertNotEqual(element["locator"], element["source_ref"])
            self.assertIn(
                element["locator_kind"],
                ("page", "section", "table_row", "cell", "paragraph", "timestamp"),
            )

    def test_applicability_never_auto_na(self):
        """红线：导入阶段不得把未知改标为不适用。"""
        result = self.run_import([MANUAL, PROCEDURE, REGISTER, AUDIT_DOCX])
        for element in result.elements:
            self.assertIn(element["applicability"],
                          ("applicable", "not_applicable", "unknown"))
            self.assertEqual(element["applicability"], "unknown")

    def test_material_version_detected(self):
        result = self.run_import([MANUAL])
        versions = {e["material_version"] for e in result.elements}
        self.assertIn("A/1.0", versions)


class TestOutputs(ImporterTestBase):
    def test_validation_report_files(self):
        result = self.run_import([REGISTER, SCAN_PDF])
        out = importer.write_validation_report(
            result, self.tmpdir, "T-001", "N1", state_name="waiting", version="v1")
        self.assertTrue(Path(out["json"]).is_file())
        self.assertTrue(Path(out["markdown"]).is_file())
        self.assertIn("资料导入校验报告", Path(out["json"]).name)
        self.assertIn("T-001", Path(out["json"]).name)
        self.assertIn("N1", Path(out["json"]).name)

        payload = json.loads(Path(out["json"]).read_text(encoding="utf-8"))
        self.assertEqual(payload["problem_counts"]["unreadable"], 1)
        self.assertTrue(any(p["reason_code"] == "CLAUSE_NOT_IN_TREE"
                            for p in payload["problems"]))
        md = Path(out["markdown"]).read_text(encoding="utf-8")
        self.assertIn("资料导入校验报告", md)
        self.assertIn("不可读", md)

    def test_elements_csv(self):
        result = self.run_import([MANUAL])
        path = importer.write_elements_csv(
            result.elements, self.tmpdir, "T-001", "N1", state_name="waiting", version="v1")
        self.assertTrue(Path(path).is_file())
        self.assertIn("要素清单", Path(path).name)
        text = Path(path).read_text(encoding="utf-8-sig")
        self.assertIn("element_id", text)
        self.assertIn("E-0001", text)

    def test_state_file_persisted_in_state_dir(self):
        state = self.make_state()
        self.run_import([MANUAL], state=state)
        self.assertTrue((self.tmpdir / "state.json").is_file())
        raw = json.loads((self.tmpdir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(len(raw["elements"]), 13)
        self.assertEqual(len(raw["element_order"]), 13)


if __name__ == "__main__":
    unittest.main()
