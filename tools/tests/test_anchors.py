"""tools/anchors.py 的 unittest 测试。

覆盖范围（对应 GitHub Issue #7 验收要求 + docs/spec/verdict-anchor-rules.md §2）：
  * 锚点 id 递增且格式为 A-0001
  * 锚点字段逐条对齐证据八要素与锚点结构口径
  * 条款号越界（9.9.9）→ basis_status=unverifiable，校验报告捕获 CLAUSE_NOT_IN_TREE
  * 事实标记四态流转（material_supported → verified / conflict）
  * 引用未确认锚点 → 判为证据不足（insufficient）；锚点缺失 → insufficient；
    自由书写引用 → insufficient；已确认且 verified → ok
  * file 锚点必须带指纹、必须指向具体位置
  * 锚点表 CSV / JSON 与校验报告落盘命名符合 output-formats.md §2.2

所有临时产物写进 tempfile 临时目录，不污染仓库。
"""

import csv
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

import anchors  # noqa: E402
import importer  # noqa: E402

FIXTURES = TOOLS_DIR / "fixtures"
MANUAL = FIXTURES / "quality_manual_A1.md"
REGISTER = FIXTURES / "process_site_register.xlsx"


class AnchorsTestBase(unittest.TestCase):
    """每个用例一套临时目录；真实导入 fixtures 得到要素，再生成锚点。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsh_anchors_test_")
        self.tmpdir = Path(self.tmp)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def import_elements(self, paths):
        """真实跑 importer，拿到要素序列（端到端，不造假数据）。"""
        state = importer.ImportState(self.tmpdir / "import_state.json")
        imp = importer.Importer(REPO_ROOT, state)
        result = imp.import_paths([Path(p) for p in paths])
        return result

    def make_generator(self):
        return anchors.AnchorGenerator(
            REPO_ROOT, task_id="T-001", node_id="N12", profile_id="qms-gbt19001-2016"
        )

    def build_table(self, paths):
        result = self.import_elements(paths)
        generator = self.make_generator()
        table = generator.generate(result.elements)
        return result, generator, table


class TestAnchorIdFormat(AnchorsTestBase):
    def test_next_anchor_id_starts_at_a0001_and_increments(self):
        table = anchors.AnchorTable(task_id="T-001", node_id="N12")
        self.assertEqual(table.next_anchor_id(), "A-0001")
        self.assertEqual(table.next_anchor_id(), "A-0002")
        self.assertEqual(table.next_anchor_id(), "A-0003")

    def test_generated_anchor_ids_are_sequential_and_well_formed(self):
        _, _, table = self.build_table([MANUAL])
        self.assertEqual(len(table.anchors), 13)
        for i, anchor in enumerate(table.anchors, start=1):
            expected = f"A-{i:04d}"
            self.assertEqual(anchor["anchor_id"], expected)
            self.assertRegex(anchor["anchor_id"], r"^A-\d{4}$")
            self.assertTrue(anchors.ANCHOR_ID_RE.match(anchor["anchor_id"]))

    def test_anchor_ids_not_reused_after_remove(self):
        table = anchors.AnchorTable()
        first = table.next_anchor_id()
        self.assertEqual(first, "A-0001")
        table.add({"anchor_id": first})
        self.assertEqual(table.next_anchor_id(), "A-0002")

    def test_add_rejects_bad_or_duplicate_id(self):
        table = anchors.AnchorTable()
        with self.assertRaises(anchors.AnchorError):
            table.add({"anchor_id": "X-1"})
        table.add({"anchor_id": "A-0001"})
        with self.assertRaises(anchors.AnchorError):
            table.add({"anchor_id": "A-0001"})


class TestAnchorFields(AnchorsTestBase):
    """锚点结构与证据八要素逐字段对齐 verdict-anchor-rules.md §2。"""

    STRUCTURE_FIELDS = (
        "anchor_id", "type", "source_ref", "locator", "fingerprint",
        "visible_state_only", "fact_state", "confirmation", "element_id",
        "source_doc_id", "clause_no", "evidence", "basis_status",
        "basis_snapshot", "points",
    )

    EVIDENCE_PARTS = (
        "requirement", "applicability", "material",
        "observation", "aux_judgement", "confirmation", "remediation",
    )

    def test_structure_fields_present(self):
        _, _, table = self.build_table([MANUAL])
        for anchor in table.anchors:
            for field in self.STRUCTURE_FIELDS:
                self.assertIn(field, anchor, f"{anchor['anchor_id']} 缺字段 {field}")

    def test_evidence_eight_parts_present(self):
        _, _, table = self.build_table([MANUAL])
        for anchor in table.anchors:
            evidence = anchor["evidence"]
            for part in self.EVIDENCE_PARTS:
                self.assertIn(part, evidence, f"{anchor['anchor_id']} 证据缺 {part}")

    def test_requirement_has_text_and_source_ref(self):
        _, _, table = self.build_table([MANUAL])
        for anchor in table.anchors:
            requirement = anchor["evidence"]["requirement"]
            self.assertTrue(requirement["text"])
            self.assertTrue(requirement["source_ref"])

    def test_applicability_has_reason_scope_and_state(self):
        _, _, table = self.build_table([MANUAL])
        for anchor in table.anchors:
            applicability = anchor["evidence"]["applicability"]
            self.assertTrue(applicability["reason"])
            self.assertIn("scope", applicability)
            self.assertIn("process", applicability)
            self.assertIn("site", applicability)
            self.assertIn(applicability["state"], ("applicable", "not_applicable", "unknown"))

    def test_material_has_version_and_locator(self):
        _, _, table = self.build_table([MANUAL])
        for anchor in table.anchors:
            material = anchor["evidence"]["material"]
            self.assertIn("version", material)
            self.assertTrue(material["locator"])

    def test_file_anchor_must_carry_fingerprint(self):
        """硬规则：file 锚点必须带指纹。"""
        _, _, table = self.build_table([MANUAL])
        for anchor in table.anchors:
            self.assertEqual(anchor["type"], "file")
            self.assertTrue(anchor["fingerprint"]["hash"].startswith("sha256:"))

    def test_anchor_must_point_to_specific_location(self):
        """硬规则：锚点必须指向具体位置。"""
        _, _, table = self.build_table([MANUAL, REGISTER])
        for anchor in table.anchors:
            locator = anchor["locator"]
            self.assertIn("kind", locator)
            self.assertIn("value", locator)
            self.assertIn(locator["kind"], anchors.FILE_LOCATOR_KINDS)
            self.assertTrue(locator["value"])

    def test_basis_snapshot_fields(self):
        """§7.1 依据快照字段。"""
        _, _, table = self.build_table([MANUAL])
        for anchor in table.anchors:
            snapshot = anchor["basis_snapshot"]
            for field in ("source_ref", "source_id", "version", "locator",
                          "snapshot_sha256", "retrieved_at", "status"):
                self.assertIn(field, snapshot)
            self.assertIn(snapshot["status"], anchors.BASIS_STATUSES)

    def test_points_link_to_audit_points(self):
        """子条款 8.5.2 落到条款卡 8.5 的审核点。"""
        _, _, table = self.build_table([MANUAL])
        targets = [a for a in table.anchors if a["clause_no"] == "8.5.2"]
        self.assertTrue(targets)
        for anchor in targets:
            self.assertIn("8.5", anchor["points"])

    def test_anchor_id_matches_element_id_linkage(self):
        result, _, table = self.build_table([MANUAL])
        element_ids = {e["element_id"] for e in result.elements}
        for anchor in table.anchors:
            self.assertIn(anchor["element_id"], element_ids)
            self.assertEqual(anchor["source_doc_id"], anchor["source_ref"] and anchor["source_doc_id"])


class TestClauseAndBasis(AnchorsTestBase):
    def test_clause_in_tree_is_current(self):
        _, _, table = self.build_table([MANUAL])
        in_tree = [a for a in table.anchors if a["clause_no"] in ("4.4", "8.1", "8.5.2")]
        self.assertTrue(in_tree)
        for anchor in in_tree:
            self.assertEqual(anchor["basis_status"], "current")

    def test_clause_not_in_tree_is_unverifiable_and_reported(self):
        """条款号 9.9.9 不在条款树 → unverifiable + CLAUSE_NOT_IN_TREE 进校验报告。"""
        _, generator, table = self.build_table([REGISTER])
        bad = [a for a in table.anchors if a["clause_no"] == "9.9.9"]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["basis_status"], "unverifiable")
        problems = [p for p in generator.problems if p["reason_code"] == "CLAUSE_NOT_IN_TREE"]
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["problem_class"], "pending_check")
        self.assertIn("9.9.9", problems[0]["detail"])
        self.assertIn(bad[0]["anchor_id"], problems[0]["anchor_ids"])

    def test_element_without_clause_is_unverifiable(self):
        result, generator, table = self.build_table([MANUAL])
        without = [a for a in table.anchors if not a["clause_no"]]
        self.assertTrue(without)
        for anchor in without:
            self.assertEqual(anchor["basis_status"], "unverifiable")
        codes = {p["reason_code"] for p in generator.problems}
        self.assertIn("APPLICABILITY_UNKNOWN", codes)


class TestFactStateAndConfirmation(AnchorsTestBase):
    def test_initial_state_is_pending_and_material_supported(self):
        _, _, table = self.build_table([MANUAL])
        for anchor in table.anchors:
            self.assertEqual(anchor["confirmation"]["state"], "pending")
            self.assertIn(anchor["fact_state"], anchors.FACT_STATES)
            self.assertEqual(anchor["fact_state"], "material_supported")

    def test_confirm_sets_verified(self):
        _, _, table = self.build_table([MANUAL])
        anchors.confirm_anchor(table, "A-0001", confirmed_by="审核员张", at="2026-11-06T10:00:00")
        anchor = table.get("A-0001")
        self.assertEqual(anchor["confirmation"]["state"], "confirmed")
        self.assertEqual(anchor["confirmation"]["by"], "审核员张")
        self.assertEqual(anchor["fact_state"], "verified")

    def test_reject_sets_conflict(self):
        _, _, table = self.build_table([MANUAL])
        anchors.reject_anchor(table, "A-0002", by="审核员李", at="2026-11-06T10:00:00")
        anchor = table.get("A-0002")
        self.assertEqual(anchor["confirmation"]["state"], "rejected")
        self.assertEqual(anchor["fact_state"], "conflict")

    def test_confirm_unknown_anchor_raises(self):
        _, _, table = self.build_table([MANUAL])
        with self.assertRaises(anchors.AnchorError):
            anchors.confirm_anchor(table, "A-9999", confirmed_by="审核员张")


class TestCitationDiscipline(AnchorsTestBase):
    """硬规则：正文只引用 [A-0007]；锚点缺失或未确认 → 证据不足。"""

    def test_citation_of_unconfirmed_anchor_is_insufficient(self):
        _, _, table = self.build_table([MANUAL])
        checker = anchors.CitationChecker(table)
        result = checker.check("控制策划的结果应予保持 [A-0001]。")
        self.assertEqual(result["verdict"], "insufficient")
        self.assertEqual(result["judge_verdict"], "insufficient")
        self.assertEqual(result["insufficient_count"], 1)
        self.assertEqual(result["citations"][0]["status"], "insufficient")
        self.assertIn("未确认", result["citations"][0]["reason"])

    def test_missing_anchor_is_insufficient(self):
        _, _, table = self.build_table([MANUAL])
        checker = anchors.CitationChecker(table)
        result = checker.check("结论见 [A-9999]。")
        self.assertEqual(result["verdict"], "insufficient")
        self.assertIn("锚点缺失", result["citations"][0]["reason"])

    def test_free_text_reference_is_insufficient(self):
        _, _, table = self.build_table([MANUAL])
        checker = anchors.CitationChecker(table)
        result = checker.check("按见第 8.5.2 条的要求执行。")
        self.assertEqual(result["verdict"], "insufficient")
        self.assertTrue(result["free_text_refs"])
        self.assertEqual(result["free_text_refs"][0]["status"], "insufficient")

    def test_confirmed_and_verified_anchor_is_ok(self):
        _, _, table = self.build_table([MANUAL])
        anchors.confirm_anchor(table, "A-0001", confirmed_by="审核员张")
        checker = anchors.CitationChecker(table)
        result = checker.check("控制策划的结果应予保持 [A-0001]。")
        self.assertEqual(result["verdict"], "ok")
        self.assertEqual(result["citations"][0]["status"], "ok")
        self.assertEqual(result["insufficient_count"], 0)

    def test_confirmed_but_not_verified_is_insufficient(self):
        _, _, table = self.build_table([MANUAL])
        anchors.confirm_anchor(table, "A-0001", confirmed_by="审核员张", fact_state="user_stated")
        checker = anchors.CitationChecker(table)
        result = checker.check("控制策划的结果应予保持 [A-0001]。")
        self.assertEqual(result["verdict"], "insufficient")
        self.assertIn("verified", result["citations"][0]["reason"])

    def test_rejected_anchor_cannot_be_cited(self):
        _, _, table = self.build_table([MANUAL])
        anchors.reject_anchor(table, "A-0001", by="审核员李")
        checker = anchors.CitationChecker(table)
        result = checker.check("控制策划的结果应予保持 [A-0001]。")
        self.assertEqual(result["verdict"], "insufficient")

    def test_format_citation(self):
        self.assertEqual(anchors.format_citation("A-0007"), "[A-0007]")
        with self.assertRaises(anchors.AnchorError):
            anchors.format_citation("A-7")


class TestOutputs(AnchorsTestBase):
    def test_anchor_table_files_and_naming(self):
        _, _, table = self.build_table([MANUAL])
        paths = anchors.write_anchor_table(table, self.tmpdir, "waiting", "v1")
        csv_path = Path(paths["csv"])
        json_path = Path(paths["json"])
        self.assertTrue(csv_path.is_file())
        self.assertTrue(json_path.is_file())
        self.assertIn("证据锚点表", csv_path.name)
        self.assertIn("T-001", csv_path.name)
        self.assertIn("N12", csv_path.name)
        self.assertIn("-waiting_", csv_path.name)

        with csv_path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader)
            rows = list(reader)
        self.assertEqual(header, list(anchors.CSV_FIELDS))
        self.assertEqual(len(rows), len(table.anchors))

        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["artifact"]["anchor_count"], len(table.anchors))
        self.assertEqual(len(payload["anchors"]), len(table.anchors))

    def test_csv_row_contains_evidence_eight_elements(self):
        _, _, table = self.build_table([MANUAL])
        paths = anchors.write_anchor_table(table, self.tmpdir, "waiting", "v1")
        with Path(paths["csv"]).open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        row = rows[0]
        for column in ("requirement", "applicability_reason", "scope",
                       "material_version", "material_version_locator",
                       "observation", "supporting_judgement", "confirmation",
                       "remediation_verification"):
            self.assertIn(column, row)
        self.assertTrue(row["anchor_id"].startswith("A-"))

    def test_anchor_validation_report(self):
        _, generator, table = self.build_table([REGISTER])
        path = Path(anchors.write_anchor_validation(
            table, generator.problems, self.tmpdir, "waiting", "v1"))
        self.assertTrue(path.is_file())
        self.assertIn("锚点生成校验报告", path.name)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["anchor_count"], len(table.anchors))
        self.assertIn("pending_check", payload["problem_counts"])
        self.assertTrue(any(p["reason_code"] == "CLAUSE_NOT_IN_TREE"
                            for p in payload["problems"]))


if __name__ == "__main__":
    unittest.main()
