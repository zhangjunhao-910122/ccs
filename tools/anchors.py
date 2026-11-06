#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""证据锚点表生成（GitHub Issue #7：资料导入 + 要素抽取 + 证据锚点表生成）。

纯文件实现：只读写文件，不连数据库、不联网。

规格依据（唯一口径）：
  - docs/spec/verdict-anchor-rules.md
      §2.1 证据八要素（requirement / applicability / evidence / aux_judgement /
            confirmation / remediation）
      §2.2 锚点结构与五条硬规则
      §2.3 anchor_id 全局单调递增、不复用；引用写法 [A-0007]；锚点表是唯一事实源
      §3.1 判定四态（conform / nonconform / insufficient / na）
      §4   问题分类
      §7.1 依据快照字段与依据状态（current / superseded / unverifiable）
      §7.4 条款号必须能在条款树找到，否则 CLAUSE_NOT_IN_TREE
      §13.1 原因码全集
  - docs/spec/output-formats.md §2（产物命名）、§7（JSON 过程底稿结构）
  - audit-point-library/shared/rules.yaml（anchor_rules：类型、定位种类、确认状态）

本模块职责：
  1. 由要素（element）生成证据锚点（anchor）序列；
  2. 输出锚点表 CSV + JSON（命名遵循 output-formats.md §2.2）；
  3. 实现「引用纪律」检查：正文只允许写 [A-0007] 这类锚点号；
     锚点缺失或未确认（fact_state 未达 verified / confirmation 未 confirmed），
     一律判「证据不足」，工具不得代为补齐。

红线承接：
  - 工具不代作决定：辅助判断、确认、整改验证一律留空待人工填写；
  - 工具不补证据：引用缺失只报「证据不足」，不生成新锚点；
  - 工具不写标准原文：requirement 引用审核点库的审核问法 + 依据快照指针。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:  # 与 importer 同目录，import 时可用
    from importer import (  # type: ignore
        APPLICABILITY_LABELS,
        FACT_STATES,
        PROBLEM_CLASS_LABELS,
        REASON_CODES,
        load_clause_tree,
        now_iso,
        sha256_bytes,
    )
except ImportError:  # pragma: no cover
    APPLICABILITY_LABELS = {"applicable": "适用", "not_applicable": "不适用", "unknown": "待确认"}
    FACT_STATES = ("user_stated", "material_supported", "verified", "conflict")
    PROBLEM_CLASS_LABELS = {
        "missing": "缺件", "unreadable": "不可读", "pending_check": "待核", "conflict": "冲突",
    }
    REASON_CODES = (
        "BASIS_MISSING", "BASIS_UNVERIFIABLE", "BASIS_SUPERSEDED",
        "APPLICABILITY_UNKNOWN", "NA_WITHOUT_BASIS", "EVIDENCE_MISSING",
        "EVIDENCE_CONFLICT", "EVIDENCE_UNREADABLE", "SAMPLING_INSUFFICIENT",
        "CLAUSE_NOT_IN_TREE", "GAP_CONFIRMED", "REPORT_ITEM_MISSING",
        "DECISION_BLOCKED_OVERDUE",
    )

    def load_clause_tree(*args, **kwargs):  # type: ignore
        raise RuntimeError("importer 模块不可用，无法加载条款树")

    def now_iso() -> str:  # type: ignore
        return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    def sha256_bytes(data: bytes) -> str:  # type: ignore
        import hashlib
        return hashlib.sha256(data).hexdigest()


SCHEMA_VERSION = 1

#: 锚点 id 形式：A- 加 4 位序号（§2.3）
ANCHOR_ID_RE = re.compile(r"^A-\d{4}$")

#: 正文引用形式：[A-0007]（§2.3）
CITATION_RE = re.compile(r"\[(A-\d{4})\]")

#: 锚点类型（rules.yaml anchor_rules.types）
ANCHOR_TYPES: Tuple[str, ...] = ("file", "image")

#: file 锚点的定位种类（rules.yaml file_locator_kinds）
FILE_LOCATOR_KINDS: Tuple[str, ...] = (
    "page", "section", "table_row", "cell", "paragraph", "timestamp",
)

