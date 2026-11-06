#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""资料导入与要素抽取（GitHub Issue #7：实现(原型) 资料导入 + 要素抽取 + 证据锚点表生成）。

纯文件实现：只读写文件，不连数据库、不联网。样本数据全部取自仓内已有材料。

规格依据（唯一口径，实现不得另立）：
  - docs/spec/verdict-anchor-rules.md
      §1   事实标记四态（B 组）、问题七分类（E 组）
      §2.1 证据八要素
      §2.2 锚点结构与五条硬规则
      §4   问题分类（missing/unreadable/pending_check/conflict）与处置
      §7   依据库、依据快照字段
      §13.1 原因码全集
  - docs/spec/output-formats.md §2（产物清单与命名）、§7（JSON 过程底稿结构）
  - docs/spec/audit-process-state-machine.md §5.3（交接前置条件）、§6（阻塞与补件）
  - docs/research/iso9001-clause-audit-baseline.md（条款级审核点基线）

本模块职责：
  1. 把企业的体系文件（PDF / DOCX / XLSX / TXT / Markdown）读成「要素」序列；
     要素 = 一份被导入材料里的一个可审核对象：一条要求、一个过程、一个记录、
     一个场所、一台设备。
  2. 幂等：以「文件 sha256 + 元素指纹（source_doc_id + locator + quote 的 sha256）」
     去重，重复导入不产生重复要素。
  3. 输出校验报告：缺件 / 不可读 / 待核 / 冲突 四类问题，命名与 §4 对齐。

红线承接（§0）：
  - 工具不代作决定：导入阶段不判「不适用」，适用性一律 unknown（待确认），
    除非材料自身带显式标记（仍需人工确认才成立）。
  - 工具不补证据：要素缺失即缺件，不自动补齐。
  - 工具不写标准原文：requirement 只引用审核点库的审核问法 + 依据快照指针。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:  # PyYAML 随解释器提供；缺失时给出明确报错而不是静默降级
    import yaml
except ImportError:  # pragma: no cover - 环境问题
    yaml = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 常量（枚举值逐字取自 docs/spec/verdict-anchor-rules.md，不得自造）
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

#: 要素类型（任务书定义：一条要求/一个过程/一个记录/一个场所/一台设备）
ELEMENT_TYPES: Tuple[str, ...] = (
    "requirement",
    "process",
    "record",
    "site",
    "equipment",
)
ELEMENT_TYPE_LABELS: Dict[str, str] = {
    "requirement": "一条要求",
    "process": "一个过程",
    "record": "一个记录",
    "site": "一个场所",
    "equipment": "一台设备",
}

#: 适用性三态（verdict-anchor-rules.md §3.2 applicability_expr）
APPLICABILITY_STATES: Tuple[str, ...] = ("applicable", "not_applicable", "unknown")
APPLICABILITY_LABELS: Dict[str, str] = {
    "applicable": "适用",
    "not_applicable": "不适用",
    "unknown": "待确认",
}

#: E 组问题分类（本票只用到前四类，后三类属判定侧）
PROBLEM_CLASSES: Tuple[str, ...] = ("missing", "unreadable", "pending_check", "conflict")
PROBLEM_CLASS_LABELS: Dict[str, str] = {
    "missing": "缺件",
    "unreadable": "不可读",
    "pending_check": "待核",
    "conflict": "冲突",
    "suspected_nc": "疑似不符合",
    "formal_nc": "正式不符合",
    "suggestion": "建议",
}

#: 原因码全集（照抄 verdict-anchor-rules.md §13.1）
REASON_CODES: Tuple[str, ...] = (
    "BASIS_MISSING",
    "BASIS_UNVERIFIABLE",
    "BASIS_SUPERSEDED",
    "APPLICABILITY_UNKNOWN",
    "NA_WITHOUT_BASIS",
    "EVIDENCE_MISSING",
    "EVIDENCE_CONFLICT",
    "EVIDENCE_UNREADABLE",
    "SAMPLING_INSUFFICIENT",
    "CLAUSE_NOT_IN_TREE",
    "GAP_CONFIRMED",
    "REPORT_ITEM_MISSING",
    "DECISION_BLOCKED_OVERDUE",
)

#: 事实标记四态（B 组）
FACT_STATES: Tuple[str, ...] = ("user_stated", "material_supported", "verified", "conflict")

#: 未取得标记（票面验收第 2 条：抽取不到的要素不臆造，宁缺留空并标「未取得」）
UNOBTAINED_LABEL: str = "未取得"

#: 要素层可登记为「未取得」的字段（与证据八要素的字段对应）
UNOBTAINED_FIELDS: Tuple[str, ...] = ("条款号", "定位", "版本号", "适用性")

SUPPORTED_SUFFIXES: Tuple[str, ...] = (".pdf", ".docx", ".xlsx", ".txt", ".md", ".markdown")

MAX_QUOTE_CHARS = 200

# 条款号候选：至少两段，形如 8.5.2；排除 "A/1.0"、"V1.0" 这类版本号
CLAUSE_RE = re.compile(r"(?<![\d.\-/])([1-9]\d?(?:\.\d{1,3}){1,3})(?![\d])")

#: 版本号上下文（出现这些前缀/后缀时不把数字当条款号）
_VERSION_PREFIXES = "/vV版本Rev"
_VERSION_SUFFIXES = "版份"

#: 规范性动词（用于区分「要求」与其它要素类型）
_NORMATIVE_MARKERS = (
    "应", "应当", "应确保", "必须", "不得", "须", "需", "确保", "要求", "规定",
)

