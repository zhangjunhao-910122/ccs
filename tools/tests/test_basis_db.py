#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""依据库与依据版本核验的单元测试（对应 GitHub Issue #20）。

运行：
    export PYTHONIOENCODING=utf-8
    /i/python3.10/python.exe -m unittest discover -s tools/tests -v

所有用例都在临时目录里建库与建台账，不写入仓库任何目录。
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import basis_db  # noqa: E402


REPO_ROOT = _TOOLS_DIR.parent
SEED_REGISTRY = _TOOLS_DIR / "basis_snapshots" / "registry.json"
LIBRARY_ROOT = REPO_ROOT / "audit-point-library"


def run_cli(argv):
    """跑一次命令行，返回 (退出码, stdout, stderr)。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = basis_db.main(argv)
    return code, out.getvalue(), err.getvalue()


class BasisDbTestCase(unittest.TestCase):
    """公共夹具：临时目录 + 临时台账 + 本地来源件。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="basis_db_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.db_path = self.tmp / "basis.sqlite3"
        self.registry_path = self.tmp / "registry.json"
        self.src_dir = self.tmp / "src"
        self.src_dir.mkdir(parents=True, exist_ok=True)

    # -- 夹具工具 ------------------------------------------------------
    def write_source(self, name: str, data: bytes) -> Path:
        p = self.src_dir / name
        p.write_bytes(data)
        return p

    def write_registry(self, snapshots):
        payload = {
            "schema_version": 1,
            "registry_updated": "2026-11-05",
            "policy": {"prohibited": ["禁止把搜索摘要当已核验正文"]},
            "snapshots": snapshots,
        }
        self.registry_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return self.registry_path

    def entry(
        self,
        source_id,
        title,
        version,
        local_file,
        sha256=None,
        status_evidence="一手已确认：该版本现行有效。",
        verification_status="pending_review",
    ):
        return {
            "source_id": source_id,
            "publisher": "ISO",
            "number": source_id,
            "title": title,
            "version": version,
            "published_date": "2015-09",
            "effective_date": None,
            "retrieved_at": "2026-11-05",
            "source_url": "https://example.invalid/" + source_id.replace(" ", "_"),
            "locator": "条款目录页",
            "applicable_reason": "测试用夹具",
            "status_evidence": status_evidence,
            "verification_status": verification_status,
            "snapshot_sha256": sha256,
            "local_file": local_file,
        }

    def cli(self, *args):
        return run_cli(
            [
                "--db",
                str(self.db_path),
                "--registry",
                str(self.registry_path),
                "--repo-root",
                str(self.tmp),
            ]
            + list(args)
        )


class TestDownloadAndList(BasisDbTestCase):
    """download 成功后 list 能查到该文档及其版本与核验结果。"""

    def test_download_then_list_finds_document(self):
        data = b"ISO 9001:2015 sample snapshot bytes"
        src = self.write_source("iso9001-2015.pdf", data)
        digest = hashlib.sha256(data).hexdigest()
        self.write_registry(
            [
                self.entry(
                    "ISO 9001:2015",
                    "Quality management systems - Requirements",
                    "2015",
                    "src/iso9001-2015.pdf",
                    sha256=digest,
                )
            ]
        )

        code, out, err = self.cli("download", "ISO 9001:2015")
        self.assertEqual(code, basis_db.EXIT_OK, msg=out + err)
        self.assertIn("ISO 9001:2015", out)
        self.assertIn(digest, out)

        code, out, err = self.cli("list")
        self.assertEqual(code, basis_db.EXIT_OK, msg=out + err)
        self.assertIn("ISO 9001:2015", out)
        self.assertIn("iso9001-2015.pdf", out)
        self.assertIn("candidate", out)

        conn = basis_db.open_db(self.db_path)
        try:
            row = conn.execute(
                "SELECT sha256, size FROM document_versions"
            ).fetchone()
            self.assertEqual(row["sha256"], digest)
            self.assertEqual(row["size"], len(data))
        finally:
            conn.close()

    def test_download_unknown_source_id_is_usage_error(self):
        self.write_registry([])
        code, out, err = self.cli("download", "ISO 9999:2999")
        self.assertEqual(code, basis_db.EXIT_USAGE)
        self.assertIn("未登记", err)