#: 确认状态（rules.yaml confirmation_states）
CONFIRMATION_STATES: Tuple[str, ...] = ("pending", "confirmed", "rejected")

#: 依据状态（§7.1 basis_snapshot.status）
BASIS_STATUSES: Tuple[str, ...] = ("current", "superseded", "unverifiable")

#: 判定四态（§3.1）——本模块只产出 insufficient / ok，不代替判定
JUDGE_VERDICTS: Tuple[str, ...] = ("conform", "nonconform", "insufficient", "na")

#: 自由书写引用的识别（没有锚点号的引用一律判证据不足）
_FREE_CITE_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"见\s*第\s*[0-9]+(?:\.[0-9]+)*\s*(?:章|条|款|节)"),
    re.compile(r"见\s*《[^》]{1,60}》"),
    re.compile(r"参见\s*[\w\u4e00-\u9fff]{2,40}(?:\.pdf|\.docx|\.xlsx|\.md|\.txt)"),
    re.compile(r"见\s*[\w\u4e00-\u9fff]{2,40}(?:\.pdf|\.docx|\.xlsx|\.md|\.txt)"),
    re.compile(r"依据\s*[\w\u4e00-\u9fff]{2,40}(?:\.pdf|\.docx|\.xlsx|\.md|\.txt)"),
)

#: CSV 列顺序（展平：证据八要素 + 锚点结构 + 依据 + 审核点）
CSV_FIELDS: Tuple[str, ...] = (
    "anchor_id",
    "element_id",
    "source_doc_id",
    "type",
    "source_ref",
    "locator",
    "locator_kind",
    "fingerprint",
    "clause_no",
    "requirement",
    "requirement_source_ref",
    "applicability_reason",
    "scope",
    "material_version",
    "material_version_locator",
    "observation",
    "supporting_judgement",
    "confirmation",
    "confirmation_state",
    "confirmed_by",
    "confirmed_at",
    "remediation_verification",
    "fact_state",
    "basis_status",
    "basis_snapshot",
    "points",
    "unobtained",
)


class AnchorError(Exception):
    """锚点生成或引用检查过程中的硬错误（不吞掉、不降级）。"""


# ---------------------------------------------------------------------------
# 审核点库（profile）读取
# ---------------------------------------------------------------------------