_TYPE_MARKERS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("site", ("场所", "车间", "厂区", "工位", "仓库", "厂房", "实验室", "产线")),
    ("equipment", ("设备", "仪器", "装置", "工装", "检具", "量具", "模具", "设施", "机床", "仪器")),
    ("record", ("记录", "台账", "清单", "档案", "表单", "报表", "日志", "纪要", "台帐", "登记")),
    ("process", ("过程", "流程", "工序", "活动", "作业")),
)

#: 互斥/替代措辞（隐式冲突检测用）
_CONFLICT_MARKERS = ("不再", "取消", "作废", "废止", "改为", "替代", "与之冲突", "互相矛盾")

#: 否定语境：撤销词出现在否定表述里时不构成冲突陈述（如「未见作废文件」）
_CONFLICT_NEGATIONS = ("未见", "未发现", "未使用", "未发生", "没有", "无")


def _has_conflict_wording(quote: str) -> bool:
    """判断一句原文是否含互斥/替代措辞（排除否定语境）。"""
    text = quote or ""
    for mark in _CONFLICT_MARKERS:
        start = 0
        while True:
            pos = text.find(mark, start)
            if pos < 0:
                break
            prefix = text[max(0, pos - 6):pos]
            if not any(neg in prefix for neg in _CONFLICT_NEGATIONS):
                return True
            start = pos + len(mark)
    return False