class TestCheckExitCodes(BasisDbTestCase):
    """check 的三态判定与退出码。"""

    def _seed(self, entry_obj):
        data = ("payload for " + entry_obj["source_id"]).encode("utf-8")
        name = (
            entry_obj["source_id"]
            .replace(":", "-")
            .replace(" ", "_")
            .replace("/", "_")
            + ".pdf"
        )
        src = self.write_source(name, data)
        entry_obj["local_file"] = "src/" + name
        entry_obj["snapshot_sha256"] = hashlib.sha256(data).hexdigest()
        self.write_registry([entry_obj])
        code, out, err = self.cli("download", entry_obj["source_id"])
        self.assertEqual(code, basis_db.EXIT_OK, msg=out + err)

    def test_check_superseded_exits_2(self):
        """来源声明该版本已撤回 → 判定 superseded → 退出码 2。"""
        entry_obj = self.entry(
            "ISO 9001:2015",
            "Quality management systems - Requirements",
            "2015",
            None,
            status_evidence="一手已确认：该版本已于 2026-09-16 撤回，被 ISO 9001:2026 取代。",
        )
        self._seed(entry_obj)
        code, out, err = self.cli("check", "ISO 9001:2015")
        self.assertEqual(code, basis_db.EXIT_SUPERSEDED, msg=out + err)
        self.assertIn("superseded", out)

    def test_check_unverifiable_when_no_version_snapshot_exits_3(self):
        """库内文档存在但没有版本快照 → 无法判定 → 退出码 3。"""
        self.write_registry(
            [
                self.entry(
                    "ISO 9001:2015",
                    "Quality management systems - Requirements",
                    "2015",
                    "src/missing.pdf",
                )
            ]
        )
        conn = basis_db.init_db(self.db_path)
        try:
            basis_db.register_document(
                conn,
                "ISO 9001:2015",
                "ISO",
                "Quality management systems - Requirements",
                "ISO 9001:2015",
                "国际标准",
                "https://example.invalid/9001",
                "https://example.invalid/9001.pdf",
            )
        finally:
            conn.close()

        code, out, err = self.cli("check", "ISO 9001:2015")
        self.assertEqual(code, basis_db.EXIT_UNVERIFIABLE, msg=out + err)
        self.assertIn("无法判定", err)

        conn = basis_db.open_db(self.db_path)
        try:
            row = conn.execute(
                "SELECT result FROM checks ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(row["result"], basis_db.STATUS_UNVERIFIABLE)
        finally:
            conn.close()

    def test_check_source_not_in_registry_exits_3(self):
        """库里有该依据但台账无来源记录 → 无法判定 → 退出码 3。"""
        self.write_registry([])
        conn = basis_db.init_db(self.db_path)
        try:
            basis_db.register_document(
                conn,
                "GB/T 19001:2016",
                "SAC",
                "质量管理体系 要求",
                "GB/T 19001-2016",
                "国家标准",
                "https://example.invalid/19001",
                None,
            )
        finally:
            conn.close()
        code, out, err = self.cli("check", "GB/T 19001:2016")
        self.assertEqual(code, basis_db.EXIT_UNVERIFIABLE, msg=out + err)
        self.assertIn("无法判定", err)

    def test_check_current_exits_0(self):
        """名称标题与版本标识一致 → 判定 current → 退出码 0。"""
        entry_obj = self.entry(
            "GB/T 19001:2016",
            "质量管理体系 要求",
            "2016",
            None,
        )
        self._seed(entry_obj)
        code, out, err = self.cli("check", "GB/T 19001:2016")
        self.assertEqual(code, basis_db.EXIT_OK, msg=out + err)
        self.assertIn("current", out)


class TestEmptyAndMissingDb(BasisDbTestCase):
    """库为空 / 库文件不存在时不得静默通过。"""

    def test_list_on_empty_db_returns_nonzero(self):
        basis_db.init_db(self.db_path).close()
        code, out, err = self.cli("list")
        self.assertNotEqual(code, 0)
        self.assertEqual(code, basis_db.EXIT_USAGE)
        self.assertIn("依据库为空", err)

    def test_list_on_missing_db_returns_nonzero(self):
        code, out, err = self.cli("list")
        self.assertNotEqual(code, 0)
        self.assertEqual(code, basis_db.EXIT_USAGE)
        self.assertIn("依据库不存在", err)

    def test_check_on_missing_db_returns_nonzero(self):
        code, out, err = self.cli("check", "ISO 9001:2015")
        self.assertNotEqual(code, 0)
        self.assertEqual(code, basis_db.EXIT_USAGE)
        self.assertIn("依据库不存在", err)

    def test_check_source_not_in_db_returns_nonzero(self):
        self.write_registry([])
        basis_db.init_db(self.db_path).close()
        code, out, err = self.cli("check", "ISO 9001:2015")
        self.assertNotEqual(code, 0)
        self.assertEqual(code, basis_db.EXIT_USAGE)
        self.assertIn("依据库中没有", err)


class TestResolveClauseBasis(BasisDbTestCase):
    """resolve_clause_basis 的字段与错误路径。"""

    def setUp(self):
        super().setUp()
        self.conn = basis_db.init_db(self.db_path)
        self.addCleanup(self.conn.close)

    def test_unregistered_clause_returns_explicit_error(self):
        result = basis_db.resolve_clause_basis(
            self.conn,
            "qms-gbt19001-2016",
            "99.9",
            registry_path=str(SEED_REGISTRY),
            library_root=str(LIBRARY_ROOT),
        )
        self.assertEqual(result["error"], "CLAUSE_NOT_IN_TREE")
        self.assertIn("99.9", result["message"])
        self.assertIn("CLAUSE_NOT_IN_TREE", result["message"])
        self.assertEqual(result["status"], basis_db.STATUS_UNVERIFIABLE)
        self.assertEqual(result["insufficient_reason"], "CLAUSE_NOT_IN_TREE")

    def test_unknown_profile_returns_explicit_error(self):
        result = basis_db.resolve_clause_basis(
            self.conn,
            "no-such-profile",
            "4.1",
            registry_path=str(SEED_REGISTRY),
            library_root=str(LIBRARY_ROOT),
        )
        self.assertEqual(result["error"], "PROFILE_NOT_FOUND")
        self.assertTrue(result["message"])

    def test_registered_clause_returns_snapshot_fields(self):
        result = basis_db.resolve_clause_basis(
            self.conn,
            "qms-gbt19001-2016",
            "4.1",
            registry_path=str(SEED_REGISTRY),
            library_root=str(LIBRARY_ROOT),
        )
        self.assertIsNone(result["error"])
        for field in (
            "source_ref",
            "source_id",
            "version",
            "locator",
            "snapshot_sha256",
            "retrieved_at",
            "status",
        ):
            self.assertIn(field, result)
            self.assertIsNotNone(result[field], msg=field)
        self.assertEqual(result["locator"], "4.1")
        self.assertEqual(result["source_id"], "GB/T 19001:2016")
        self.assertEqual(result["source_ref"], "GB/T 19001:2016#4.1")
        # 未经人工复核 → unverifiable → 只能落证据不足
        self.assertEqual(result["status"], basis_db.STATUS_UNVERIFIABLE)
        self.assertEqual(result["insufficient_reason"], "BASIS_UNVERIFIABLE")


class TestSha256Recompute(BasisDbTestCase):
    """sha256 复算一致。"""

    def test_sha256_matches_manual_recompute(self):
        conn = basis_db.init_db(self.db_path)
        try:
            doc_id = basis_db.register_document(
                conn,
                "ISO 9001:2015",
                "ISO",
                "Quality management systems - Requirements",
                "ISO 9001:2015",
                "国际标准",
                "https://example.invalid/9001",
                "https://example.invalid/9001.pdf",
            )
            payload = bytes(range(256)) * 7
            version_id = basis_db.add_version(
                conn, doc_id, "ISO 9001:2015", "iso9001.pdf", "file:///x", payload
            )
            row = conn.execute(
                "SELECT sha256, size, content FROM document_versions WHERE id = ?",
                (version_id,),
            ).fetchone()
            self.assertEqual(row["sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(row["size"], len(payload))
            self.assertEqual(bytes(row["content"]), payload)
            self.assertEqual(
                basis_db.sha256_bytes(payload), row["sha256"]
            )
        finally:
            conn.close()


class TestIsBasisCurrent(BasisDbTestCase):
    """is_basis_current 的三态与人工复核闸口。"""

    def setUp(self):
        super().setUp()
        self.conn = basis_db.init_db(self.db_path)
        self.addCleanup(self.conn.close)

    def test_pending_review_basis_is_unverifiable_not_current(self):
        status = basis_db.is_basis_current(
            self.conn, "GB/T 19001:2016", registry_path=str(SEED_REGISTRY)
        )
        self.assertEqual(status, basis_db.STATUS_UNVERIFIABLE)
        self.assertNotEqual(status, basis_db.STATUS_CURRENT)

    def test_seed_iso_9001_2015_is_superseded(self):
        status = basis_db.is_basis_current(
            self.conn, "ISO 9001:2015", registry_path=str(SEED_REGISTRY)
        )
        self.assertEqual(status, basis_db.STATUS_SUPERSEDED)

    def test_human_verified_flow_allows_current(self):
        """人工复核流转 human_verified 后，现行依据才可为 current。"""
        payload = b"gb/t 19001-2016 snapshot"
        doc_id = basis_db.register_document(
            self.conn,
            "GB/T 19001:2016",
            "SAC",
            "质量管理体系 要求",
            "GB/T 19001-2016",
            "国家标准",
            "https://example.invalid/19001",
            None,
        )
        version_id = basis_db.add_version(
            self.conn, doc_id, "GB/T 19001:2016", "gbt19001.pdf", None, payload
        )
        basis_db.set_version_verification(
            self.conn, version_id, "pending_review", "张三", "初步核对"
        )
        status = basis_db.is_basis_current(
            self.conn, "GB/T 19001:2016", registry_path=str(SEED_REGISTRY)
        )
        self.assertEqual(status, basis_db.STATUS_UNVERIFIABLE)

        basis_db.set_version_verification(
            self.conn, version_id, "human_verified", "李四", "复核通过"
        )
        status = basis_db.is_basis_current(
            self.conn, "GB/T 19001:2016", registry_path=str(SEED_REGISTRY)
        )
        self.assertEqual(status, basis_db.STATUS_CURRENT)

        row = self.conn.execute(
            "SELECT verification_status, verified_by, note FROM document_versions "
            "WHERE id = ?",
            (version_id,),
        ).fetchone()
        self.assertEqual(row["verification_status"], "human_verified")
        self.assertEqual(row["verified_by"], "李四")
        self.assertIn("human_verified", row["note"])


class TestSeedRegistry(BasisDbTestCase):
    """快照台账种子数据的静态检查。"""

    def test_seed_registry_has_nine_entries_all_pending_review(self):
        entries = basis_db.registry_entries(str(SEED_REGISTRY))
        self.assertEqual(len(entries), 9)
        for e in entries:
            self.assertEqual(e["verification_status"], "pending_review")
            for field in (
                "source_id",
                "publisher",
                "number",
                "title",
                "version",
                "published_date",
                "effective_date",
                "retrieved_at",
                "source_url",
                "locator",
                "applicable_reason",
                "status_evidence",
                "verification_status",
                "snapshot_sha256",
                "local_file",
            ):
                self.assertIn(field, e, msg=field)
        ids = {e["source_id"] for e in entries}
        for expected in (
            "ISO 9001:2015",
            "ISO 9001:2015/Amd 1:2024",
            "ISO 9001:2026",
            "GB/T 19001:2016",
            "ISO 14001:2015",
            "ISO 14001:2026",
            "GB/T 24001:2016",
            "ISO 45001:2018",
            "GB/T 45001:2020",
        ):
            self.assertIn(expected, ids)

    def test_seed_registry_states_prohibit_search_snippet_as_verified(self):
        data = basis_db.load_registry(str(SEED_REGISTRY))
        blob = json.dumps(data, ensure_ascii=False)
        self.assertIn("禁止把搜索摘要当已核验正文", blob)

    def test_seed_iso_9001_2015_entry_is_superseded(self):
        entry = basis_db.find_snapshot("ISO 9001:2015", str(SEED_REGISTRY))
        self.assertIsNotNone(entry)
        status = basis_db.derive_snapshot_status(entry)
        self.assertEqual(status, basis_db.STATUS_SUPERSEDED)


if __name__ == "__main__":
    unittest.main()