class AuditPointProfile:
    """audit-point-library/profiles/quality/<profile_id>.yaml 的只读视图。

    只取本模块需要的字段：条款 → 审核点（point_key / audit_question）、
    标准标识与版本、依据快照 id、条款树引用。
    """

    def __init__(self, workspace_root: Path, profile_id: str = "qms-gbt19001-2016") -> None:
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise AnchorError("缺少 PyYAML，无法加载审核点库") from exc
        self.workspace_root = Path(workspace_root).resolve()
        self.profile_id = profile_id
        rel = f"audit-point-library/profiles/quality/{profile_id}.yaml"
        path = self.workspace_root / rel
        if not path.is_file():
            # 文件名未必等于 profile id（如 qms-gbt19001-2016 → gbt19001-2016.yaml）：
            # 按 profile.id 在目录内匹配，匹配不到才算不存在。
            profile_dir = self.workspace_root / "audit-point-library" / "profiles" / "quality"
            for cand in sorted(profile_dir.glob("*.yaml")):
                try:
                    raw = yaml.safe_load(cand.read_text(encoding="utf-8")) or {}
                except Exception:  # pragma: no cover - 坏文件跳过
                    continue
                if str((raw.get("profile") or {}).get("id") or "") == profile_id:
                    path = cand
                    rel = str(cand.relative_to(self.workspace_root)).replace("\\", "/")
                    break
            else:
                raise AnchorError(f"审核点库不存在：{path}")
        self.source_ref = rel
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        prof = data.get("profile") or {}
        standard = prof.get("standard") or {}
        version_meta = prof.get("version_meta") or {}
        self.designation: str = str(standard.get("designation") or "")
        self.iso_equivalent: str = str(standard.get("iso_equivalent") or "")
        self.edition: str = str(standard.get("edition") or "")
        self.standard_status: str = str(standard.get("status") or "")
        self.verification_status: str = str(version_meta.get("verification_status") or "")
        self.snapshot_id: str = str(version_meta.get("snapshot_id") or "")
        self.clause_tree_ref: str = str(version_meta.get("clause_tree_ref") or "")

        self.points_by_clause: Dict[str, List[Dict[str, Any]]] = {}
        self.titles: Dict[str, str] = {}
        self.cover_index: Dict[str, str] = {}
        self.alias_index: Dict[str, str] = {}
        for clause in data.get("clauses") or []:
            cno = str(clause.get("clause_no") or "")
            if not cno:
                continue
            self.titles[cno] = str(clause.get("title") or "")
            bucket = self.points_by_clause.setdefault(cno, [])
            for point in clause.get("points") or []:
                bucket.append(point)
            # 条款卡 covers 子条款：子条款号 → 卡号（审核点按卡组织）
            for covered in clause.get("covers") or []:
                self.cover_index.setdefault(str(covered), cno)
            for alias in clause.get("aliases") or []:
                self.alias_index.setdefault(str(alias), cno)

    def card_for(self, clause_no: str) -> str:
        """把条款号路由到审核点库里的条款卡号。

        审核点按「条款卡」组织：卡 8.5 覆盖 8.5.1~8.5.6（covers 来自条款树），
        旧版编号走 aliases 路由。因此子条款号要能落到父卡上。
        """
        cno = str(clause_no or "")
        if cno in self.points_by_clause:
            return cno
        if cno in self.cover_index:
            return self.cover_index[cno]
        if cno in self.alias_index:
            return self.alias_index[cno]
        return ""

    def points_for(self, clause_no: str) -> List[Dict[str, Any]]:
        """取某条款（或其所属条款卡）下的审核点。"""
        out = list(self.points_by_clause.get(clause_no) or [])
        if not out:
            for cno, bucket in self.points_by_clause.items():
                for point in bucket:
                    if str(point.get("point_key") or "") == clause_no:
                        out.append(point)
        if not out:
            card = self.card_for(clause_no)
            if card:
                out = list(self.points_by_clause.get(card) or [])
        return out

    def title_for(self, clause_no: str) -> str:
        """条款标题；子条款落到所属条款卡的标题。"""
        cno = str(clause_no or "")
        if cno in self.titles:
            return self.titles[cno]
        card = self.card_for(cno)
        if card:
            return self.titles.get(card, "")
        return ""

    def point_keys(self, clause_no: str) -> List[str]:
        keys: List[str] = []
        for point in self.points_for(clause_no):
            key = str(point.get("point_key") or "")
            if key and key not in keys:
                keys.append(key)
        return keys

    def audit_questions(self, clause_no: str) -> List[str]:
        questions: List[str] = []
        for point in self.points_for(clause_no):
            q = str(point.get("audit_question") or "").strip()
            if q and q not in questions:
                questions.append(q)
        return questions


# ---------------------------------------------------------------------------
# 锚点表
# ---------------------------------------------------------------------------