class UnreadableDocument(Exception):
    """材料已提交但无法辨认（模糊、不可打开、加密、扫描失真、空内容）。

    对应 verdict-anchor-rules.md §4 的 `unreadable`。
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def normalize_ws(text: str) -> str:
    """折叠空白，保留原文措辞（不翻译、不改写）。"""
    return re.sub(r"[ \t\u3000]+", " ", (text or "")).strip()


def truncate_quote(text: str, limit: int = MAX_QUOTE_CHARS) -> str:
    """截断到 limit 字以内（任务书：quote ≤ 200 字）。"""
    text = normalize_ws(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def element_fingerprint(source_doc_id: str, locator: str, quote: str) -> str:
    """元素指纹 = sha256(source_doc_id + locator + quote)。"""
    return sha256_text(f"{source_doc_id}|{locator}|{quote}")


def find_clause_no(text: str) -> Tuple[str, List[str]]:
    """从一段文字里抽条款号。

    返回 (主条款号, 其它候选条款号)。版本号（如 A/1.0、V2.0）不作为条款号。
    """
    found: List[str] = []
    for m in CLAUSE_RE.finditer(text or ""):
        start, end = m.span(1)
        before = text[max(0, start - 1):start]
        after = text[end:end + 1]
        if before and before[-1] in _VERSION_PREFIXES:
            continue
        if after and after[0] in _VERSION_SUFFIXES:
            continue
        value = m.group(1)
        if value not in found:
            found.append(value)
    if not found:
        return "", []
    return found[0], found[1:]


def detect_element_type(text: str, explicit: str = "") -> str:
    """判定要素类型。

    显式标记优先；否则：有规范性动词 → requirement（一条要求）；
    再按 场所 > 设备 > 记录 > 过程 的顺序匹配关键词。
    """
    if explicit:
        e = explicit.strip().lower()
        if e in ELEMENT_TYPES:
            return e
        for t in ELEMENT_TYPES:
            if t in e or ELEMENT_TYPE_LABELS[t] in explicit:
                return t
    has_normative = any(mark in text for mark in _NORMATIVE_MARKERS)
    if has_normative:
        return "requirement"
    for etype, markers in _TYPE_MARKERS:
        if any(mark in text for mark in markers):
            return etype
    return "requirement"


def parse_applicability(explicit: str = "") -> str:
    """适用性取值。只认显式标记；正文里出现「不适用」字样一律仍记待确认。

    对应红线二：「不适用不是垃圾桶」，工具不得把未知改标为不适用。
    """
    e = (explicit or "").strip().lower()
    if e in ("applicable", "适用"):
        return "applicable"
    if e in ("not_applicable", "不适用"):
        return "not_applicable"
    return "unknown"


def printable_ratio(text: str) -> float:
    """可辨认字符占比：用于识别乱码。"""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    good = 0
    for c in chars:
        o = ord(c)
        if (
            0x20 <= o < 0x7F
            or 0x3000 <= o <= 0x303F
            or 0x4E00 <= o <= 0x9FFF
            or 0xFF00 <= o <= 0xFFEF
            or c in "…—‘’“”·°±×÷≥≤"
        ):
            good += 1
    return good / len(chars)


def looks_like_garbage(text: str) -> bool:
    """乱码判定：可辨认字符占比过低，或字符多样性过低（空文件、重复填充）。"""
    stripped = text.strip()
    if not stripped:
        return True
    if printable_ratio(stripped) < 0.75:
        return True
    distinct = len(set(stripped.replace(" ", "")))
    return distinct < 6


# ---------------------------------------------------------------------------
# 条款树索引
# ---------------------------------------------------------------------------


class ClauseIndex:
    """条款树索引：条款号 → 中文标题。

    条款号只认 clause_trees/*.yaml（§7.4：找不到 → CLAUSE_NOT_IN_TREE）。
    """

    def __init__(self, profile_id: str, clause_no: str, title: str, source: str,
                 verification_status: str = "") -> None:
        self.profile_id = profile_id
        self.clause_no = clause_no
        self.title = title
        self.source = source
        self.verification_status = verification_status

    def to_dict(self) -> Dict[str, str]:
        return {
            "profile_id": self.profile_id,
            "clause_no": self.clause_no,
            "title": self.title,
            "source": self.source,
            "verification_status": self.verification_status,
        }


class ClauseTree:
    """加载 audit-point-library/clause_trees/<profile>.yaml。"""

    def __init__(self, profile_id: str, clauses: Dict[str, str], source: str,
                 verification_status: str = "") -> None:
        self.profile_id = profile_id
        self.clauses = clauses
        self.source = source
        self.verification_status = verification_status

    def known(self, clause_no: str) -> bool:
        return bool(clause_no) and clause_no in self.clauses

    def title(self, clause_no: str) -> str:
        return self.clauses.get(clause_no, "")

    def all_clause_nos(self) -> List[str]:
        return sorted(self.clauses.keys(), key=_clause_sort_key)


def _clause_sort_key(clause_no: str) -> Tuple:
    parts = clause_no.split(".")
    out: List[Any] = []
    for p in parts:
        try:
            out.append((0, int(p)))
        except ValueError:
            out.append((1, p))
    return tuple(out)


def _walk_clause_nodes(node: Any, acc: Dict[str, str]) -> None:
    if isinstance(node, list):
        for item in node:
            _walk_clause_nodes(item, acc)
        return
    if not isinstance(node, dict):
        return
    cno = node.get("clause_no")
    if cno is not None:
        acc[str(cno)] = str(node.get("title") or "")
    for child in node.get("children") or []:
        _walk_clause_nodes(child, acc)


def load_clause_tree(workspace_root: Path, profile_id: str = "qms-gbt19001-2016") -> ClauseTree:
    """从 audit-point-library 加载条款树。

    找不到条款树时抛错——没有条款树就不能做条款号校验，
    不能静默放行（红线一：判定不可自由书写）。
    """
    if yaml is None:
        raise RuntimeError("缺少 PyYAML，无法加载条款树")
    path = workspace_root / "audit-point-library" / "clause_trees" / f"{profile_id}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"条款树不存在：{path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    acc: Dict[str, str] = {}
    _walk_clause_nodes(data.get("clause_tree") or [], acc)
    if not acc:
        raise RuntimeError(f"条款树为空：{path}")
    return ClauseTree(
        profile_id=profile_id,
        clauses=acc,
        source=str(path.relative_to(workspace_root)).replace(os.sep, "/"),
        verification_status=str(data.get("verification_status") or ""),
    )


# ---------------------------------------------------------------------------
# 要素与状态
# ---------------------------------------------------------------------------


def _now() -> str:
    return now_iso()


class ImportState:
    """导入状态（纯文件，JSON 持久化）。

    幂等的两层键：
      1) 文件级：sha256 —— 同一份内容（不论路径）只导入一次；
      2) 元素级：fingerprint = sha256(source_doc_id|locator|quote)。
    """

    def __init__(self, path: Path, profile_id: str = "qms-gbt19001-2016") -> None:
        self.path = Path(path)
        self.profile_id = profile_id
        self.documents: Dict[str, Dict[str, Any]] = {}   # sha256 -> 文档记录
        self.elements: Dict[str, Dict[str, Any]] = {}    # fingerprint -> 要素记录
        self.element_order: List[str] = []              # 元素指纹（按分配顺序）
        self.doc_counter = 0
        self.element_counter = 0
        self.load()

    # -- 持久化 -------------------------------------------------------------
    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.profile_id = data.get("profile_id", self.profile_id)
        self.documents = data.get("documents") or {}
        self.elements = data.get("elements") or {}
        self.element_order = data.get("element_order") or list(self.elements.keys())
        self.doc_counter = int(data.get("doc_counter") or 0)
        self.element_counter = int(data.get("element_counter") or 0)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "profile_id": self.profile_id,
            "updated_at": _now(),
            "doc_counter": self.doc_counter,
            "element_counter": self.element_counter,
            "documents": self.documents,
            "element_order": self.element_order,
            "elements": self.elements,
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )

    # -- 计数器 -------------------------------------------------------------
    def next_doc_id(self) -> str:
        self.doc_counter += 1
        return f"D-{self.doc_counter:04d}"

    def next_element_id(self) -> str:
        self.element_counter += 1
        return f"E-{self.element_counter:04d}"

    # -- 查询 ---------------------------------------------------------------
    def element_list(self) -> List[Dict[str, Any]]:
        return [self.elements[fp] for fp in self.element_order if fp in self.elements]

    def doc_by_sha(self, sha: str) -> Optional[Dict[str, Any]]:
        return self.documents.get(sha)


# ---------------------------------------------------------------------------
# 导入结果
# ---------------------------------------------------------------------------


class ImportResult:
    """一次导入的产物：要素列表 + 校验报告。"""

    def __init__(self, workspace_root: Path, profile_id: str) -> None:
        self.workspace_root = Path(workspace_root)
        self.profile_id = profile_id
        self.elements: List[Dict[str, Any]] = []
        self.problems: List[Dict[str, Any]] = []
        self.documents: List[Dict[str, Any]] = []
        self.stats: Dict[str, Any] = {
            "files_seen": 0,
            "files_imported": 0,
            "files_skipped_duplicate": 0,
            "files_failed": 0,
            "elements_new": 0,
            "elements_reused": 0,
        }

    def add_problem(self, problem_class: str, subject: str, detail: str,
                    reason_code: str = "", element_ids: Optional[Sequence[str]] = None,
                    locator: str = "") -> None:
        assert problem_class in PROBLEM_CLASSES, f"非法问题分类：{problem_class}"
        if reason_code and reason_code not in REASON_CODES:
            raise ValueError(f"非法原因码：{reason_code}")
        self.problems.append({
            "problem_class": problem_class,
            "problem_class_label": PROBLEM_CLASS_LABELS[problem_class],
            "subject": subject,
            "detail": detail,
            "reason_code": reason_code,
            "element_ids": list(element_ids or []),
            "locator": locator,
        })

    def problem_counts(self) -> Dict[str, int]:
        counts = {c: 0 for c in PROBLEM_CLASSES}
        for p in self.problems:
            counts[p["problem_class"]] += 1
        return counts

    def summary(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "generated_at": _now(),
            "stats": dict(self.stats),
            "problem_counts": self.problem_counts(),
            "elements_total": len(self.elements),
            "documents": self.documents,
            "problems": self.problems,
        }


# ---------------------------------------------------------------------------
# 格式解析：把材料读成「候选段落」
# ---------------------------------------------------------------------------


def _read_text_file(path: Path) -> str:
    """读纯文本；UTF-8 失败再试 GB18030；都失败 → 不可读。"""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="gb18030")
        except (UnicodeDecodeError, LookupError) as exc:
            raise UnreadableDocument("编码无法识别", str(exc)) from exc
    except OSError as exc:
        raise UnreadableDocument("文件无法读取", str(exc)) from exc


def _split_md_sections(text: str) -> List[Tuple[str, str, int]]:
    """Markdown/纯文本 → [(section_title, line_text, physical_line_no)]。

    标题识别：# 开头（Markdown）或「数字/中文标题独占一行」。
    """
    out: List[Tuple[str, str, int]] = []
    section = ""
    for idx, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        title = ""
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
        elif re.match(r"^第[一二三四五六七八九十百]+[章节]", stripped):
            title = stripped
        elif (
            re.match(r"^\d+(?:\.\d+)*\s+\S{2,20}$", stripped)
            and len(stripped) <= 30
            and not re.search(r"[，。；,.;]", stripped)
        ):
            title = stripped
            title = stripped
        if title:
            section = title
            continue
        out.append((section, stripped, idx))
    return out


def _parse_txt_like(path: Path) -> List[Dict[str, Any]]:
    text = _read_text_file(path)
    if looks_like_garbage(text):
        raise UnreadableDocument(
            "内容不可辨认",
            f"可辨认字符占比 {printable_ratio(text):.2f}，疑似乱码/空文件/扫描件无文本层",
        )
    out: List[Dict[str, Any]] = []
    for section, line, lineno in _split_md_sections(text):
        out.append({
            "locator": f"L{lineno}",
            "locator_kind": "paragraph",
            "text": line,
            "section": section,
        })
    return out


def _parse_docx(path: Path) -> List[Dict[str, Any]]:
    try:
        import docx  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise UnreadableDocument("缺少 python-docx 依赖", str(exc)) from exc
    try:
        document = docx.Document(str(path))
    except Exception as exc:
        raise UnreadableDocument("DOCX 无法打开或已损坏/加密", str(exc)) from exc

    out: List[Dict[str, Any]] = []
    section = ""
    counter = 0
    try:
        paragraphs = list(document.paragraphs)
    except Exception as exc:
        raise UnreadableDocument("DOCX 正文无法读取", str(exc)) from exc
    for para in paragraphs:
        counter += 1
        text = normalize_ws(para.text or "")
        if not text:
            continue
        style = ""
        try:
            style = (para.style.name or "") if para.style is not None else ""
        except Exception:
            style = ""
        if style.lower().startswith("heading") or style.startswith("标题"):
            section = text
            continue
        out.append({
            "locator": f"p.{counter}",
            "locator_kind": "paragraph",
            "text": text,
            "section": section,
        })

    try:
        tables = list(document.tables)
    except Exception:
        tables = []
    for ti, table in enumerate(tables, start=1):
        for ri, row in enumerate(table.rows, start=1):
            cells: List[str] = []
            for cell in row.cells:
                text = normalize_ws(cell.text or "")
                if text:
                    cells.append(text)
            if not cells:
                continue
            out.append({
                "locator": f"t{ti} r{ri}",
                "locator_kind": "table_row",
                "text": "；".join(cells),
                "section": section,
            })
    if not out:
        raise UnreadableDocument("DOCX 无可提取文本", "文档为空或全部内容为图片")
    return out


def _parse_xlsx(path: Path) -> List[Dict[str, Any]]:
    """XLSX → 候选段落。

    行级优先：一行视为一个可审核对象，locator 形如 "Sheet1!A2:E2"（kind=table_row）；
    只有单个非空单元格的行退化为单元格定位 "Sheet1!B7"（kind=cell）。
    """
    try:
        import openpyxl  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise UnreadableDocument("缺少 openpyxl 依赖", str(exc)) from exc
    try:
        workbook = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    except Exception as exc:
        raise UnreadableDocument("XLSX 无法打开或已损坏/加密", str(exc)) from exc

    out: List[Dict[str, Any]] = []
    try:
        sheets = list(workbook.worksheets)
    except Exception as exc:
        raise UnreadableDocument("XLSX 工作表无法枚举", str(exc)) from exc
    for sheet in sheets:
        sheet_clause = ""
        try:
            rows = list(sheet.iter_rows(values_only=True))
        except Exception as exc:
            raise UnreadableDocument(
                "XLSX 工作表无法读取",
                f"{sheet.title}: {exc}",
            ) from exc
        for r_idx, row in enumerate(rows, start=1):
            cells: List[Tuple[int, str]] = []
            for c_idx, value in enumerate(row, start=1):
                if value is None:
                    continue
                text = normalize_ws(str(value))
                if not text:
                    continue
                cells.append((c_idx, text))
            if not cells:
                continue
            if r_idx == 1:
                # 表头行：只用来标注章节，不生成要素
                continue
            own_clause = ""
            for _, text in cells:
                found, _others = find_clause_no(text)
                if found:
                    own_clause = found
                    break
            clause = own_clause or sheet_clause
            if own_clause:
                sheet_clause = own_clause
            if len(cells) == 1:
                c_idx, text = cells[0]
                out.append({
                    "locator": f"{sheet.title}!{_cell_coord(r_idx, c_idx)}",
                    "locator_kind": "cell",
                    "text": text,
                    "section": sheet.title,
                    "clause_hint": clause,
                })
            else:
                first_col = cells[0][0]
                last_col = cells[-1][0]
                text = "；".join(t for _, t in cells)
                out.append({
                    "locator": (
                        f"{sheet.title}!{_cell_coord(r_idx, first_col)}:"
                        f"{_cell_coord(r_idx, last_col)}"
                    ),
                    "locator_kind": "table_row",
                    "text": text,
                    "section": sheet.title,
                    "clause_hint": clause,
                })
    try:
        workbook.close()
    except Exception:
        pass
    if not out:
        raise UnreadableDocument("XLSX 无可提取内容", "所有单元格为空")
    return out



def _cell_coord(row: int, col: int) -> str:
    letters = ""
    c = col
    while c > 0:
        c, rem = divmod(c - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return f"{letters}{row}"


def _parse_pdf(path: Path) -> List[Dict[str, Any]]:
    try:
        import pymupdf  # type: ignore
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise UnreadableDocument("缺少 pymupdf/fitz 依赖", str(exc)) from exc
    try:
        doc = pymupdf.open(str(path))
    except Exception as exc:
        raise UnreadableDocument("PDF 无法打开或已损坏/加密", str(exc)) from exc

    out: List[Dict[str, Any]] = []
    empty_pages: List[int] = []
    try:
        for pno in range(doc.page_count):
            page = doc.load_page(pno)
            try:
                blocks = page.get_text("blocks")
            except Exception as exc:
                raise UnreadableDocument("PDF 页面文本层无法读取", str(exc)) from exc
            para_index = 0
            for block in blocks:
                if len(block) < 5:
                    continue
                btype = block[6] if len(block) > 6 else 0
                if btype != 0:
                    continue
                for raw_line in str(block[4]).splitlines():
                    text = normalize_ws(raw_line)
                    if not text:
                        continue
                    para_index += 1
                    out.append({
                        "locator": f"p.{pno + 1} ¶{para_index}",
                        "locator_kind": "page",
                        "text": text,
                        "section": f"p.{pno + 1}",
                    })
            if para_index == 0:
                empty_pages.append(pno + 1)
    finally:
        try:
            doc.close()
        except Exception:
            pass
    if not out:
        raise UnreadableDocument(
            "PDF 无可提取文本",
            f"共 {len(empty_pages)} 页无文本层，疑似扫描件（未 OCR）",
        )
    return out


# ---------------------------------------------------------------------------
# 导入器
# ---------------------------------------------------------------------------


def _element_unobtained(clause_no: str, locator: str, source_ref: str,
                        applicability: str, doc_version: str) -> List[str]:
    """登记该要素中「未取得」的字段（票面验收第 2 条：宁缺留空并标未取得）。

    缺项一律留空 + 记入 unobtained，绝不臆造内容。
    """
    missing: List[str] = []
    if not clause_no:
        missing.append("条款号")
    if not locator or locator == source_ref:
        missing.append("定位")
    if not doc_version:
        missing.append("版本号")
    if applicability != "applicable":
        missing.append("适用性")
    return missing


class Importer:
    """把体系文件导入为要素序列。"""

    def __init__(self, workspace_root: Path, state: ImportState,
                 clause_tree: Optional[ClauseTree] = None) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.state = state
        self.clause_tree = clause_tree or load_clause_tree(self.workspace_root, state.profile_id)

    # -- 对外入口 -----------------------------------------------------------
    def import_paths(self, paths: Iterable[Path],
                    expected: Optional[Sequence[Path]] = None) -> ImportResult:
        result = ImportResult(self.workspace_root, self.state.profile_id)
        files = [Path(p) for p in paths]
        seen_sha: Dict[str, str] = {}
        for path in files:
            self._import_one(path, result, seen_sha)
        if expected:
            self._check_expected(expected, result, seen_sha)
        result.elements = self.state.element_list()
        self.state.save()
        return result

    def import_directory(self, directory: Path, recursive: bool = True) -> ImportResult:
        directory = Path(directory)
        found: List[Path] = []
        for p in sorted(directory.rglob("*") if recursive else directory.glob("*")):
            if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES:
                found.append(p)
        return self.import_paths(found)

    # -- 内部实现 -----------------------------------------------------------
    def _rel(self, path: Path) -> str:
        try:
            return str(Path(path).resolve().relative_to(self.workspace_root)).replace(os.sep, "/")
        except ValueError:
            return str(Path(path).resolve()).replace(os.sep, "/")

    def _check_expected(self, expected: Sequence[Path], result: ImportResult,
                        seen_sha: Dict[str, str]) -> None:
        for path in expected:
            p = Path(path)
            if not p.is_absolute():
                p = self.workspace_root / p
            if p.is_file():
                continue
            result.add_problem(
                "missing",
                self._rel(p),
                "应索取的证据项未取得任何材料（未提交）",
                reason_code="EVIDENCE_MISSING",
            )

    def _import_one(self, path: Path, result: ImportResult,
                    seen_sha: Dict[str, str]) -> None:
        path = Path(path)
        result.stats["files_seen"] += 1
        if not path.is_file():
            result.add_problem(
                "missing",
                self._rel(path),
                "文件不存在：该证据项未取得",
                reason_code="EVIDENCE_MISSING",
            )
            result.stats["files_failed"] += 1
            return

        data = path.read_bytes()
        sha = sha256_bytes(data)
        if sha in seen_sha:
            result.stats["files_skipped_duplicate"] += 1
            result.add_problem(
                "pending_check",
                self._rel(path),
                f"与已导入文件内容重复（sha256={sha[:12]}…，同 {seen_sha[sha]}）",
                reason_code="EVIDENCE_MISSING",
            )
            return
        seen_sha[sha] = self._rel(path)

        known = self.state.doc_by_sha(sha)
        if known is not None:
            # 文件级幂等：同一份内容不重复导入，不新增要素
            result.stats["files_skipped_duplicate"] += 1
            aliases = known.setdefault("aliases", [])
            rel = self._rel(path)
            if rel != known.get("path") and rel not in aliases:
                aliases.append(rel)
            result.documents.append({
                "source_doc_id": known["source_doc_id"],
                "path": known.get("path"),
                "sha256": sha,
                "status": "skipped_duplicate",
                "element_count": len(known.get("element_ids") or []),
            })
            return

        suffix = path.suffix.lower()
        try:
            if suffix == ".pdf":
                chunks = _parse_pdf(path)
            elif suffix == ".docx":
                chunks = _parse_docx(path)
            elif suffix == ".xlsx":
                chunks = _parse_xlsx(path)
            elif suffix in (".txt", ".md", ".markdown"):
                chunks = _parse_txt_like(path)
            else:
                result.add_problem(
                    "unreadable",
                    self._rel(path),
                    f"不支持的文件格式：{suffix}",
                    reason_code="EVIDENCE_UNREADABLE",
                )
                result.stats["files_failed"] += 1
                return
        except UnreadableDocument as exc:
            result.add_problem(
                "unreadable",
                self._rel(path),
                f"{exc.reason}：{exc.detail}" if exc.detail else exc.reason,
                reason_code="EVIDENCE_UNREADABLE",
            )
            result.stats["files_failed"] += 1
            return
        except Exception as exc:  # 兜底：任何解析异常都归为不可读，不吞掉
            result.add_problem(
                "unreadable",
                self._rel(path),
                f"解析异常：{type(exc).__name__}: {exc}",
                reason_code="EVIDENCE_UNREADABLE",
            )
            result.stats["files_failed"] += 1
            return

        source_doc_id = self.state.next_doc_id()
        doc_record = {
            "source_doc_id": source_doc_id,
            "path": self._rel(path),
            "sha256": sha,
            "size": len(data),
            "title": path.stem,
            "doc_version": self._detect_version(chunks),
            "imported_at": _now(),
            "element_ids": [],
            "aliases": [],
        }
        self.state.documents[sha] = doc_record

        elements = self._build_elements(source_doc_id, doc_record, chunks, result)
        doc_record["element_ids"] = [e["element_id"] for e in elements]
        result.stats["files_imported"] += 1
        result.documents.append({
            "source_doc_id": source_doc_id,
            "path": doc_record["path"],
            "sha256": sha,
            "status": "imported",
            "element_count": len(elements),
        })

    def _detect_version(self, chunks: Sequence[Dict[str, Any]]) -> str:
        """从材料里探测版本标识（如 A/1.0）。"""
        for chunk in chunks[:40]:
            m = re.search(r"版本[:：]\s*([A-Za-z0-9][A-Za-z0-9./\-]{0,19})", chunk["text"])
            if m:
                return m.group(1)
        return ""

    def _build_elements(self, source_doc_id: str, doc_record: Dict[str, Any],
                        chunks: Sequence[Dict[str, Any]], result: ImportResult) -> List[Dict[str, Any]]:
        elements: List[Dict[str, Any]] = []
        for chunk in chunks:
            raw = chunk["text"]
            explicit_clause, explicit_type, explicit_applicability, conflict_marked = self._parse_markup(raw)
            quote = truncate_quote(raw)
            if not quote:
                continue
            locator = chunk["locator"]
            locator_kind = chunk["locator_kind"]
            clause_no = explicit_clause or chunk.get("clause_hint") or ""
            if not clause_no:
                found, others = find_clause_no(raw)
                clause_no = found
            else:
                others = []
            if not clause_no:
                _, others = find_clause_no(raw)
            element_type = detect_element_type(raw, explicit_type)
            applicability = parse_applicability(explicit_applicability)
            tags: List[str] = []
            if chunk.get("section"):
                tags.append("section:" + str(chunk["section"])[:60])
            if others:
                tags.append("also:" + ",".join(others[:4]))
            if conflict_marked:
                tags.append("conflict_declared")
            if applicability == "unknown" and ("不适用" in raw or "未提供" in raw):
                tags.append("mentions_na_unconfirmed")

            fingerprint = element_fingerprint(source_doc_id, locator, quote)
            existing = self.state.elements.get(fingerprint)
            if existing is not None:
                # 元素级幂等：同 source_doc_id+locator+quote 已有 → 复用，不新增
                result.stats["elements_reused"] += 1
                element = existing
            else:
                element = {
                    "element_id": self.state.next_element_id(),
                    "source_doc_id": source_doc_id,
                    "source_ref": doc_record["path"],
                    "locator": locator,
                    "locator_kind": locator_kind,
                    "quote": quote,
                    "element_type": element_type,
                    "element_type_label": ELEMENT_TYPE_LABELS[element_type],
                    "clause_no": clause_no,
                    "applicability": applicability,
                    "applicability_label": APPLICABILITY_LABELS[applicability],
                    "material_version": doc_record.get("doc_version") or "",
                    "tags": tags,
                    "fingerprint": fingerprint,
                    "doc_sha256": doc_record["sha256"],
                    "imported_at": _now(),
                    "unobtained": _element_unobtained(
                        clause_no, locator, doc_record["path"],
                        applicability, doc_record.get("doc_version") or "",
                    ),
                }
                self.state.elements[fingerprint] = element
                self.state.element_order.append(fingerprint)
                result.stats["elements_new"] += 1
                if "unobtained" not in element:
                    element["unobtained"] = _element_unobtained(
                        clause_no, locator, doc_record["path"],
                        applicability, doc_record.get("doc_version") or "",
                    )
            elements.append(element)

        self._validate_elements(source_doc_id, elements, result)
        return elements

    @staticmethod
    def _parse_markup(raw: str) -> Tuple[str, str, str, bool]:
        """解析显式标记。

        支持写法：
          [8.5.2] ...                     → 指定条款号
          ... | requirement | 适用 |      → 管道语法：类型 | 适用性
          ... | conflict                  → 显式冲突声明
        """
        clause_no = ""
        explicit_type = ""
        applicability = ""
        conflict = False
        m = re.match(r"^\s*\[([0-9.]+)\]", raw)
        if m:
            clause_no = m.group(1).strip(".")
        parts = [p.strip() for p in raw.split("|")]
        for part in parts[1:]:
            low = part.lower()
            if part in ELEMENT_TYPES or any(t in part for t in ELEMENT_TYPE_LABELS):
                explicit_type = part
            elif part in ("适用", "不适用", "待确认") or low in APPLICABILITY_STATES:
                applicability = part
            elif low == "conflict" or part == "冲突":
                conflict = True
        return clause_no, explicit_type, applicability, conflict

    def _validate_elements(self, source_doc_id: str, elements: List[Dict[str, Any]],
                           result: ImportResult) -> None:
        """逐要素校验：条款号在不在条款树、定位是否具体、版本是否标注。"""
        by_clause: Dict[str, List[Dict[str, Any]]] = {}
        for element in elements:
            by_clause.setdefault(element["clause_no"], []).append(element)

            clause_no = element["clause_no"]
            if not clause_no:
                result.add_problem(
                    "pending_check",
                    f"{element['source_ref']}#{element['locator']}",
                    f"要素 {element['element_id']} 未关联条款号（未取得），无法定位审核点",
                    reason_code="APPLICABILITY_UNKNOWN",
                    element_ids=[element["element_id"]],
                    locator=element["locator"],
                )
            elif not self.clause_tree.known(clause_no):
                result.add_problem(
                    "pending_check",
                    f"{element['source_ref']}#{element['locator']}",
                    f"要素 {element['element_id']} 的条款号 {clause_no} 不在条款树 "
                    f"{self.clause_tree.source} 中",
                    reason_code="CLAUSE_NOT_IN_TREE",
                    element_ids=[element["element_id"]],
                    locator=element["locator"],
                )
            if not element["locator"] or element["locator"] in (element["source_ref"],):
                result.add_problem(
                    "pending_check",
                    f"{element['source_ref']}#{element['element_id']}",
                    f"要素 {element['element_id']} 定位未取得或不具体（禁止只写文件名）",
                    reason_code="EVIDENCE_MISSING",
                    element_ids=[element["element_id"]],
                    locator=element["locator"],
                )
            if not element["material_version"]:
                result.add_problem(
                    "pending_check",
                    f"{element['source_ref']}#{element['locator']}",
                    f"要素 {element['element_id']} 所属材料版本号未取得（未标注版本号）",
                    reason_code="EVIDENCE_MISSING",
                    element_ids=[element["element_id"]],
                    locator=element["locator"],
                )

        # 冲突检测：同一文档同一条款下出现互斥陈述
        for clause_no, group in by_clause.items():
            if len(group) < 2:
                continue
            flagged = [e for e in group if "conflict_declared" in e["tags"]]
            implicit = [e for e in group if _has_conflict_wording(e["quote"])]
            if not flagged and not implicit:
                continue
            sides = [e["element_id"] for e in group]
            result.add_problem(
                "conflict",
                f"{group[0]['source_ref']}#{clause_no}",
                f"条款 {clause_no or '(未标条款)'} 在同一材料内存在互斥陈述，"
                f"需现场反查或第二来源澄清",
                reason_code="EVIDENCE_CONFLICT",
                element_ids=sides,
                locator="; ".join(e["locator"] for e in group),
            )


# ---------------------------------------------------------------------------
# 校验报告输出
# ---------------------------------------------------------------------------


def write_validation_report(result: ImportResult, out_dir: Path, task_id: str,
                            node_id: str, state_name: str = "waiting",
                            version: str = "v1") -> Dict[str, str]:
    """输出校验报告（JSON + Markdown）。

    命名遵循 output-formats.md §2.2：<任务标识>_<节点编号>_<成果名称>_<版本>.<扩展名>。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"{task_id}_{node_id}_资料导入校验报告-{state_name}_{version}"
    json_path = out_dir / f"{base}.json"
    md_path = out_dir / f"{base}.md"
    payload = result.summary()
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines: List[str] = []
    lines.append(f"# 资料导入校验报告 · {task_id} · {node_id}")
    lines.append("")
    lines.append(f"- 生成时间：{payload['generated_at']}")
    lines.append(f"- 标准画像：{result.profile_id}")
    lines.append(
        "- 统计：文件 {} 个（导入 {} / 重复跳过 {} / 失败 {}），要素 {} 条"
        "（新增 {} / 复用 {}）".format(
            payload["stats"]["files_seen"],
            payload["stats"]["files_imported"],
            payload["stats"]["files_skipped_duplicate"],
            payload["stats"]["files_failed"],
            payload["elements_total"],
            payload["stats"]["elements_new"],
            payload["stats"]["elements_reused"],
        )
    )
    counts = payload["problem_counts"]
    lines.append(
        "- 问题分类："
        + "、".join(
            f"{PROBLEM_CLASS_LABELS[c]} {counts[c]}" for c in PROBLEM_CLASSES
        )
    )
    lines.append("")
    lines.append("## 问题项")
    lines.append("")
    if not result.problems:
        lines.append("（无）")
    else:
        lines.append("| 分类 | 对象 | 原因码 | 说明 | 涉及要素 |")
        lines.append("|---|---|---|---|---|")
        for p in result.problems:
            lines.append(
                "| {cls} | {subj} | {rc} | {detail} | {els} |".format(
                    cls=p["problem_class_label"],
                    subj=p["subject"].replace("|", "/"),
                    rc=p["reason_code"] or "-",
                    detail=p["detail"].replace("|", "/"),
                    els=", ".join(p["element_ids"]) or "-",
                )
            )
    lines.append("")
    lines.append("## 导入文件")
    lines.append("")
    lines.append("| source_doc_id | 路径 | sha256 | 状态 | 要素数 |")
    lines.append("|---|---|---|---|---|")
    for d in result.documents:
        lines.append(
            "| {sid} | {p} | {sha} | {st} | {n} |".format(
                sid=d["source_doc_id"],
                p=d["path"],
                sha=d["sha256"][:12] + "…",
                st=d["status"],
                n=d["element_count"],
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def write_elements_csv(elements: Sequence[Dict[str, Any]], out_dir: Path, task_id: str,
                        node_id: str, state_name: str = "waiting",
                        version: str = "v1") -> str:
    """要素清单 CSV（供人工核对，不属于 output-formats.md §2.1 的五件正式产物）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{task_id}_{node_id}_要素清单-{state_name}_{version}.csv"
    fields = [
        "element_id", "source_doc_id", "source_ref", "locator", "locator_kind",
        "element_type", "element_type_label", "clause_no", "applicability",
        "applicability_label", "material_version", "quote", "tags", "fingerprint", "unobtained",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for element in elements:
            row = {k: element.get(k, "") for k in fields}
            row["tags"] = ";".join(element.get("tags") or [])
            row["unobtained"] = ";".join(element.get("unobtained") or [])
            writer.writerow(row)
    return str(path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _collect_inputs(inputs: Sequence[str]) -> List[Path]:
    out: List[Path] = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            for child in sorted(p.rglob("*")):
                if child.is_file() and child.suffix.lower() in SUPPORTED_SUFFIXES:
                    out.append(child)
        else:
            out.append(p)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="资料导入与要素抽取（Issue #7，纯文件实现）"
    )
    parser.add_argument("--workspace", default=".", help="仓库根目录（默认当前目录）")
    parser.add_argument("--input", action="append", default=[], help="待导入的文件或目录，可重复")
    parser.add_argument("--expect", action="append", default=[], help="应到未到的材料清单，可重复")
    parser.add_argument("--profile", default="qms-gbt19001-2016", help="标准画像 id")
    parser.add_argument("--state", default=".research-tmp/import_state.json", help="导入状态文件")
    parser.add_argument("--out", default=".research-tmp/import", help="校验报告输出目录")
    parser.add_argument("--task-id", default="TASK-2026-QMS-A", help="任务标识")
    parser.add_argument("--node-id", default="N12", help="节点编号")
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).resolve()
    state_path = Path(args.state)
    if not state_path.is_absolute():
        state_path = workspace / state_path
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = workspace / out_dir

    state = ImportState(state_path, profile_id=args.profile)
    importer = Importer(workspace, state)
    inputs = _collect_inputs(args.input)
    expected = [Path(x) for x in args.expect]
    result = importer.import_paths(inputs, expected=expected)

    reports = write_validation_report(result, out_dir, args.task_id, args.node_id)
    csv_path = write_elements_csv(result.elements, out_dir, args.task_id, args.node_id)

    print("导入完成：")
    print(f"  文件：{result.stats}")
    print(f"  要素总数：{len(result.elements)}")
    print(f"  问题：{result.problem_counts()}")
    print(f"  校验报告 JSON：{reports['json']}")
    print(f"  校验报告 MD  ：{reports['markdown']}")
    print(f"  要素清单 CSV：{csv_path}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())


