#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""依据库与依据版本核验（basis_db）。

对应 GitHub Issue #20「依据库与依据版本核验」。

规格依据：
  - docs/spec/verdict-anchor-rules.md §7「依据库、依据快照与可核验性」
    快照字段名照抄：source_ref / source_id / version / locator /
    snapshot_sha256 / retrieved_at / status（current | superseded | unverifiable）
  - docs/spec/verdict-anchor-rules.md §7.3「与 #20 依据库的联动」
    核验返回退出码 3（无法判定）时，该条款只能落 insufficient，
    不得凭标准文本或模型记忆补证。

种子依据来源：
  - docs/research/standard-version-watch.md（三体系标准版本现状与换版时间线）
  - audit-point-library/registry.yaml（画像 standard 段与 clause_tree_ref）
  - audit-point-library/clause_trees/*.yaml（条款号清单）

模块提供三件事：
  1. SQLite 依据库（三表：documents / document_versions / checks）
  2. 命令行：download / check / list
  3. 供 #12 判定引擎调用的 Python API：
     resolve_clause_basis(conn, profile_id, clause_no) -> dict
     is_basis_current(conn, source_ref) -> str

红线（§0 红线一）：本模块不猜测标准内容、不复制标准正文；
不联网抓取标准正文，只在 --live 模式下访问来源页面的「名称标题」。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "SCHEMA_VERSION",
    "STATUS_CURRENT",
    "STATUS_SUPERSEDED",
    "STATUS_UNVERIFIABLE",
    "CHECK_RESULTS",
    "VERIFICATION_CANDIDATE",
    "VERIFICATION_PENDING_REVIEW",
    "VERIFICATION_HUMAN_VERIFIED",
    "VERIFICATION_STATES",
    "EXIT_OK",
    "EXIT_SUPERSEDED",
    "EXIT_UNVERIFIABLE",
    "EXIT_USAGE",
    "BasisError",
    "ProfileNotFound",
    "ClauseNotInTree",
    "BasisNotRegistered",
    "init_db",
    "open_db",
    "register_document",
    "add_version",
    "record_check",
    "resolve_clause_basis",
    "is_basis_current",
    "set_version_verification",
    "load_registry",
    "registry_entries",
    "find_snapshot",
    "find_profile",
    "load_clause_tree",
    "collect_clause_numbers",
    "designation_to_source_id",
    "derive_snapshot_status",
    "list_documents",
    "compare_stored_vs_source",
    "cmd_review",
    "main",
]

SCHEMA_VERSION = 1

# --------------------------------------------------------------------------
# 状态常量（照抄 docs/spec/verdict-anchor-rules.md §7.1 / §7.2）
# --------------------------------------------------------------------------
STATUS_CURRENT = "current"
STATUS_SUPERSEDED = "superseded"
STATUS_UNVERIFIABLE = "unverifiable"
CHECK_RESULTS = (STATUS_CURRENT, STATUS_SUPERSEDED, STATUS_UNVERIFIABLE)

# 核验状态（三值）：加在 document_versions 上
VERIFICATION_CANDIDATE = "candidate"
VERIFICATION_PENDING_REVIEW = "pending_review"
VERIFICATION_HUMAN_VERIFIED = "human_verified"
VERIFICATION_STATES = (
    VERIFICATION_CANDIDATE,
    VERIFICATION_PENDING_REVIEW,
    VERIFICATION_HUMAN_VERIFIED,
)

# --------------------------------------------------------------------------
# 退出码
# --------------------------------------------------------------------------
EXIT_OK = 0            # 成功且判定明确
EXIT_SUPERSEDED = 2    # 判定为非最新版或已更新
EXIT_UNVERIFIABLE = 3  # 无法判定
EXIT_USAGE = 4         # 参数或库状态错误

# --------------------------------------------------------------------------
# 默认路径
# --------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent          # <repo>/tools
REPO_ROOT = _HERE.parent                          # <repo>
DEFAULT_DB_PATH = _HERE / "basis_snapshots" / "basis.sqlite3"
DEFAULT_REGISTRY_PATH = _HERE / "basis_snapshots" / "registry.json"
DEFAULT_LIBRARY_ROOT = REPO_ROOT / "audit-point-library"

# 源记录里出现这些词，说明来源已声明该版本被撤回 / 作废 → superseded
WITHDRAWN_MARKERS = (
    "撤回",
    "废止",
    "作废",
    "withdrawn",
    "Withdrawn",
    "WITHDRAWN",
    "superseded",
)

# 表结构：字段名照抄票面
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    id           INTEGER PRIMARY KEY,
    source_id    TEXT    NOT NULL UNIQUE,
    publisher    TEXT,
    title        TEXT,
    number       TEXT,
    category     TEXT,
    listing_url  TEXT,
    file_url     TEXT,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS document_versions (
    id           INTEGER PRIMARY KEY,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    title        TEXT,
    filename     TEXT,
    url          TEXT,
    size         INTEGER,
    sha256       TEXT,
    content      BLOB,
    downloaded_at TEXT   NOT NULL,
    note         TEXT,
    verification_status TEXT NOT NULL DEFAULT 'candidate',
    verified_by  TEXT,
    verified_at  TEXT
);

CREATE TABLE IF NOT EXISTS checks (
    id            INTEGER PRIMARY KEY,
    document_id   INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    checked_at    TEXT    NOT NULL,
    result        TEXT    NOT NULL,
    stored_title  TEXT,
    source_titles TEXT,
    detail        TEXT,
    next_action   TEXT
);

CREATE INDEX IF NOT EXISTS idx_document_versions_doc ON document_versions(document_id);
CREATE INDEX IF NOT EXISTS idx_checks_doc ON checks(document_id);
"""


# --------------------------------------------------------------------------
# 异常
# --------------------------------------------------------------------------
class BasisError(Exception):
    """依据库通用错误（参数或库状态错误，映射到退出码 4）。"""


class ProfileNotFound(BasisError):
    """画像 id 在 audit-point-library/registry.yaml 中不存在。"""


class ClauseNotInTree(BasisError):
    """条款号不在该画像的条款树中。"""


class BasisNotRegistered(BasisError):
    """依据未登记进快照台账 registry.json。"""


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def _now() -> str:
    """UTC 时间戳（ISO 8601，秒精度）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data: bytes) -> str:
    """对字节串求 sha256（十六进制小写）。"""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(bytes(data)).hexdigest()


def _normalize(text: Optional[str]) -> str:
    """名称/标题归一化：大小写、空白、常见标点差异不应造成误判。"""
    if text is None:
        return ""
    s = str(text).strip().lower()
    s = re.sub(r"[\s　]+", " ", s)
    s = re.sub(r"[:：\-–—_/、,，.。()（）\[\]【】'\"“”‘’]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _year_tokens(text: Optional[str]) -> Tuple[str, ...]:
    """抽取版本标识里的年份序列，如 'ISO 9001:2015/Amd 1:2024' -> ('2015','2024')。

    只取独立的四位年份（前后不得再接数字），避免把标准序号
    'GB/T 19001-2016' 里的 '1900' 当成年份。
    """
    if not text:
        return ()
    return tuple(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", str(text)))


def designation_to_source_id(designation: Optional[str]) -> str:
    """标准代号 → source_id。

    ISO 风格 'ISO 9001:2015' 原样；GB 风格 'GB/T 19001-2016' → 'GB/T 19001:2016'。
    """
    d = (designation or "").strip()
    if not d:
        return ""
    if ":" in d:
        return d
    m = re.match(r"^(.*?)[\s\-]+((?:19|20)\d{2})$", d)
    if m:
        return f"{m.group(1)}:{m.group(2)}"
    return d


def _guess_category(source_id: str) -> str:
    sid = (source_id or "").upper()
    if sid.startswith("GB"):
        return "国家标准"
    if sid.startswith("ISO") or sid.startswith("IEC"):
        return "国际标准"
    if sid.startswith("CNCA") or sid.startswith("CNAS"):
        return "认证认可规范"
    return "其他"


# --------------------------------------------------------------------------
# 数据库
# --------------------------------------------------------------------------
def init_db(path) -> sqlite3.Connection:
    """创建（或打开）依据库并保证三表存在，返回已提交的连接。"""
    p = Path(path)
    if p.parent and str(p.parent):
        p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    return conn


def open_db(path) -> sqlite3.Connection:
    """打开已存在的依据库；不存在或结构不完整时抛 BasisError（退出码 4）。"""
    p = Path(path)
    if not p.exists():
        raise BasisError(
            f"依据库不存在：{p}；请先执行 python tools/basis_db.py download <source_id> 初始化"
        )
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    have = {r["name"] for r in rows}
    missing = {"documents", "document_versions", "checks"} - have
    if missing:
        conn.close()
        raise BasisError(f"依据库结构不完整：{p} 缺少表 {sorted(missing)}")
    return conn


def register_document(
    conn: sqlite3.Connection,
    source_id: str,
    publisher: Optional[str],
    title: Optional[str],
    number: Optional[str],
    category: Optional[str],
    listing_url: Optional[str],
    file_url: Optional[str],
) -> int:
    """登记（或更新）一份依据文档，返回 document_id。"""
    if not source_id or not str(source_id).strip():
        raise BasisError("source_id 不能为空")
    sid = str(source_id).strip()
    cat = category or _guess_category(sid)
    now = _now()
    row = conn.execute(
        "SELECT id FROM documents WHERE source_id = ?", (sid,)
    ).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE documents SET publisher=?, title=?, number=?, category=?, "
            "listing_url=?, file_url=?, updated_at=? WHERE id=?",
            (publisher, title, number, cat, listing_url, file_url, now, row["id"]),
        )
        conn.commit()
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO documents (source_id, publisher, title, number, category, "
        "listing_url, file_url, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (sid, publisher, title, number, cat, listing_url, file_url, now, now),
    )
    conn.commit()
    return int(cur.lastrowid)


def add_version(
    conn: sqlite3.Connection,
    document_id: int,
    title: Optional[str],
    filename: Optional[str],
    url: Optional[str],
    content_bytes: bytes,
    note: Optional[str] = None,
) -> int:
    """为文档新增一个版本快照（自动计算 sha256），返回 version_id。

    新版本一律以 verification_status='candidate' 落库：未经人工复核。
    """
    if content_bytes is None:
        raise BasisError("content_bytes 不能为空")
    if isinstance(content_bytes, str):
        content_bytes = content_bytes.encode("utf-8")
    data = bytes(content_bytes)
    digest = sha256_bytes(data)
    cur = conn.execute(
        "INSERT INTO document_versions (document_id, title, filename, url, size, "
        "sha256, content, downloaded_at, note, verification_status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            document_id,
            title,
            filename,
            url,
            len(data),
            digest,
            sqlite3.Binary(data),
            _now(),
            note,
            VERIFICATION_CANDIDATE,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def record_check(
    conn: sqlite3.Connection,
    document_id: int,
    result: str,
    stored_title: Optional[str],
    source_titles: Optional[str],
    detail: Optional[str],
    next_action: Optional[str],
) -> int:
    """记录一次核验结果（result ∈ current / superseded / unverifiable）。"""
    if result not in CHECK_RESULTS:
        raise BasisError(
            f"result 必须是 {CHECK_RESULTS} 之一，收到 {result!r}"
        )
    if isinstance(source_titles, (list, tuple, set)):
        source_titles = "; ".join(str(x) for x in source_titles)
    cur = conn.execute(
        "INSERT INTO checks (document_id, checked_at, result, stored_title, "
        "source_titles, detail, next_action) VALUES (?,?,?,?,?,?,?)",
        (
            document_id,
            _now(),
            result,
            stored_title,
            source_titles,
            detail,
            next_action,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def set_version_verification(
    conn: sqlite3.Connection,
    version_id: int,
    new_status: str,
    reviewer: str,
    note: Optional[str] = None,
) -> int:
    """人工复核流转：candidate -> pending_review -> human_verified。

    留痕方式：在 document_versions.note 追加一行不可变审计记录，
    并写 verified_by / verified_at。
    """
    if new_status not in VERIFICATION_STATES:
        raise BasisError(
            f"verification_status 必须是 {VERIFICATION_STATES} 之一，收到 {new_status!r}"
        )
    if not reviewer or not str(reviewer).strip():
        raise BasisError("人工复核必须记录复核人 reviewer")
    row = conn.execute(
        "SELECT id, note, verification_status FROM document_versions WHERE id = ?",
        (version_id,),
    ).fetchone()
    if row is None:
        raise BasisError(f"版本快照不存在：version_id={version_id}")
    old = row["verification_status"]
    ts = _now()
    trail = (
        f"[{ts}] {old} -> {new_status} by={reviewer}"
        + (f" 备注：{note}" if note else "")
    )
    new_note = (row["note"] + " | " + trail) if row["note"] else trail
    conn.execute(
        "UPDATE document_versions SET verification_status = ?, verified_by = ?, "
        "verified_at = ?, note = ? WHERE id = ?",
        (new_status, str(reviewer).strip(), ts, new_note, version_id),
    )
    conn.commit()
    return int(version_id)


# --------------------------------------------------------------------------
# 快照台账（tools/basis_snapshots/registry.json）
# --------------------------------------------------------------------------
def load_registry(path=None) -> Dict[str, Any]:
    """读取快照台账 JSON。"""
    p = Path(path) if path else DEFAULT_REGISTRY_PATH
    if not p.exists():
        raise BasisError(f"快照台账不存在：{p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BasisError(f"快照台账 JSON 解析失败：{p}：{exc}") from exc
    if not isinstance(data.get("snapshots"), list):
        raise BasisError(f"快照台账格式错误：{p} 缺少 snapshots 数组")
    return data


def registry_entries(path=None) -> List[Dict[str, Any]]:
    """返回台账中的全部依据记录。"""
    return list(load_registry(path).get("snapshots") or [])


def find_snapshot(source_id: str, path=None) -> Optional[Dict[str, Any]]:
    """按 source_id 精确查台账；查不到再按 number 兜底。"""
    sid = str(source_id or "").split("#", 1)[0].strip()
    entries = registry_entries(path)
    for e in entries:
        if str(e.get("source_id") or "").strip() == sid:
            return e
    for e in entries:
        if str(e.get("number") or "").strip() == sid:
            return e
    return None


def derive_snapshot_status(
    entry: Dict[str, Any],
    db_status: Optional[str] = None,
    db_verification: Optional[str] = None,
) -> str:
    """按 §7.2 推导快照三态。

    优先级：
      1. 来源记录声明已撤回/作废        → superseded
      2. 库内最近一次核验为 superseded / unverifiable → 沿用
      3. 没有快照哈希（文件缺失）        → unverifiable
      4. 未经有权人员人工复核            → unverifiable
      5. 其余                            → current

    db_verification 是库内 document_versions.verification_status（人工复核闸口），
    为空时回落到台账里的 verification_status。
    """
    evidence = str(entry.get("status_evidence") or "")
    if any(marker in evidence for marker in WITHDRAWN_MARKERS):
        return STATUS_SUPERSEDED
    if db_status in (STATUS_SUPERSEDED, STATUS_UNVERIFIABLE):
        return db_status
    if not entry.get("snapshot_sha256"):
        return STATUS_UNVERIFIABLE
    verification = db_verification or entry.get("verification_status")
    if verification != VERIFICATION_HUMAN_VERIFIED:
        return STATUS_UNVERIFIABLE
    return STATUS_CURRENT


# --------------------------------------------------------------------------
# 画像与条款树（audit-point-library/）
# --------------------------------------------------------------------------
def _load_yaml(path: Path) -> Any:
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - 环境缺依赖时的明确报错
        raise BasisError(
            f"读取 {path} 需要 PyYAML，但当前解释器未安装：{exc}"
        ) from exc
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BasisError(f"文件不存在：{path}") from exc


def find_profile(profile_id: str, library_root=None) -> Dict[str, Any]:
    """在 audit-point-library/registry.yaml 中查画像。"""
    root = Path(library_root) if library_root else DEFAULT_LIBRARY_ROOT
    p = root / "registry.yaml"
    if not p.exists():
        raise ProfileNotFound(f"审核点库登记表不存在：{p}")
    data = _load_yaml(p) or {}
    for prof in data.get("profiles") or []:
        if str(prof.get("id")) == str(profile_id):
            return prof
    known = [str(x.get("id")) for x in (data.get("profiles") or [])]
    raise ProfileNotFound(
        f"画像 {profile_id!r} 未登记在 {p}；已知画像：{known}"
    )


def load_clause_tree(profile: Dict[str, Any], library_root=None) -> Dict[str, Any]:
    """按画像的 clause_tree_ref 载入条款树。"""
    root = Path(library_root) if library_root else DEFAULT_LIBRARY_ROOT
    ref = str(profile.get("clause_tree_ref") or "").strip()
    if not ref:
        raise BasisError(f"画像 {profile.get('id')!r} 未声明 clause_tree_ref")
    p = root / ref
    if not p.exists():
        raise BasisError(f"条款树文件不存在：{p}")
    return _load_yaml(p) or {}


def collect_clause_numbers(tree: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """把条款树摊平成 {clause_no: title}。"""
    out: Dict[str, Optional[str]] = {}

    def walk(nodes: Any) -> None:
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            no = node.get("clause_no")
            if no is None:
                continue
            out[str(no).strip()] = node.get("title")
            walk(node.get("children"))

    walk((tree or {}).get("clause_tree"))
    return out


# --------------------------------------------------------------------------
# 供 #12 判定引擎调用的 API
# --------------------------------------------------------------------------
def _latest_check_row(
    conn: Optional[sqlite3.Connection], source_id: str
) -> Optional[sqlite3.Row]:
    if conn is None:
        return None
    return conn.execute(
        "SELECT c.* FROM checks c JOIN documents d ON d.id = c.document_id "
        "WHERE d.source_id = ? ORDER BY c.id DESC LIMIT 1",
        (str(source_id),),
    ).fetchone()


def _latest_version_row(
    conn: Optional[sqlite3.Connection], document_id: int
) -> Optional[sqlite3.Row]:
    if conn is None:
        return None
    return conn.execute(
        "SELECT * FROM document_versions WHERE document_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (document_id,),
    ).fetchone()


def _document_row(
    conn: Optional[sqlite3.Connection], source_id: str
) -> Optional[sqlite3.Row]:
    if conn is None:
        return None
    return conn.execute(
        "SELECT * FROM documents WHERE source_id = ?", (str(source_id),)
    ).fetchone()


def resolve_clause_basis(
    conn: Optional[sqlite3.Connection],
    profile_id: str,
    clause_no: str,
    registry_path=None,
    library_root=None,
) -> Dict[str, Any]:
    """解析「画像 + 条款号」对应的依据快照（供 #12 判定引擎调用）。

    返回字典必含 §7.1 的快照字段：
      source_ref / source_id / version / locator / snapshot_sha256 / retrieved_at / status
    解析不到时返回带 error 字段的字典（error 为错误码，message 为可读说明），
    绝不返回悬空结果（既不返回 None，也不返回缺字段的半成品）。
    """
    out: Dict[str, Any] = {
        "error": None,
        "profile_id": profile_id,
        "clause_no": str(clause_no),
    }

    # 1) 画像
    try:
        profile = find_profile(profile_id, library_root)
    except BasisError as exc:
        out.update(
            {
                "error": "PROFILE_NOT_FOUND",
                "message": str(exc),
                "status": STATUS_UNVERIFIABLE,
                "insufficient_reason": "PROFILE_NOT_FOUND",
            }
        )
        return out

    out["standard"] = (profile.get("standard") or {}).get("designation")
    out["clause_tree_ref"] = profile.get("clause_tree_ref")

    # 2) 条款树 —— 条款号必须能在条款树中找到
    try:
        tree = load_clause_tree(profile, library_root)
    except BasisError as exc:
        out.update(
            {
                "error": "CLAUSE_TREE_UNAVAILABLE",
                "message": str(exc),
                "status": STATUS_UNVERIFIABLE,
                "insufficient_reason": "CLAUSE_TREE_UNAVAILABLE",
            }
        )
        return out
    clauses = collect_clause_numbers(tree)
    cno = str(clause_no).strip()
    if cno not in clauses:
        out.update(
            {
                "error": "CLAUSE_NOT_IN_TREE",
                "message": (
                    f"条款号 {cno!r} 不在画像 {profile_id!r} 的条款树中"
                    f"（{profile.get('clause_tree_ref')}，树内共 {len(clauses)} 个条款单元）；"
                    f"按 verdict-anchor-rules §7.4 落 insufficient（CLAUSE_NOT_IN_TREE），"
                    f"不得凭模型知识补条款"
                ),
                "clause_count": len(clauses),
                "status": STATUS_UNVERIFIABLE,
                "insufficient_reason": "CLAUSE_NOT_IN_TREE",
            }
        )
        return out
    out["clause_title"] = clauses.get(cno)

    # 3) 台账中的依据记录
    designation = str((profile.get("standard") or {}).get("designation") or "")
    source_id = designation_to_source_id(designation)
    out["source_id"] = source_id
    entry = find_snapshot(source_id, registry_path)
    if entry is None:
        out.update(
            {
                "error": "BASIS_NOT_REGISTERED",
                "message": (
                    f"快照台账未登记 {source_id!r}（画像 {profile_id!r} 的标准 {designation!r}）；"
                    f"按 verdict-anchor-rules §7 落 insufficient"
                ),
                "source_ref": None,
                "status": STATUS_UNVERIFIABLE,
                "insufficient_reason": "BASIS_UNVERIFIABLE",
            }
        )
        return out

    # 4) 组装快照字段
    doc = _document_row(conn, source_id)
    db_status = None
    chk = _latest_check_row(conn, source_id)
    if chk is not None:
        db_status = chk["result"]
    version_row = _latest_version_row(conn, doc["id"]) if doc is not None else None
    db_verification = version_row["verification_status"] if version_row is not None else None
    status = derive_snapshot_status(entry, db_status, db_verification)

    out.update(
        {
            "source_ref": f"{source_id}#{cno}",
            "version": entry.get("version"),
            "locator": cno,
            "snapshot_sha256": entry.get("snapshot_sha256"),
            "retrieved_at": entry.get("retrieved_at"),
            "status": status,
            "verification_status": entry.get("verification_status"),
            "status_evidence": entry.get("status_evidence"),
            "applicable_reason": entry.get("applicable_reason"),
            "publisher": entry.get("publisher"),
            "number": entry.get("number"),
            "title": entry.get("title"),
            "source_url": entry.get("source_url"),
            "local_file": entry.get("local_file"),
            "db_document_id": doc["id"] if doc is not None else None,
            "db_present": doc is not None,
            "db_version_sha256": version_row["sha256"] if version_row else None,
            "db_last_check": db_status,
        }
    )

    # §7.2 / §7.3 联动：不可用时明确给出只能落 insufficient 的原因码
    if status == STATUS_UNVERIFIABLE:
        out["insufficient_reason"] = "BASIS_UNVERIFIABLE"
        out["note"] = (
            "依据无法核验（未人工复核 / 快照哈希缺失 / 来源不可达）："
            "该条款只能落 insufficient，不得凭标准文本或模型记忆补证"
        )
    elif status == STATUS_SUPERSEDED:
        out["insufficient_reason"] = "BASIS_SUPERSEDED"
        out["note"] = (
            "依据快照已被更新版本取代：不得用它下结论，须换用新版本快照，否则落 insufficient"
        )
    else:
        out["insufficient_reason"] = None
        out["note"] = "依据快照可核验且适用"

    # 5) ISO 对应版本的侧写（画像标准多为等同采用的国标，ISO 侧可能已换版）
    iso_designation = str((profile.get("standard") or {}).get("iso_equivalent") or "")
    iso_source_id = designation_to_source_id(iso_designation)
    if iso_source_id and iso_source_id != source_id:
        iso_entry = find_snapshot(iso_source_id, registry_path)
        if iso_entry is not None:
            iso_chk = _latest_check_row(conn, iso_source_id)
            iso_status = derive_snapshot_status(
                iso_entry, iso_chk["result"] if iso_chk is not None else None
            )
            out["iso_equivalent"] = {
                "source_ref": f"{iso_source_id}#{cno}",
                "source_id": iso_source_id,
                "version": iso_entry.get("version"),
                "locator": cno,
                "snapshot_sha256": iso_entry.get("snapshot_sha256"),
                "retrieved_at": iso_entry.get("retrieved_at"),
                "status": iso_status,
                "status_evidence": iso_entry.get("status_evidence"),
                "verification_status": iso_entry.get("verification_status"),
            }
        else:
            out["iso_equivalent"] = {
                "source_id": iso_source_id,
                "status": STATUS_UNVERIFIABLE,
                "message": "ISO 侧依据未登记进快照台账",
            }
    else:
        out["iso_equivalent"] = None

    return out


def is_basis_current(
    conn: Optional[sqlite3.Connection], source_ref: str, registry_path=None
) -> str:
    """判断依据快照状态：current / superseded / unverifiable。

    - 未经有权人员复核（verification_status != human_verified）时返回 unverifiable，
      绝不返回 current（§7.2 + 人工复核闸口）。
    - source_ref 可带条款定位（'ISO 9001:2015#4.1'），按 source_id 处理。
    """
    sid = str(source_ref or "").split("#", 1)[0].strip()
    if not sid:
        return STATUS_UNVERIFIABLE
    entry = find_snapshot(sid, registry_path)
    chk = _latest_check_row(conn, sid)
    db_status = chk["result"] if chk is not None else None
    if entry is None:
        if db_status in (STATUS_SUPERSEDED, STATUS_UNVERIFIABLE):
            return db_status
        return STATUS_UNVERIFIABLE
    doc = _document_row(conn, sid)
    ver_row = _latest_version_row(conn, doc["id"]) if doc is not None else None
    db_verification = ver_row["verification_status"] if ver_row is not None else None
    return derive_snapshot_status(entry, db_status, db_verification)


# --------------------------------------------------------------------------
# 来源取件
# --------------------------------------------------------------------------
class FetchedFile:
    """一次取件的结果。"""

    __slots__ = ("data", "filename", "url", "origin")

    def __init__(self, data: bytes, filename: str, url: str, origin: str):
        self.data = data
        self.filename = filename
        self.url = url
        self.origin = origin  # local_file | source_url(local) | source_url(http)


def _looks_like_http(url: str) -> bool:
    return url.lower().startswith(("http://", "https://"))


def _local_candidate(entry: Dict[str, Any], repo_root: Path) -> Optional[Path]:
    """台账里的 local_file / 本地 source_url → 存在的本地文件。"""
    for key in ("local_file", "source_url"):
        raw = str(entry.get(key) or "").strip()
        if not raw or _looks_like_http(raw):
            continue
        p = Path(raw)
        if not p.is_absolute():
            p = repo_root / p
        if p.exists() and p.is_file():
            return p
    return None


def _http_fetch(url: str, timeout: float = 20.0) -> FetchedFile:
    """抓取 http(s) 来源件（只取名称标题，不复制标准正文进判定）。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": "basis-db/1.0 (offline-verification)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        data = resp.read()
        final_url = resp.geturl()
        disp = resp.headers.get("Content-Disposition") or ""
    filename = ""
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disp, re.IGNORECASE)
    if m:
        filename = urllib.parse.unquote(m.group(1)).strip()
    if not filename:
        filename = os.path.basename(urllib.parse.urlparse(final_url).path) or "source.bin"
    return FetchedFile(data, filename, final_url, "source_url(http)")


def resolve_download_source(
    entry: Dict[str, Any], repo_root: Optional[Path] = None
) -> Optional[FetchedFile]:
    """按台账记录定位来源件：取不到返回 None（调用方落 unverifiable）。

    顺序：local_file → 本地 source_url → http(s) source_url。
    """
    root = Path(repo_root) if repo_root else REPO_ROOT
    local = _local_candidate(entry, root)
    if local is not None:
        return FetchedFile(local.read_bytes(), local.name, str(local), "local_file")
    url = str(entry.get("source_url") or "").strip()
    if url and _looks_like_http(url):
        try:
            return _http_fetch(url)
        except (urllib.error.URLError, OSError, ValueError):
            return None
    return None


def _offered_identity(entry: Dict[str, Any]) -> Tuple[str, str, str]:
    """来源当前提供的「名称 / 版本 / 标识」（台账视角）。"""
    return (
        str(entry.get("title") or ""),
        str(entry.get("version") or ""),
        str(entry.get("source_id") or ""),
    )


def compare_stored_vs_source(
    stored_version_row: Optional[sqlite3.Row],
    entry: Dict[str, Any],
    live_offered: Optional[Tuple[str, str, str]] = None,
) -> Tuple[str, str]:
    """比对库内快照与来源当前提供的名称标题，返回 (result, detail)。

    result ∈ current / superseded / unverifiable。
    """
    offered_title, offered_version, _offered_id = (
        live_offered or _offered_identity(entry)
    )

    evidence = str(entry.get("status_evidence") or "")
    if any(marker in evidence for marker in WITHDRAWN_MARKERS):
        return (
            STATUS_SUPERSEDED,
            f"来源记录声明该版本已撤回/被取代：{evidence}",
        )

    if stored_version_row is None:
        return (
            STATUS_UNVERIFIABLE,
            "库内没有该依据的版本快照，无法比对名称标题",
        )

    stored_title = str(stored_version_row["title"] or "")
    stored_filename = str(stored_version_row["filename"] or "")
    try:
        stored_doc_title = str(stored_version_row["document_title"] or "")
    except (IndexError, KeyError):
        stored_doc_title = ""

    # 版本标识比对：库内快照的版本标识 vs 来源当前提供的版本
    stored_tokens = _year_tokens(stored_title) or _year_tokens(stored_filename)
    offered_tokens = _year_tokens(offered_version)
    if stored_tokens and offered_tokens and stored_tokens != offered_tokens:
        return (
            STATUS_SUPERSEDED,
            f"库内快照版本标识「{stored_title or stored_filename}」"
            f"（{'/'.join(stored_tokens)}）与来源当前提供的版本"
            f"「{offered_version}」（{'/'.join(offered_tokens)}）不一致",
        )

    # 名称标题比对
    if stored_doc_title and offered_title:
        if _normalize(stored_doc_title) != _normalize(offered_title):
            return (
                STATUS_SUPERSEDED,
                f"库内快照标题「{stored_doc_title}」与来源当前提供的标题"
                f"「{offered_title}」不一致",
            )

    return (
        STATUS_CURRENT,
        "库内快照的名称标题与来源当前提供的一致"
        f"（版本标识 {'/'.join(stored_tokens) or '?'}，"
        f"标题「{stored_doc_title or stored_title}」）",
    )


def _live_offer(entry: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
    """实时访问来源页，取其当前提供的名称标题（失败返回 None）。"""
    url = str(entry.get("source_url") or "").strip()
    if not url or not _looks_like_http(url):
        return None
    try:
        fetched = _http_fetch(url)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    text = fetched.data.decode("utf-8", errors="replace")
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
    designation = str(entry.get("number") or "").strip()
    version = str(entry.get("version") or "")
    if not title and designation:
        title = designation
    return (title, version, str(entry.get("source_id") or ""))


# --------------------------------------------------------------------------
# 列表
# --------------------------------------------------------------------------
def list_documents(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """列出库内文档及其版本与最近一次核验结果。"""
    rows = conn.execute("SELECT * FROM documents ORDER BY id").fetchall()
    out: List[Dict[str, Any]] = []
    for doc in rows:
        versions = conn.execute(
            "SELECT id, title, filename, size, sha256, downloaded_at, "
            "verification_status, verified_by FROM document_versions "
            "WHERE document_id = ? ORDER BY id",
            (doc["id"],),
        ).fetchall()
        last = conn.execute(
            "SELECT * FROM checks WHERE document_id = ? ORDER BY id DESC LIMIT 1",
            (doc["id"],),
        ).fetchone()
        out.append(
            {
                "document": doc,
                "versions": [dict(v) for v in versions],
                "last_check": dict(last) if last is not None else None,
            }
        )
    return out


def _display_width(s: str) -> int:
    """中文按两列宽计算。"""
    w = 0
    for ch in s:
        w += 2 if ord(ch) > 0x2E80 else 1
    return w


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _display_width(s))


def _render_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    widths = [len(str(h)) for h in headers]
    norm_rows = []
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            s = "" if cell is None else str(cell)
            if len(s) > 46:
                s = s[:43] + "..."
            cells.append(s)
            widths[i] = max(widths[i], _display_width(s))
        norm_rows.append(cells)
    lines = []
    lines.append("  ".join(_pad(str(h), widths[i]) for i, h in enumerate(headers)))
    lines.append("  ".join("-" * widths[i] for i in range(len(headers))))
    for cells in norm_rows:
        lines.append("  ".join(_pad(cells[i], widths[i]) for i in range(len(headers))))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------
def cmd_download(args: argparse.Namespace) -> int:
    source_id = str(args.source_id).split("#", 1)[0].strip()
    try:
        entry = find_snapshot(source_id, args.registry)
    except BasisError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return EXIT_USAGE
    if entry is None:
        print(
            f"[错误] 快照台账中未登记 source_id={source_id!r}，无法取得来源地址；"
            f"请先在 {args.registry} 中登记该依据",
            file=sys.stderr,
        )
        return EXIT_USAGE

    repo_root = Path(args.repo_root) if args.repo_root else REPO_ROOT
    conn = init_db(args.db)
    try:
        document_id = register_document(
            conn,
            source_id,
            entry.get("publisher"),
            entry.get("title"),
            entry.get("number"),
            _guess_category(source_id),
            entry.get("source_url"),
            entry.get("local_file") or entry.get("source_url"),
        )

        fetched = resolve_download_source(entry, repo_root)
        if fetched is None:
            detail = (
                f"来源不可达：local_file={entry.get('local_file')!r} "
                f"source_url={entry.get('source_url')!r}"
            )
            record_check(
                conn,
                document_id,
                STATUS_UNVERIFIABLE,
                entry.get("title"),
                str(entry.get("title") or ""),
                detail,
                "人工核对来源页面并补齐可取回的来源件后重跑 download；"
                "在此之前该依据只能落 insufficient",
            )
            print(f"[无法判定] {source_id}：{detail}", file=sys.stderr)
            print(
                f"已写入 checks 记录：document_id={document_id} "
                f"result={STATUS_UNVERIFIABLE}（退出码 {EXIT_UNVERIFIABLE}）",
                file=sys.stderr,
            )
            return EXIT_UNVERIFIABLE

        note = (
            f"取件来源 {fetched.origin}；来源记录 retrieved_at="
            f"{entry.get('retrieved_at')}；"
            f"未经人工复核（verification_status={VERIFICATION_CANDIDATE}）"
        )
        version_id = add_version(
            conn,
            document_id,
            source_id,
            fetched.filename,
            fetched.url,
            fetched.data,
            note,
        )
        status = derive_snapshot_status(entry)
        record_check(
            conn,
            document_id,
            status,
            entry.get("title"),
            str(entry.get("title") or ""),
            f"已抓取来源件入库：{fetched.filename}（{len(fetched.data)} 字节，"
            f"sha256={sha256_bytes(fetched.data)[:16]}…）",
            "人工复核：candidate -> pending_review -> human_verified，"
            "复核通过前该依据不得用于下结论",
        )
        print(
            f"[已入库] {source_id}\n"
            f"  document_id = {document_id}\n"
            f"  version_id  = {version_id}\n"
            f"  文件名      = {fetched.filename}\n"
            f"  字节数      = {len(fetched.data)}\n"
            f"  sha256      = {sha256_bytes(fetched.data)}\n"
            f"  取件来源    = {fetched.origin}\n"
            f"  核验状态    = {status}"
            f"（verification_status={VERIFICATION_CANDIDATE}，待人工复核）"
        )
        return EXIT_OK
    finally:
        conn.close()


def cmd_check(args: argparse.Namespace) -> int:
    source_id = str(args.source_id).split("#", 1)[0].strip()
    db_path = Path(args.db)
    if not db_path.exists():
        print(
            f"[错误] 依据库不存在：{db_path}；"
            f"请先执行 python tools/basis_db.py download <source_id>",
            file=sys.stderr,
        )
        return EXIT_USAGE
    try:
        conn = open_db(db_path)
    except BasisError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return EXIT_USAGE

    try:
        doc = conn.execute(
            "SELECT * FROM documents WHERE source_id = ?", (source_id,)
        ).fetchone()
        if doc is None:
            print(
                f"[错误] 依据库中没有 source_id={source_id!r}；"
                f"请先执行 download 入库后再核验",
                file=sys.stderr,
            )
            return EXIT_USAGE

        entry = find_snapshot(source_id, args.registry)
        if entry is None:
            record_check(
                conn,
                doc["id"],
                STATUS_UNVERIFIABLE,
                doc["title"],
                "",
                f"快照台账中没有 {source_id!r} 的来源记录，无法取得来源当前提供的名称标题",
                "补齐快照台账记录后重跑 check；在此之前该依据只能落 insufficient",
            )
            print(
                f"[无法判定] {source_id}：快照台账中无该依据的来源记录",
                file=sys.stderr,
            )
            return EXIT_UNVERIFIABLE

        stored = conn.execute(
            "SELECT v.*, d.title AS document_title FROM document_versions v "
            "JOIN documents d ON d.id = v.document_id "
            "WHERE v.document_id = ? ORDER BY v.id DESC LIMIT 1",
            (doc["id"],),
        ).fetchone()

        live = None
        if getattr(args, "live", False):
            live = _live_offer(entry)
            if live is None:
                record_check(
                    conn,
                    doc["id"],
                    STATUS_UNVERIFIABLE,
                    doc["title"],
                    str(entry.get("title") or ""),
                    f"--live 模式无法访问来源：{entry.get('source_url')}",
                    "恢复网络或改用离线台账记录后重跑 check；"
                    "在此之前该依据只能落 insufficient",
                )
                print(
                    f"[无法判定] {source_id}：--live 模式无法访问来源页面",
                    file=sys.stderr,
                )
                return EXIT_UNVERIFIABLE

        result, detail = compare_stored_vs_source(stored, entry, live)
        offered = live or _offered_identity(entry)
        ver_row = (
            conn.execute(
                "SELECT verification_status FROM document_versions "
                "WHERE document_id = ? ORDER BY id DESC LIMIT 1",
                (doc["id"],),
            ).fetchone()
        )
        db_verification = ver_row["verification_status"] if ver_row is not None else None
        effective = derive_snapshot_status(entry, None, db_verification)
        record_check(
            conn,
            doc["id"],
            result,
            (stored["title"] if stored is not None else doc["title"]),
            f"{offered[0]} | 版本标识 {offered[1]} | {offered[2]}",
            detail,
            {
                STATUS_CURRENT: "人工复核快照：candidate -> pending_review -> human_verified",
                STATUS_SUPERSEDED: "改用新版依据快照（更新 source_ref 与 snapshot_sha256），"
                "否则该条款落 insufficient",
                STATUS_UNVERIFIABLE: "补齐来源记录/文件后重跑 check；"
                "在此之前该条款只能落 insufficient",
            }[result],
        )
        print(
            f"[核验] {source_id}\n"
            f"  库内快照   = {stored['title'] if stored is not None else '（无）'}\n"
            f"  来源提供   = {offered[0]} | 版本标识 {offered[1]} | {offered[2]}\n"
            f"  判定       = {result}\n"
            f"  说明       = {detail}\n"
            f"  生效状态   = {effective}"
            f"（人工复核状态 {db_verification or '（库内无版本快照）'}）\n"
            f"  模式       = {'--live（实时访问来源）' if live else '离线台账记录'}"
        )
        if effective == STATUS_UNVERIFIABLE and result == STATUS_CURRENT:
            print(
                "  提示       = 版本名称比对通过，但人工复核未完成："
                "该依据对判定引擎仍为 unverifiable，只能落证据不足"
            )
        if result == STATUS_UNVERIFIABLE:
            print(
                f"[无法判定] {source_id}：{detail}",
                file=sys.stderr,
            )
        return {
            STATUS_CURRENT: EXIT_OK,
            STATUS_SUPERSEDED: EXIT_SUPERSEDED,
            STATUS_UNVERIFIABLE: EXIT_UNVERIFIABLE,
        }[result]
    finally:
        conn.close()


def cmd_list(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    if not db_path.exists():
        print(
            f"[错误] 依据库不存在：{db_path}；"
            f"请先执行 python tools/basis_db.py download <source_id>",
            file=sys.stderr,
        )
        return EXIT_USAGE
    try:
        conn = open_db(db_path)
    except BasisError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        data = list_documents(conn)
        if not data:
            print(
                f"[错误] 依据库为空：{db_path} 尚无任何文档；"
                f"请先执行 python tools/basis_db.py download <source_id>",
                file=sys.stderr,
            )
            return EXIT_USAGE

        print(f"依据库：{db_path}")
        print(f"文档数：{len(data)}")
        print()
        headers = [
            "SOURCE_ID",
            "NUMBER",
            "TITLE",
            "VERSION",
            "FILENAME",
            "SHA256",
            "VERIFICATION",
            "LAST_CHECK",
            "CHECKED_AT",
        ]
        rows: List[List[Any]] = []
        for item in data:
            doc = item["document"]
            last = item["last_check"]
            if item["versions"]:
                for v in item["versions"]:
                    rows.append(
                        [
                            doc["source_id"],
                            doc["number"],
                            doc["title"],
                            v["title"],
                            v["filename"],
                            (v["sha256"] or "")[:16] + ("…" if v["sha256"] else ""),
                            v["verification_status"],
                            (last["result"] if last else "") or "",
                            (last["checked_at"] if last else "") or "",
                        ]
                    )
            else:
                rows.append(
                    [
                        doc["source_id"],
                        doc["number"],
                        doc["title"],
                        "（无版本快照）",
                        "",
                        "",
                        "",
                        "",
                        "",
                    ]
                )
        print(_render_table(headers, rows))
        print()
        print("说明：VERIFICATION 为人工复核状态（candidate / pending_review / human_verified）；")
        print("      LAST_CHECK 为最近一次核验结果（current / superseded / unverifiable）。")
        return EXIT_OK
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------
def cmd_review(args: argparse.Namespace) -> int:
    """人工复核：推进某个版本快照的核验状态，并在 note 里留痕。"""
    source_id = str(args.source_id).split("#", 1)[0].strip()
    db_path = Path(args.db)
    if not db_path.exists():
        print(
            f"[错误] 依据库不存在：{db_path}；"
            f"请先执行 python tools/basis_db.py download <source_id>",
            file=sys.stderr,
        )
        return EXIT_USAGE
    try:
        conn = open_db(db_path)
    except BasisError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        doc = conn.execute(
            "SELECT * FROM documents WHERE source_id = ?", (source_id,)
        ).fetchone()
        if doc is None:
            print(
                f"[错误] 依据库中没有 source_id={source_id!r}；"
                f"请先执行 download 入库后再复核",
                file=sys.stderr,
            )
            return EXIT_USAGE
        ver = conn.execute(
            "SELECT id, verification_status, verified_by, verified_at, sha256, "
            "filename FROM document_versions WHERE document_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (doc["id"],),
        ).fetchone()
        if ver is None:
            print(
                f"[错误] {source_id} 还没有版本快照，无法复核；请先执行 download",
                file=sys.stderr,
            )
            return EXIT_USAGE
        set_version_verification(
            conn, ver["id"], args.status, args.reviewer, args.note
        )
        row = conn.execute(
            "SELECT verification_status, verified_by, verified_at FROM "
            "document_versions WHERE id = ?",
            (ver["id"],),
        ).fetchone()
        print(
            f"[复核] {source_id}\n"
            f"  version_id  = {ver['id']}\n"
            f"  文件名      = {ver['filename']}\n"
            f"  sha256      = {ver['sha256']}\n"
            f"  原状态      = {ver['verification_status']}\n"
            f"  新状态      = {row['verification_status']}\n"
            f"  复核人      = {row['verified_by']}\n"
            f"  复核时间    = {row['verified_at']}\n"
            f"  留痕        = document_versions.note 追加不可变审计行"
        )
        return EXIT_OK
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="basis_db.py",
        description="依据库与依据版本核验（#20）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "退出码：0 成功且判定明确 / 2 判定为非最新版或已更新 / "
            "3 无法判定 / 4 参数或库状态错误"
        ),
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"依据库 SQLite 路径（默认 {DEFAULT_DB_PATH}）",
    )
    parser.add_argument(
        "--registry",
        default=str(DEFAULT_REGISTRY_PATH),
        help=f"快照台账 registry.json 路径（默认 {DEFAULT_REGISTRY_PATH}）",
    )
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="仓库根目录（用于解析台账里的相对 local_file 路径）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_dl = sub.add_parser("download", help="从来源取件入库（库内没有该 source_id 时）")
    p_dl.add_argument("source_id", help="依据的 source_id，如 ISO 9001:2015")
    p_dl.set_defaults(func=cmd_download)

    p_ck = sub.add_parser("check", help="去来源看当前提供的名称标题并比对")
    p_ck.add_argument("source_id", help="依据的 source_id，如 ISO 9001:2015")
    p_ck.add_argument(
        "--live",
        action="store_true",
        help="实时访问来源页面复核（需联网；默认用离线台账记录）",
    )
    p_ck.set_defaults(func=cmd_check)

    p_ls = sub.add_parser("list", help="列出库内文档及其版本与最近一次核验结果")
    p_ls.set_defaults(func=cmd_list)

    p_rv = sub.add_parser(
        "review",
        help="人工复核：推进某个版本快照的核验状态并留痕",
    )
    p_rv.add_argument("source_id", help="依据的 source_id，如 ISO 9001:2015")
    p_rv.add_argument(
        "--status",
        required=True,
        choices=list(VERIFICATION_STATES),
        help="新的核验状态：candidate / pending_review / human_verified",
    )
    p_rv.add_argument(
        "--reviewer", required=True, help="复核人（留痕必填，不得留空）"
    )
    p_rv.add_argument("--note", default=None, help="复核备注（写入留痕行）")
    p_rv.set_defaults(func=cmd_review)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    # 统一 UTF-8 输出，避免 Windows GBK 控制台编码异常
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - 老解释器兜底
            pass
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except BasisError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