class AnchorTable:
    """锚点表：anchor_id 全局单调递增、不复用。

    §2.3：锚点表是唯一事实源；正文引用只写 [A-0007]。
    """

    def __init__(self, task_id: str = "", node_id: str = "",
                 profile_id: str = "qms-gbt19001-2016",
                 anchors: Optional[Sequence[Dict[str, Any]]] = None) -> None:
        self.task_id = task_id
        self.node_id = node_id
        self.profile_id = profile_id
        self.anchors: List[Dict[str, Any]] = []
        self._counter = 0
        for anchor in anchors or []:
            self.anchors.append(anchor)
            m = ANCHOR_ID_RE.match(str(anchor.get("anchor_id") or ""))
            if m:
                self._counter = max(self._counter, int(m.group(0)[2:]))

    def next_anchor_id(self) -> str:
        self._counter += 1
        return f"A-{self._counter:04d}"

    def add(self, anchor: Dict[str, Any]) -> Dict[str, Any]:
        if not ANCHOR_ID_RE.match(str(anchor.get("anchor_id") or "")):
            raise AnchorError(f"锚点 id 格式非法：{anchor.get('anchor_id')!r}")
        if anchor["anchor_id"] in {a["anchor_id"] for a in self.anchors}:
            raise AnchorError(f"锚点 id 重复：{anchor['anchor_id']}")
        self.anchors.append(anchor)
        return anchor

    def get(self, anchor_id: str) -> Optional[Dict[str, Any]]:
        for anchor in self.anchors:
            if anchor.get("anchor_id") == anchor_id:
                return anchor
        return None

    def index(self) -> Dict[str, Dict[str, Any]]:
        return {str(a.get("anchor_id")): a for a in self.anchors}

    def to_rows(self) -> List[Dict[str, Any]]:
        """展平成 CSV 行。"""
        rows: List[Dict[str, Any]] = []
        for anchor in self.anchors:
            ev = anchor.get("evidence") or {}
            req = ev.get("requirement") or {}
            app = ev.get("applicability") or {}
            mat = ev.get("material") or {}
            conf = anchor.get("confirmation") or {}
            row: Dict[str, Any] = {
                "anchor_id": anchor.get("anchor_id", ""),
                "element_id": anchor.get("element_id", ""),
                "source_doc_id": anchor.get("source_doc_id", ""),
                "type": anchor.get("type", ""),
                "source_ref": anchor.get("source_ref", ""),
                "locator": (anchor.get("locator") or {}).get("value", ""),
                "locator_kind": (anchor.get("locator") or {}).get("kind", ""),
                "fingerprint": (anchor.get("fingerprint") or {}).get("hash", ""),
                "clause_no": anchor.get("clause_no", ""),
                "requirement": req.get("text", ""),
                "requirement_source_ref": req.get("source_ref", ""),
                "applicability_reason": app.get("reason", ""),
                "scope": app.get("scope", ""),
                "material_version": mat.get("version", ""),
                "material_version_locator": mat.get("locator", ""),
                "observation": ev.get("observation", ""),
                "supporting_judgement": ev.get("aux_judgement", ""),
                "confirmation": conf.get("label", ""),
                "confirmation_state": conf.get("state", ""),
                "confirmed_by": conf.get("by", ""),
                "confirmed_at": conf.get("at", ""),
                "remediation_verification": ev.get("remediation", ""),
                "fact_state": anchor.get("fact_state", ""),
                "basis_status": anchor.get("basis_status", ""),
                "basis_snapshot": json.dumps(
                    anchor.get("basis_snapshot") or {}, ensure_ascii=False, sort_keys=True
                ),
                "points": ";".join(anchor.get("points") or []),
                "unobtained": ";".join(anchor.get("unobtained") or []),
            }
            rows.append(row)
        return rows

    def to_payload(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "artifact": {
                "kind": "evidence_anchor_table",
                "task_id": self.task_id,
                "node_id": self.node_id,
                "profile_id": self.profile_id,
                "generated_at": now_iso(),
                "anchor_count": len(self.anchors),
            },
            "anchors": self.anchors,
        }


# ---------------------------------------------------------------------------
# 锚点生成
# ---------------------------------------------------------------------------


_SCOPE_PROCESS_RE = re.compile(r"(?:过程|流程|工序)\s*[:：]?\s*([\u4e00-\u9fffA-Za-z0-9]{2,16})")
_SCOPE_SITE_RE = re.compile(
    r"(?:场所|车间|厂区|工位|仓库|厂房|实验室|产线)\s*[:：]?\s*([\u4e00-\u9fffA-Za-z0-9]{2,16})"
)


