#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/build_library.py 的测试（GitHub Issue #2 收尾：point_key 形态、共享块合并与 V12~V15）。

纪律：
  * 三个现行画像必须通过 validate（0 错误）；
  * 负例必须被抓住：非法 point_key、非法难度枚举、未合并的共享块点；
  * 「难度取不到就留空」是既定口径，只告警不报错（V13），但必须喊出来；
  * 负例要重算 snapshot_id，否则会被快照失效规则误判为通过。
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import build_library as bl  # noqa: E402


def run_validate(lib_dir=None):
    """跑一次 validate，返回 (退出码, 报告文本)。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = bl.validate(lib_dir)
    return rc, buf.getvalue()


def registry_entries(lib_dir):
    reg = bl.read_yaml(Path(lib_dir) / "registry.yaml")
    return {e["id"]: e for e in reg.get("profiles", [])}


def current_profiles(lib_dir):
    """当前生效的画像列表：(画像 id, 画像内容)。"""
    out = []
    for pid, entry in registry_entries(lib_dir).items():
        if entry.get("status") != "current":
            continue
        out.append((pid, bl.read_yaml(Path(lib_dir) / entry["file"])))
    return out


class LibraryStateTest(unittest.TestCase):
    """真实库的现状（正例）。"""

    def test_validate_real_library_has_no_error(self):
        """三个现行画像 validate 0 错误。"""
        rc, out = run_validate()
        self.assertEqual(rc, 0, out)

    def test_all_point_keys_follow_pattern(self):
        """所有审核点的 point_key 形如 <条款号>#<序号>，且前缀与所在条款号一致。"""
        total = 0
        for pid, prof in current_profiles(bl.LIB):
            for c in prof.get("clauses", []):
                cn = c.get("clause_no")
                for pt in c.get("points", []):
                    pk = str(pt.get("point_key"))
                    total += 1
                    self.assertTrue(
                        bl.POINT_KEY_RE.match(pk),
                        f"{pid}:{cn} 的 point_key 形态非法 {pk}",
                    )
                    self.assertTrue(
                        pk.startswith(str(cn) + "#"),
                        f"{pid}:{cn} 的 point_key 前缀不匹配 {pk}",
                    )
        self.assertGreater(total, 0, "库里应有审核点")

    def test_block_points_are_merged(self):
        """引用了共享块的条款卡，其块内审核点必须已并入（带 block_ref 溯源）。"""
        checked = 0
        for pid, prof in current_profiles(bl.LIB):
            for c in prof.get("clauses", []):
                blocks = c.get("blocks") or []
                if not blocks:
                    continue
                checked += 1
                cn = c.get("clause_no")
                refs = [
                    b for p in c.get("points", []) for b in (p.get("block_ref") or [])
                ]
                for bid in blocks:
                    self.assertIn(
                        bid, refs,
                        f"{pid}:{cn} 引用了块 {bid} 但块点未合并进来",
                    )
        self.assertGreater(checked, 0, "库里应有引用共享块的条款卡")

    def test_estimated_difficulty_is_reported(self):
        """类比推定的难度必须被 V13 点名，并带 estimated 来源标记。"""
        rc, out = run_validate()
        self.assertEqual(rc, 0, out)
        self.assertIn("V13", out)
        hit = None
        for pid, prof in current_profiles(bl.LIB):
            for c in prof.get("clauses", []):
                for pt in c.get("points", []):
                    if pt.get("difficulty_basis") == "estimated":
                        hit = (pid, c.get("clause_no"), pt.get("point_key"))
                        break
                if hit:
                    break
            if hit:
                break
        self.assertIsNotNone(hit, "应存在类比推定的难度标注")
        self.assertIn(hit[1], out, "V13 报告应点名条款号")


class NegativeCaseTest(unittest.TestCase):
    """校验器必须抓住的坏数据（负例）。"""

    def _copy_lib(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        dst = tmp / "audit-point-library"
        shutil.copytree(bl.LIB, dst)
        return dst

    def _mutate(self, profile_id, mutate):
        """复制库并修改指定画像，返回新库目录。"""
        dst = self._copy_lib()
        entry = registry_entries(dst)[profile_id]
        ppath = dst / entry["file"]
        prof = bl.read_yaml(ppath)
        mutate(prof)
        prof["profile"]["version_meta"]["snapshot_id"] = bl.snapshot_id(prof["clauses"])
        bl.dump_yaml(prof, ppath)
        return dst

    def test_illegal_point_key_is_rejected(self):
        """point_key 缺 #序号 -> 必须报错并点名条款号。"""
        target = {}

        def mutate(prof):
            c = prof["clauses"][0]
            target["clause"] = c["clause_no"]
            c["points"][0]["point_key"] = str(c["clause_no"])

        dst = self._mutate("qms-gbt19001-2016", mutate)
        rc, out = run_validate(str(dst))
        self.assertNotEqual(rc, 0, out)
        self.assertIn("V12", out)
        self.assertIn(target["clause"], out)

    def test_point_key_prefix_mismatch_is_rejected(self):
        """point_key 前缀与所在条款号不一致 -> 必须报错。"""
        target = {}

        def mutate(prof):
            c = prof["clauses"][0]
            target["clause"] = c["clause_no"]
            c["points"][0]["point_key"] = "99.9#1"

        dst = self._mutate("qms-gbt19001-2016", mutate)
        rc, out = run_validate(str(dst))
        self.assertNotEqual(rc, 0, out)
        self.assertIn("V12", out)
        self.assertIn(target["clause"], out)

    def test_illegal_difficulty_value_is_rejected(self):
        """难度枚举非法 -> 必须报错并点名条款号。"""
        target = {}

        def mutate(prof):
            c = prof["clauses"][0]
            target["clause"] = c["clause_no"]
            c["points"][0]["difficulty"] = "very_high"

        dst = self._mutate("qms-gbt19001-2016", mutate)
        rc, out = run_validate(str(dst))
        self.assertNotEqual(rc, 0, out)
        self.assertIn("V4", out)
        self.assertIn(target["clause"], out)

    def test_missing_difficulty_is_warning_only(self):
        """难度缺失按既定口径只告警（不猜、不报错），但必须被点名。"""
        target = {}

        def mutate(prof):
            c = prof["clauses"][0]
            target["clause"] = c["clause_no"]
            c["points"][0]["difficulty"] = None
            c["points"][0]["difficulty_reason"] = None
            c["points"][0]["difficulty_basis"] = None

        dst = self._mutate("qms-gbt19001-2016", mutate)
        rc, out = run_validate(str(dst))
        self.assertEqual(rc, 0, out)
        self.assertIn("V13", out)
        self.assertIn(target["clause"], out)

    def test_unmerged_block_point_is_rejected(self):
        """块内审核点未并入 -> V15 报错并点名条款号。"""
        target = {}

        def mutate(prof):
            for c in prof["clauses"]:
                if not c.get("blocks"):
                    continue
                target["clause"] = c["clause_no"]
                c["points"] = [p for p in c["points"] if not p.get("block_ref")]
                return
            raise AssertionError("库里没有引用共享块的条款卡")

        dst = self._mutate("qms-gbt19001-2016", mutate)
        rc, out = run_validate(str(dst))
        self.assertNotEqual(rc, 0, out)
        self.assertIn("V15", out)
        self.assertIn(target["clause"], out)


if __name__ == "__main__":
    unittest.main()