class AnchorGenerator:
    """由要素生成证据锚点。"""

    def __init__(self, workspace_root: Path, task_id: str = "", node_id: str = "",
                 profile_id: str = "qms-gbt19001-2016",
                 clause_tree: Optional[Any] = None) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.profile_id = profile_id
        self.profile = AuditPointProfile(self.workspace_root, profile_id)
        self.clause_tree = clause_tree or load_clause_tree(self.workspace_root, profile_id)
        self.table = AnchorTable(task_id=task_id, node_id=node_id, profile_id=profile_id)
        self.problems: List[Dict[str, Any]] = []
        self._tree_path = self.workspace_root / "audit-point-library" / "clause_trees" / f"{profile_id}.yaml"
        self._tree_sha = ""
        try:
            self._tree_sha = "sha256:" + sha256_bytes(self._tree_path.read_bytes())
        except OSError:
            self._tree_sha = ""

    # -- 依据快照 -----------------------------------------------------------
    def basis_snapshot(self, clause_no: str, basis_status: str) -> Dict[str, Any]:
        """§7.1 依据快照字段。"""
        return {
            "source_ref": self.profile.clause_tree_ref or self.clause_tree.source,
            "source_id": self.profile.designation,
            "version": self.profile.edition,
            "locator": clause_no,
            "snapshot_sha256": self.profile.snapshot_id or self._tree_sha,
            "retrieved_at": now_iso(),
            "status": basis_status,
        }

    # -- 适用性 / 范围 ------------------------------------------------------
    @staticmethod
    def _applicability_reason(element: Dict[str, Any]) -> str:
        state = element.get("applicability") or "unknown"
        locator = element.get("locator", "")
        if state == "applicable":
            return f"材料显式标注适用（定位 {locator}）；具体理由待人工确认"
        if state == "not_applicable":
            return f"材料显式标注不适用（定位 {locator}）；不适用依据待人工核对"
        return "适用性待确认：导入阶段不得代为判定"

    @staticmethod
    def _scope(element: Dict[str, Any]) -> Tuple[str, str, str]:
        """返回 (scope 文本, 过程, 场所)。"""
        quote = str(element.get("quote") or "")
        process = ""
        site = ""
        m = _SCOPE_PROCESS_RE.search(quote)
        if m:
            process = m.group(1)
        m = _SCOPE_SITE_RE.search(quote)
        if m:
            site = m.group(1)
        if not process and not site:
            section = ""
            for tag in element.get("tags") or []:
                if str(tag).startswith("section:"):
                    section = str(tag)[len("section:"):]
                    break
            if section:
                return f"待确认（来源章节：{section}）", "", ""
            return "", "", ""
        parts = []
        if process:
            parts.append(f"过程 {process}")
        if site:
            parts.append(f"场所 {site}")
        return "；".join(parts), process, site

    # -- 生成 ---------------------------------------------------------------
    @staticmethod
    def _unobtained(element: Dict[str, Any], clause_no: str, scope_text: str,
                    requirement: str, material_version: str,
                    observation: str) -> List[str]:
        """登记该锚点里「未取得」的证据要素（宁缺留空并标未取得，绝不臆造）。

        对应 verdict-anchor-rules.md §2.1 证据八要素。
        """
        missing: List[str] = []
        if not clause_no or "待人工补充" in requirement:
            missing.append("具体要求")
        if element.get("applicability", "unknown") != "applicable":
            missing.append("适用理由")
        if not scope_text:
            missing.append("企业过程或场所")
        if not material_version:
            missing.append("材料版本")
        if not observation:
            missing.append("观察事实")
        return missing

    def generate(self, elements: Sequence[Dict[str, Any]]) -> AnchorTable:
        for element in elements:
            self.table.add(self.build_anchor(element))
        return self.table

    def build_anchor(self, element: Dict[str, Any]) -> Dict[str, Any]:
        anchor_id = self.table.next_anchor_id()
        clause_no = str(element.get("clause_no") or "")
        source_ref = str(element.get("source_ref") or "")
        locator_value = str(element.get("locator") or "")
        locator_kind = str(element.get("locator_kind") or "paragraph")
        if locator_kind not in FILE_LOCATOR_KINDS:
            locator_kind = "paragraph"

        # 条款号必须在条款树里（§7.4）
        in_tree = bool(clause_no) and self.clause_tree.known(clause_no)
        if clause_no and not in_tree:
            basis_status = "unverifiable"
            self._add_problem(
                "pending_check",
                f"{source_ref}#{locator_value}",
                f"锚点 {anchor_id} 的条款号 {clause_no} 不在条款树 {self.clause_tree.source} 中",
                reason_code="CLAUSE_NOT_IN_TREE",
                anchor_ids=[anchor_id],
            )
        elif not clause_no:
            basis_status = "unverifiable"
            self._add_problem(
                "pending_check",
                f"{source_ref}#{locator_value}",
                f"锚点 {anchor_id} 未关联条款号",
                reason_code="APPLICABILITY_UNKNOWN",
                anchor_ids=[anchor_id],
            )
        else:
            basis_status = "current"

        # 审核点关联
        points = self.profile.point_keys(clause_no) if clause_no else []
        if clause_no and in_tree and not points:
            self._add_problem(
                "pending_check",
                f"{source_ref}#{locator_value}",
                f"锚点 {anchor_id} 的条款号 {clause_no} 在审核点库里没有对应审核点",
                reason_code="BASIS_MISSING",
                anchor_ids=[anchor_id],
            )
        if not points:
            points = [clause_no] if clause_no else []

        questions = self.profile.audit_questions(clause_no) if clause_no else []
        if questions:
            requirement = "；".join(questions)
        elif clause_no:
            requirement = f"{clause_no} {self.profile.title_for(clause_no)}（审核问法待人工补充）"
        else:
            requirement = "（未关联条款号，具体要求待人工补充）"
        requirement_source_ref = (
            f"{self.profile.source_ref}#{clause_no}" if clause_no else self.profile.source_ref
        )

        scope_text, process, site = self._scope(element)
        if not scope_text:
            self._add_problem(
                "pending_check",
                f"{source_ref}#{locator_value}",
                f"锚点 {anchor_id} 未能确定企业过程或场所",
                reason_code="EVIDENCE_MISSING",
                anchor_ids=[anchor_id],
            )

        fingerprint_hash = str(element.get("fingerprint") or "")
        doc_sha = str(element.get("doc_sha256") or "")
        material_version = str(element.get("material_version") or "")

        observation = str(element.get("quote") or "")
        fact_state = "material_supported"
        if element.get("applicability") == "unknown" and not observation:
            fact_state = "user_stated"

        confirmation = {"state": "pending", "by": "", "at": "", "label": "待确认"}
        evidence = {
            "requirement": {
                "text": requirement,
                "source_ref": requirement_source_ref,
                "version": self.profile.edition,
            },
            "applicability": {
                "reason": self._applicability_reason(element),
                "process": process,
                "site": site,
                "scope": scope_text,
                "state": element.get("applicability", "unknown"),
            },
            "material": {
                "version": material_version,
                "locator": locator_value,
                "fingerprint": {"hash": "sha256:" + fingerprint_hash} if fingerprint_hash else {},
                "doc_sha256": doc_sha,
            },
            "observation": observation,
            "aux_judgement": "",
            "confirmation": confirmation,
            "remediation": "",
        }

        anchor = {
            "anchor_id": anchor_id,
            "type": "file",
            "source_ref": source_ref,
            "locator": {"kind": locator_kind, "value": locator_value},
            "fingerprint": {"hash": "sha256:" + fingerprint_hash} if fingerprint_hash else {},
            "visible_state_only": False,
            "fact_state": fact_state,
            "confirmation": confirmation,
            "element_id": element.get("element_id", ""),
            "source_doc_id": element.get("source_doc_id", ""),
            "clause_no": clause_no,
            "evidence": evidence,
            "basis_status": basis_status,
            "basis_snapshot": self.basis_snapshot(clause_no, basis_status),
            "points": points,
            "unobtained": self._unobtained(element, clause_no, scope_text, requirement,
                                   material_version, observation),
        }
        return anchor

    def _add_problem(self, problem_class: str, subject: str, detail: str,
                     reason_code: str = "", anchor_ids: Optional[Sequence[str]] = None) -> None:
        if reason_code and reason_code not in REASON_CODES:
            raise AnchorError(f"非法原因码：{reason_code}")
        self.problems.append({
            "problem_class": problem_class,
            "problem_class_label": PROBLEM_CLASS_LABELS.get(problem_class, problem_class),
            "subject": subject,
            "detail": detail,
            "reason_code": reason_code,
            "anchor_ids": list(anchor_ids or []),
        })


# ---------------------------------------------------------------------------
# 锚点确认（人工动作的记录接口）
# ---------------------------------------------------------------------------


def confirm_anchor(table: AnchorTable, anchor_id: str, confirmed_by: str,
                   at: Optional[str] = None, fact_state: str = "verified") -> Dict[str, Any]:
    """记录一次人工确认。

    只有确认（confirmation.state = confirmed）且 fact_state 达 verified 时，
    该锚点才可被正文引用（§2.2 硬规则一）。
    """
    anchor = table.get(anchor_id)
    if anchor is None:
        raise AnchorError(f"锚点不存在：{anchor_id}")
    if fact_state not in FACT_STATES:
        raise AnchorError(f"非法事实标记：{fact_state}")
    stamp = at or now_iso()
    anchor["confirmation"] = {
        "state": "confirmed",
        "by": confirmed_by,
        "at": stamp,
        "label": "已确认",
    }
    anchor["fact_state"] = fact_state
    anchor.setdefault("evidence", {})["confirmation"] = anchor["confirmation"]
    return anchor


def reject_anchor(table: AnchorTable, anchor_id: str, by: str,
                  at: Optional[str] = None) -> Dict[str, Any]:
    anchor = table.get(anchor_id)
    if anchor is None:
        raise AnchorError(f"锚点不存在：{anchor_id}")
    stamp = at or now_iso()
    anchor["confirmation"] = {
        "state": "rejected",
        "by": by,
        "at": stamp,
        "label": "已否决",
    }
    anchor["fact_state"] = "conflict"
    anchor.setdefault("evidence", {})["confirmation"] = anchor["confirmation"]
    return anchor


# ---------------------------------------------------------------------------
# 引用纪律检查
# ---------------------------------------------------------------------------


class CitationChecker:
    """检查正文里的证据引用是否合规。

    硬规则（§2.2 / §2.3）：
      - 正文只允许写 [A-0007] 这类锚点号；
      - 锚点不存在 → 证据不足（工具不得补齐）；
      - 锚点未确认（confirmation.state != confirmed）或 fact_state 未达 verified
        → 证据不足；
      - 自由书写的引用（"见第 8.5.2 条"、"见《手册》"、"参见手册.pdf"）→ 证据不足。
    """

    def __init__(self, table: AnchorTable) -> None:
        self.table = table
        self.index = table.index()

    def check(self, text: str) -> Dict[str, Any]:
        text = text or ""
        citations: List[Dict[str, Any]] = []
        consumed: List[Tuple[int, int]] = []
        for m in CITATION_RE.finditer(text):
            consumed.append(m.span())
            anchor_id = m.group(1)
            anchor = self.index.get(anchor_id)
            if anchor is None:
                citations.append({
                    "raw": m.group(0),
                    "anchor_id": anchor_id,
                    "status": "insufficient",
                    "reason": "锚点不在锚点表内：锚点缺失即证据不足，工具不得补齐",
                })
                continue
            conf = anchor.get("confirmation") or {}
            if conf.get("state") != "confirmed":
                citations.append({
                    "raw": m.group(0),
                    "anchor_id": anchor_id,
                    "status": "insufficient",
                    "reason": f"锚点未确认（confirmation.state={conf.get('state') or 'pending'}）",
                })
                continue
            if anchor.get("fact_state") != "verified":
                citations.append({
                    "raw": m.group(0),
                    "anchor_id": anchor_id,
                    "status": "insufficient",
                    "reason": f"事实标记未达 verified（fact_state={anchor.get('fact_state')}）",
                })
                continue
            citations.append({
                "raw": m.group(0),
                "anchor_id": anchor_id,
                "status": "ok",
                "reason": "锚点已确认，可引用",
            })

        free_refs: List[Dict[str, Any]] = []
        for pattern in _FREE_CITE_PATTERNS:
            for m in pattern.finditer(text):
                if any(s <= m.start() < e for s, e in consumed):
                    continue
                free_refs.append({
                    "raw": m.group(0),
                    "status": "insufficient",
                    "reason": "自由书写的引用：证据引用只能写 [A-0007] 形式锚点号",
                })

        insufficient = (
            [c for c in citations if c["status"] == "insufficient"]
            + free_refs
        )
        verdict = "insufficient" if insufficient else "ok"
        return {
            "verdict": verdict,
            "judge_verdict": "insufficient" if insufficient else None,
            "citations": citations,
            "free_text_refs": free_refs,
            "insufficient_count": len(insufficient),
            "checked_at": now_iso(),
        }


def format_citation(anchor_id: str) -> str:
    """引用写法：[A-0007]（§2.3）。"""
    if not ANCHOR_ID_RE.match(anchor_id):
        raise AnchorError(f"锚点 id 格式非法：{anchor_id}")
    return f"[{anchor_id}]"


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def write_anchor_table(table: AnchorTable, out_dir: Path, state_name: str = "waiting",
                       version: str = "v1") -> Dict[str, str]:
    """输出锚点表 CSV + JSON。

    命名遵循 output-formats.md §2.2：<任务标识>_<节点编号>_<成果名称>_<版本>.<扩展名>，
    状态后缀 -waiting/-review/-final/-terminated。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"{table.task_id}_{table.node_id}_证据锚点表-{state_name}_{version}"
    csv_path = out_dir / f"{base}.csv"
    json_path = out_dir / f"{base}.json"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_FIELDS))
        writer.writeheader()
        for row in table.to_rows():
            writer.writerow({k: row.get(k, "") for k in CSV_FIELDS})
    json_path.write_text(
        json.dumps(table.to_payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"csv": str(csv_path), "json": str(json_path)}


def write_anchor_validation(table: AnchorTable, problems: Sequence[Dict[str, Any]],
                            out_dir: Path, state_name: str = "waiting",
                            version: str = "v1") -> str:
    """锚点生成阶段的校验报告（与 importer 的校验报告同族命名）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"{table.task_id}_{table.node_id}_锚点生成校验报告-{state_name}_{version}"
    path = out_dir / f"{base}.json"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "profile_id": table.profile_id,
        "generated_at": now_iso(),
        "anchor_count": len(table.anchors),
        "problem_counts": {
            key: sum(1 for p in problems if p["problem_class"] == key)
            for key in ("missing", "unreadable", "pending_check", "conflict")
        },
        "problems": list(problems),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_elements(state_path: Path) -> List[Dict[str, Any]]:
    if not state_path.is_file():
        raise AnchorError(f"导入状态文件不存在：{state_path}")
    data = json.loads(state_path.read_text(encoding="utf-8"))
    elements = data.get("elements") or {}
    order = data.get("element_order") or list(elements.keys())
    return [elements[fp] for fp in order if fp in elements]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="证据锚点表生成（Issue #7，纯文件实现）"
    )
    parser.add_argument("--workspace", default=".", help="仓库根目录")
    parser.add_argument("--state", default=".research-tmp/import_state.json", help="导入状态文件")
    parser.add_argument("--out", default=".research-tmp/anchors", help="锚点表输出目录")
    parser.add_argument("--profile", default="qms-gbt19001-2016", help="标准画像 id")
    parser.add_argument("--task-id", default="TASK-2026-QMS-A", help="任务标识")
    parser.add_argument("--node-id", default="N12", help="节点编号")
    parser.add_argument("--state-suffix", default="waiting", help="产物状态后缀")
    parser.add_argument("--version", default="v1", help="产物版本")
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).resolve()
    state_path = Path(args.state)
    if not state_path.is_absolute():
        state_path = workspace / state_path
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = workspace / out_dir

    elements = _load_elements(state_path)
    generator = AnchorGenerator(
        workspace, task_id=args.task_id, node_id=args.node_id, profile_id=args.profile
    )
    table = generator.generate(elements)
    paths = write_anchor_table(table, out_dir, args.state_suffix, args.version)
    validation = write_anchor_validation(table, generator.problems, out_dir,
                                         args.state_suffix, args.version)

    print("锚点表生成完成：")
    print(f"  锚点数：{len(table.anchors)}")
    print(f"  问题数：{len(generator.problems)}")
    print(f"  锚点表 CSV：{paths['csv']}")
    print(f"  锚点表 JSON：{paths['json']}")
    print(f"  校验报告：{validation}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
