#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""规则知识库参考实现（#54）：校验、引用、版本差异、提醒、可追溯问答。

只依赖 Python 标准库。运行：
  python -X utf8 reference_rulekb.py --verify
产物 verification.txt 由 --verify 写出。
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent
SAMPLES = BASE / "samples.json"
VERIFY = BASE / "verification.txt"

ZERO_WIDTH = "\u200b\u200c\u200d\ufeff"
CITE_RE = re.compile(
    r"^〔(?P<title>.+?)〕(?P<rest>.+?)（(?P<version>.+?)，(?P<date>\d{4}-\d{2}-\d{2})）$"
)

IMPACT_MAP = {
    "SETTLEMENT_PERIOD": ["SETTLEMENT"],
    "SETTLEMENT_ENERGY": ["SETTLEMENT"],
    "SETTLEMENT_PRICE": ["SETTLEMENT", "TRADE"],
    "RETAIL_MARGIN": ["RETAIL", "SETTLEMENT"],
    "DEVIATION_ASSESSMENT": ["RETAIL", "SETTLEMENT"],
    "RETRO_ADJUSTMENT": ["SETTLEMENT"],
    "OPERATION_COMPENSATION": ["SETTLEMENT"],
    "DEVIATION_REFUND": ["SETTLEMENT"],
    "PEAK_PERIOD": ["TRADE", "SETTLEMENT"],
    "PEAK_UPLIFT": ["TRADE", "SETTLEMENT"],
    "PRICE_CAP_FLOOR": ["TRADE", "SETTLEMENT"],
    "NEGATIVE_PRICE": ["TRADE", "SETTLEMENT"],
    "GREEN_ENV_VALUE": ["TRADE", "RETAIL"],
    "PERFORMANCE_GUARANTEE": ["CREDIT"],
    "INFORMATION_DISCLOSURE": ["DISCLOSURE"],
    "TRADING_CALENDAR": ["TRADE"],
    "VALIDITY": ["DISCLOSURE"],
    "REGISTRATION": ["CREDIT"],
    "BACKSTOP_RETAIL": ["RETAIL"],
    "OTHER": [],
}

SEVERITY = {
    "NEW_ACTIVE_DOC": "INFO",
    "STATUS_CHANGED": "WARN",
    "DIFF_IMPACT": "WARN",
    "EXPIRING_SOON": {"T30": "WARN", "T7": "CRITICAL", "T1": "CRITICAL"},
    "EFFECTIVE_SOON": {"T30": "INFO", "T7": "WARN", "T1": "WARN"},
}


# ---------- 基础工具 ----------

def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    for ch in ZERO_WIDTH:
        text = text.replace(ch, "")
    return re.sub(r"\s+", " ", text).strip()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def as_date(value) -> date | None:
    if value in (None, ""):
        return None
    return date.fromisoformat(str(value)[:10])


def days_between(a: date, b: date) -> int:
    return (b - a).days


def load_library(path: Path = SAMPLES) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    docs = {d["doc_id"]: d for d in raw["docs"]}
    clauses = raw["clauses"]
    by_uid = {c["clause_uid"]: c for c in clauses}
    by_doc: dict[str, list] = {}
    for c in clauses:
        by_doc.setdefault(c["doc_id"], []).append(c)
    for rows in by_doc.values():
        rows.sort(key=lambda c: c["clause_seq"])
    successor = {}
    for d in raw["docs"]:
        old = d.get("supersedes_doc_id")
        if old:
            successor[old] = d["doc_id"]
    equiv = {}
    for e in raw.get("clause_equivs", []):
        equiv[e["old_clause_uid"]] = e
    return {
        "docs": docs,
        "doc_list": raw["docs"],
        "clauses": clauses,
        "by_uid": by_uid,
        "by_doc": by_doc,
        "successor": successor,
        "equivs": equiv,
        "meta": raw.get("meta", {}),
    }


# ---------- 状态解析 ----------

def resolve_status(lib: dict, doc: dict, as_of) -> str:
    """按 as_of 重算状态；不信任 doc['status'] 列。"""
    day = as_date(as_of)
    if day is None:
        raise ValueError("as_of required")
    successor_id = lib["successor"].get(doc["doc_id"])
    if successor_id:
        succ = lib["docs"][successor_id]
        succ_from = as_date(succ.get("effective_from"))
        if succ_from and succ_from <= day:
            return "SUPERSEDED"
    start = as_date(doc.get("effective_from"))
    end = as_date(doc.get("effective_to"))
    if start is None:
        return "DRAFT"
    if day < start:
        return "DRAFT"
    if end is not None and day > end:
        return "EXPIRED"
    return "ACTIVE"


def cited_status(lib: dict, doc: dict, as_of) -> str:
    return resolve_status(lib, doc, as_of)


# ---------- 校验 ----------

def validate_doc(lib: dict, doc: dict) -> list[str]:
    errs: list[str] = []
    required = [
        "doc_id", "title", "issuer", "issuer_code", "doc_type", "version_label",
        "publish_date", "status", "status_evidence", "evidence_status", "url",
        "source_kind", "accessed_date", "file_hash", "clause_count",
    ]
    for key in required:
        if doc.get(key) in (None, ""):
            errs.append(f"{doc.get('doc_id')}: 缺字段 {key}")
    if not re.match(r"^RULE-\d{4}-\d{4}-\d{3}$", str(doc.get("doc_id", ""))):
        errs.append(f"{doc.get('doc_id')}: doc_id 格式非法")
    if not re.match(r"^\d{4}$", str(doc.get("issuer_code", ""))):
        errs.append(f"{doc.get('doc_id')}: issuer_code 必须是 4 位")
    if not re.match(r"^[0-9a-f]{64}$", str(doc.get("file_hash", ""))):
        errs.append(f"{doc.get('doc_id')}: file_hash 不是 64 位小写 hex")
    if doc.get("doc_no") in (None, ""):
        if doc.get("evidence_status") != "PARTIAL":
            errs.append(f"{doc.get('doc_id')}: 缺文号时 evidence_status 必须为 PARTIAL")
        if not doc.get("notes"):
            errs.append(f"{doc.get('doc_id')}: 缺文号时 notes 必须写明缺口")
    if doc.get("status") in ("ACTIVE", "SUPERSEDED", "EXPIRED"):
        if not doc.get("url") or not doc.get("accessed_date"):
            errs.append(f"{doc.get('doc_id')}: 生效文件必须有 url 与 accessed_date")
        if doc.get("evidence_status") == "UNVERIFIED":
            errs.append(f"{doc.get('doc_id')}: 已生效文件不得 UNVERIFIED")
    if doc.get("status") == "DRAFT" and doc.get("effective_from"):
        errs.append(f"{doc.get('doc_id')}: DRAFT 不得有 effective_from")
    start, end = as_date(doc.get("effective_from")), as_date(doc.get("effective_to"))
    if start and end and end < start:
        errs.append(f"{doc.get('doc_id')}: effective_to 早于 effective_from")
    if doc.get("source_kind") == "MIRROR" and not doc.get("mirror_note"):
        errs.append(f"{doc.get('doc_id')}: MIRROR 必须写 mirror_note")
    old = doc.get("supersedes_doc_id")
    if old and old not in lib["docs"]:
        errs.append(f"{doc.get('doc_id')}: supersedes 指向不存在的 {old}")
    for rid in doc.get("repeals", []):
        if rid not in lib["docs"]:
            errs.append(f"{doc.get('doc_id')}: repeals 指向不存在的 {rid}")
        if rid == doc.get("doc_id"):
            errs.append(f"{doc.get('doc_id')}: 不得自我废止")
    actual = len(lib["by_doc"].get(doc["doc_id"], []))
    if doc.get("clause_count") != actual:
        errs.append(f"{doc.get('doc_id')}: clause_count={doc.get('clause_count')} 实际={actual}")
    return errs


def validate_clause(clause: dict) -> list[str]:
    errs: list[str] = []
    for key in ("clause_uid", "doc_id", "clause_no", "clause_key", "clause_seq",
                "clause_topic", "text_raw", "text_norm", "text_hash", "char_count", "text_status"):
        if key not in clause:
            errs.append(f"{clause.get('clause_uid')}: 缺字段 {key}")
    if not str(clause.get("clause_uid", "")).startswith(str(clause.get("doc_id")) + "#"):
        errs.append(f"{clause.get('clause_uid')}: uid 前缀必须是 doc_id#")
    expected = sha256_text(clause.get("text_norm", ""))
    if clause.get("text_hash") != expected:
        errs.append(f"{clause.get('clause_uid')}: text_hash 与 text_norm 不符")
    if normalize_text(clause.get("text_raw", "")) != clause.get("text_norm"):
        errs.append(f"{clause.get('clause_uid')}: text_norm 不是 text_raw 的规范化结果")
    if clause.get("char_count") != len(clause.get("text_raw", "")):
        errs.append(f"{clause.get('clause_uid')}: char_count 不符")
    if clause.get("text_status") == "VERBATIM":
        if not clause.get("text_raw"):
            errs.append(f"{clause.get('clause_uid')}: VERBATIM 必须有原文")
        if not clause.get("page_ref"):
            errs.append(f"{clause.get('clause_uid')}: VERBATIM 必须有 page_ref")
    elif clause.get("text_status") == "PENDING_TRANSCRIPTION":
        if clause.get("text_raw") != "":
            errs.append(f"{clause.get('clause_uid')}: PENDING 时 text_raw 必须为空")
    else:
        errs.append(f"{clause.get('clause_uid')}: 未知 text_status")
    return errs


def validate_samples(lib: dict) -> list[str]:
    errs: list[str] = []
    seen_uid = set()
    for doc in lib["doc_list"]:
        errs.extend(validate_doc(lib, doc))
    for clause in lib["clauses"]:
        errs.extend(validate_clause(clause))
        uid = clause["clause_uid"]
        if uid in seen_uid:
            errs.append(f"重复 clause_uid: {uid}")
        seen_uid.add(uid)
    for doc_id, rows in lib["by_doc"].items():
        keys = [c["clause_key"] for c in rows]
        if len(keys) != len(set(keys)):
            errs.append(f"{doc_id}: clause_key 同文件内重复")
    # 版本链无环
    for doc_id in lib["docs"]:
        seen = set()
        cur = doc_id
        while cur in lib["successor"]:
            if cur in seen:
                errs.append(f"版本链成环: {doc_id}")
                break
            seen.add(cur)
            cur = lib["successor"][cur]
    # 等价关系指向存在的条款
    for old, e in lib["equivs"].items():
        if old not in lib["by_uid"] or e["new_clause_uid"] not in lib["by_uid"]:
            errs.append(f"clause_equiv 指向不存在的条款: {old}")
    return errs


def evidence_gaps(lib: dict) -> list[dict]:
    out = []
    for doc in lib["doc_list"]:
        gaps = []
        if not doc.get("doc_no"):
            gaps.append("文号未查到/待补，待客户确认（Q01）")
        if doc.get("evidence_status") == "PARTIAL":
            gaps.append("证据等级 PARTIAL：" + (doc.get("notes") or "缺页面/附件"))
        if doc.get("source_kind") == "MIRROR":
            gaps.append("仅镜像可达：" + (doc.get("mirror_note") or "未写镜像渠道"))
        pending = [c for c in lib["by_doc"].get(doc["doc_id"], [])
                   if c.get("text_status") != "VERBATIM"]
        if pending:
            gaps.append(f"{len(pending)} 条条款原文待补（PENDING_TRANSCRIPTION）")
        if gaps:
            out.append({
                "doc_id": doc["doc_id"],
                "title": doc["title"],
                "doc_no": doc.get("doc_no"),
                "gaps": gaps,
                "pending_clause_uids": [c["clause_uid"] for c in pending],
            })
    return out


# ---------- 引用 ----------

def clause_path(clause: dict) -> str:
    chapter = (clause.get("chapter_path") or "").strip()
    no = (clause.get("clause_no") or "").strip()
    return f"{chapter} {no}".strip()


def cite_clause(lib: dict, clause: dict) -> str:
    doc = lib["docs"][clause["doc_id"]]
    doc_no = doc.get("doc_no") or "文号待补"
    return "〔{}〕{} {}（{}，{}）".format(
        doc["title"], doc_no, clause_path(clause),
        doc["version_label"], doc["effective_from"],
    )


def parse_citation(lib: dict, citation: str) -> dict:
    m = CITE_RE.match(citation)
    if not m:
        raise ValueError("引用格式不合法: " + citation)
    rest = m.group("rest")
    doc_no, _, path = rest.partition(" ")
    title = m.group("title")
    doc = None
    for d in lib["doc_list"]:
        if d["title"] == title:
            doc = d
            break
    if doc is None:
        raise ValueError("找不到文件: " + title)
    clause = None
    for c in lib["by_doc"].get(doc["doc_id"], []):
        if clause_path(c) == path.strip():
            clause = c
            break
    if clause is None:
        raise ValueError("找不到条款: " + path)
    return {
        "title": title,
        "doc_no": doc_no,
        "clause_no": clause["clause_no"],
        "version_label": m.group("version"),
        "effective_from": m.group("date"),
        "clause_uid": clause["clause_uid"],
    }


def make_citation(lib: dict, clause: dict, quote: str, as_of) -> dict:
    doc = lib["docs"][clause["doc_id"]]
    return {
        "title": doc["title"],
        "doc_no": doc.get("doc_no") or "文号待补",
        "clause_no": clause["clause_no"],
        "chapter_path": clause.get("chapter_path") or "",
        "version_label": doc["version_label"],
        "effective_from": doc["effective_from"],
        "doc_id": doc["doc_id"],
        "clause_uid": clause["clause_uid"],
        "text_hash": clause["text_hash"],
        "quote": quote,
        "source_url": doc["url"],
        "accessed_date": doc["accessed_date"],
        "evidence_status": doc["evidence_status"],
        "status_at_as_of": cited_status(lib, doc, as_of),
    }


# ---------- 检索与可追溯问答 ----------

def tokens(text: str) -> set[str]:
    text = normalize_text(text).lower()
    out: set[str] = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 1:
            out.add(run)
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
    for word in re.findall(r"[a-z0-9]+", text):
        out.add(word)
    return out


def score_clause(question: str, clause: dict) -> int:
    q = tokens(question)
    body = tokens(clause.get("text_norm", ""))
    return len(q & body)


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。；])", text)
    return [p.strip() for p in parts if p.strip()]


def extract_quote(question: str, clause: dict) -> str:
    candidates = sentences(clause["text_raw"])
    if not candidates:
        return clause["text_raw"]
    q = tokens(question)
    best = max(candidates, key=lambda s: len(q & tokens(s)))
    return best


def search_clauses(lib: dict, question: str, as_of) -> list[tuple[int, dict]]:
    hits = []
    for clause in lib["clauses"]:
        if clause.get("text_status") != "VERBATIM":
            continue
        doc = lib["docs"][clause["doc_id"]]
        if cited_status(lib, doc, as_of) != "ACTIVE":
            continue
        sc = score_clause(question, clause)
        if sc > 0:
            hits.append((sc, clause))
    hits.sort(key=lambda x: (-x[0], lib["docs"][x[1]["doc_id"]]["doc_type"], x[1]["clause_uid"]))
    return hits


def answer_question(lib: dict, question: str, as_of: str) -> dict:
    hits = search_clauses(lib, question, as_of)
    gaps = evidence_gaps(lib)
    base = {
        "question": question,
        "as_of": as_of,
        "answer_status": "INSUFFICIENT_EVIDENCE",
        "answer_text": "",
        "citations": [],
        "rule_refs": [],
        "gaps": gaps,
        "refused_reason": "",
        "index_version": "idx-1.0.0",
    }
    if not hits or hits[0][0] < 2:
        base["refused_reason"] = "规则库中没有与本问题直接匹配的现行条款；不以推断补写。"
        return base
    top_score, top = hits[0]
    quote = extract_quote(question, top)
    citation = make_citation(lib, top, quote, as_of)
    base.update({
        "answer_status": "ANSWERED",
        "answer_text": quote,
        "citations": [citation],
        "rule_refs": [cite_clause(lib, top)],
        "refused_reason": "",
    })
    # 同主题还有 PENDING 条款时降为 PARTIAL，提示证据未齐
    same_topic_pending = [
        c for c in lib["clauses"]
        if c["clause_topic"] == top["clause_topic"] and c["text_status"] != "VERBATIM"
    ]
    if same_topic_pending:
        base["answer_status"] = "PARTIAL"
        base["refused_reason"] = "同主题仍有 {} 条原文待补，答案仅覆盖已入库条款。".format(len(same_topic_pending))
    return base


def validate_answer(lib: dict, ans: dict) -> list[str]:
    errs: list[str] = []
    status = ans.get("answer_status")
    if status not in ("ANSWERED", "PARTIAL", "INSUFFICIENT_EVIDENCE", "OUT_OF_SCOPE"):
        errs.append("answer_status 非法")
    if status in ("ANSWERED", "PARTIAL"):
        if not ans.get("citations"):
            errs.append("有答案但无引用")
        for c in ans.get("citations", []):
            for field in ("title", "clause_no", "version_label", "effective_from", "quote"):
                if not c.get(field):
                    errs.append("引用缺五要素之一: " + field)
            clause = lib["by_uid"].get(c.get("clause_uid"))
            if clause is None:
                errs.append("引用指向不存在的条款")
                continue
            if clause.get("text_status") != "VERBATIM":
                errs.append("引用了未校验原文的条款")
            if c["quote"] not in clause["text_raw"]:
                errs.append("quote 不是原文片段（疑似改写）")
            doc = lib["docs"][clause["doc_id"]]
            if cited_status(lib, doc, ans["as_of"]) != "ACTIVE":
                errs.append("引用了非现行版本")
    else:
        if ans.get("citations"):
            errs.append("拒答/部分回答不应带引用")
        if not ans.get("refused_reason"):
            errs.append("拒答必须写 refused_reason")
    return errs


# ---------- 版本差异 ----------

def impact_areas(clause: dict) -> list[str]:
    return list(IMPACT_MAP.get(clause.get("clause_topic"), []))


def diff_docs(lib: dict, old_doc_id: str, new_doc_id: str, created_at: str = "2026-11-05T23:00:00+08:00") -> dict:
    old_doc = lib["docs"][old_doc_id]
    new_doc = lib["docs"][new_doc_id]
    old_rows = lib["by_doc"].get(old_doc_id, [])
    new_rows = lib["by_doc"].get(new_doc_id, [])
    old_by_key = {c["clause_key"]: c for c in old_rows}
    new_by_key = {c["clause_key"]: c for c in new_rows}
    used_new: set[str] = set()
    changes: list[dict] = []
    clause_map: dict[str, str] = {}

    def add(change_type: str, reason: str, clause: dict, old_hash=None, new_hash=None):
        changes.append({
            "diff_id": hashlib.sha256(
                f"{old_doc_id}|{new_doc_id}|{clause['clause_uid']}|{change_type}".encode()
            ).hexdigest()[:16],
            "old_doc_id": old_doc_id,
            "new_doc_id": new_doc_id,
            "clause_uid": clause["clause_uid"],
            "change_type": change_type,
            "reason": reason,
            "impact_areas": impact_areas(clause),
            "old_text_hash": old_hash,
            "new_text_hash": new_hash,
            "created_at": created_at,
        })

    # 1) 同键且同文：未变化
    for key, oc in old_by_key.items():
        nc = new_by_key.get(key)
        if nc is not None and nc["text_hash"] == oc["text_hash"]:
            used_new.add(nc["clause_uid"])
            clause_map[oc["clause_uid"]] = nc["clause_uid"]

    # 2) 跨键同文：改序号 / 搬章节（人工等价优先）
    for oc in old_rows:
        if oc["clause_uid"] in clause_map:
            continue
        pair = None
        eq = lib["equivs"].get(oc["clause_uid"])
        if eq and eq["new_clause_uid"] not in used_new:
            pair = lib["by_uid"].get(eq["new_clause_uid"])
        if pair is None:
            for nc in new_rows:
                if nc["clause_uid"] in used_new:
                    continue
                if nc["text_hash"] == oc["text_hash"]:
                    pair = nc
                    break
        if pair is not None:
            used_new.add(pair["clause_uid"])
            clause_map[oc["clause_uid"]] = pair["clause_uid"]
            # 条款号变了也算变更：下游引用必须随之改，即使正文一字未动
            add("MODIFIED", "RENUMBERED", pair, oc["text_hash"], pair["text_hash"])

    # 3) 同键但文本变了：相似度足够才认为是改写，否则按废止+新增处理
    for oc in old_rows:
        if oc["clause_uid"] in clause_map:
            continue
        nc = new_by_key.get(oc["clause_key"])
        if nc is None or nc["clause_uid"] in used_new:
            continue
        ratio = difflib.SequenceMatcher(None, oc["text_norm"], nc["text_norm"]).ratio()
        if ratio >= 0.45:
            used_new.add(nc["clause_uid"])
            clause_map[oc["clause_uid"]] = nc["clause_uid"]
            add("MODIFIED", "TEXT_EDIT", nc, oc["text_hash"], nc["text_hash"])

    # 4) 剩余：旧=废止，新=新增
    for oc in old_rows:
        if oc["clause_uid"] in clause_map:
            continue
        reason = "DOC_REPEALED" if old_doc_id in (new_doc.get("repeals") or []) else "OMITTED"
        add("REPEALED", reason, oc, oc["text_hash"], None)

    for nc in new_rows:
        if nc["clause_uid"] not in used_new:
            add("ADDED", "NEW_CLAUSE", nc, None, nc["text_hash"])

    summary = {"added": 0, "modified": 0, "repealed": 0}
    for ch in changes:
        summary[ch["change_type"].lower()] += 1
    return {
        "old_doc_id": old_doc_id,
        "new_doc_id": new_doc_id,
        "changes": changes,
        "summary": summary,
        "clause_map": clause_map,
    }


# ---------- 变更提醒 ----------

def window_for(days: int) -> str | None:
    if days < 0:
        return None
    if days <= 1:
        return "T1"
    if days <= 7:
        return "T7"
    if days <= 30:
        return "T30"
    return None


def build_alerts(lib: dict, as_of: str, diff_result: dict | None = None,
                 subscriptions: list[str] | None = None,
                 existing: set[str] | None = None) -> list[dict]:
    day = as_date(as_of)
    existing = set(existing or [])
    subs = set(subscriptions or [])
    alerts: list[dict] = []

    def emit(trigger: str, doc: dict, window: str, due, payload: dict, impacts: list[str]):
        # 时间窗口类提醒的指纹不包含 days_left，否则每天都会重复提醒
        stable = payload if trigger == "DIFF_IMPACT" else {
            k: payload.get(k) for k in ("title", "doc_no", "version")
        }
        fp = hashlib.sha256(
            json.dumps([trigger, doc["doc_id"], window, stable], ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        if fp in existing:
            return
        existing.add(fp)
        sev = SEVERITY[trigger]
        if isinstance(sev, dict):
            sev = sev.get(window, "INFO")
        alerts.append({
            "alert_id": "ALT-" + fp[:16],
            "trigger": trigger,
            "severity": sev,
            "doc_id": doc["doc_id"],
            "due_date": due,
            "impact_areas": impacts,
            "fingerprint": fp,
            "payload": payload,
            "created_at": as_of + "T00:00:00+08:00",
        })

    for doc in lib["doc_list"]:
        if doc.get("is_synthetic"):
            continue
        status = resolve_status(lib, doc, as_of)
        if status == "ACTIVE" and doc.get("effective_from") == as_of:
            emit("NEW_ACTIVE_DOC", doc, "D0", as_of,
                 {"title": doc["title"], "doc_no": doc.get("doc_no"), "version": doc["version_label"]},
                 [])
        if status == "ACTIVE" and doc.get("effective_to"):
            days = days_between(day, as_date(doc["effective_to"]))
            window = window_for(days)
            if window:
                emit("EXPIRING_SOON", doc, window, doc["effective_to"],
                     {"title": doc["title"], "doc_no": doc.get("doc_no"),
                      "version": doc["version_label"], "days_left": days},
                     ["DISCLOSURE"])
        start = as_date(doc.get("effective_from"))
        if status == "DRAFT" and start and start > day:
            window = window_for(days_between(day, start))
            if window:
                emit("EFFECTIVE_SOON", doc, window, doc["effective_from"],
                     {"title": doc["title"], "doc_no": doc.get("doc_no"),
                      "version": doc["version_label"]},
                     [])

    if diff_result:
        areas = sorted({a for ch in diff_result["changes"] for a in ch["impact_areas"]})
        if areas and (not subs or set(areas) & subs):
            new_doc = lib["docs"][diff_result["new_doc_id"]]
            emit("DIFF_IMPACT", new_doc, "DIFF", as_of,
                 {"old": diff_result["old_doc_id"], "new": diff_result["new_doc_id"],
                  "summary": diff_result["summary"], "areas": areas},
                 areas)
    return alerts


# ---------- 自检 ----------

def check(results, cid, name, fn):
    try:
        ok, detail = fn()
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, "异常: {!r}".format(exc)
    results.append((cid, name, bool(ok), "" if ok else str(detail)))
    print("{} {} {}：{}".format(cid, "PASS" if ok else "FAIL", name, detail))


def run_checks() -> list[tuple[str, str, bool, str]]:
    lib = load_library(SAMPLES)
    results: list[tuple[str, str, bool, str]] = []

    def e1():
        errs = validate_samples(lib)
        return (not errs,
                "{} 份文件 / {} 条条款；{}".format(
                    len(lib["docs"]), len(lib["clauses"]),
                    "无错" if not errs else "; ".join(errs[:3])))

    def e2():
        clause = lib["by_uid"]["RULE-4800-2026-001#第2章 日前市场/2.7.1.1"]
        cite = cite_clause(lib, clause)
        parsed = parse_citation(lib, cite)
        ans = answer_question(lib, "小时平均节点电价的结算价格怎么定？", "2026-11-05")
        errs = validate_answer(lib, ans)
        five = ans["citations"][0]
        ok = (parsed["clause_uid"] == clause["clause_uid"]
              and parsed["effective_from"] == "2026-11-03"
              and all(five.get(k) for k in ("title", "clause_no", "version_label", "effective_from", "quote"))
              and not errs)
        return ok, "{}；五要素={}".format(cite, {k: five[k] for k in ("title", "clause_no", "version_label", "effective_from", "quote")})

    def e3():
        d1595 = lib["docs"]["RULE-1000-2021-001"]
        retail = lib["docs"]["RULE-4210-2025-001"]
        draft = lib["docs"]["RULE-1000-2026-901"]
        old_syn = lib["docs"]["RULE-9000-2025-001"]
        got = {
            "1105": resolve_status(lib, d1595, "2026-11-05"),
            "1110": resolve_status(lib, d1595, "2026-11-10"),
            "1111": resolve_status(lib, d1595, "2026-11-11"),
            "retail2030": resolve_status(lib, retail, "2030-01-01"),
            "draft": resolve_status(lib, draft, "2026-11-05"),
            "syn_old_0301": resolve_status(lib, old_syn, "2026-03-01"),
        }
        ok = (got["1105"] == "ACTIVE" and got["1110"] == "ACTIVE" and got["1111"] == "EXPIRED"
              and got["retail2030"] == "ACTIVE" and got["draft"] == "DRAFT"
              and got["syn_old_0301"] == "SUPERSEDED")
        return ok, str(got)

    def e4():
        d = diff_docs(lib, "RULE-9000-2025-001", "RULE-9000-2026-001")
        types = sorted((c["change_type"], c["reason"]) for c in d["changes"])
        ok = (d["summary"] == {"added": 1, "modified": 2, "repealed": 1}
              and ("REPEALED", "DOC_REPEALED") in types
              and ("ADDED", "NEW_CLAUSE") in types
              and ("MODIFIED", "TEXT_EDIT") in types)
        return ok, "summary={} changes={}".format(d["summary"], types)

    def e5():
        d = diff_docs(lib, "RULE-9000-2025-001", "RULE-9000-2026-001")
        renum = [c for c in d["changes"] if c["reason"] == "RENUMBERED"]
        ok = (len(renum) == 1
              and d["clause_map"].get("RULE-9000-2025-001#第三条") == "RULE-9000-2026-001#第二条"
              and renum[0]["change_type"] == "MODIFIED")
        return ok, "clause_map={}".format(d["clause_map"])

    def e6():
        refused = answer_question(lib, "储能容量补偿标准是多少？", "2026-11-05")
        answered = answer_question(lib, "小时平均节点电价的结算价格怎么定？", "2026-11-05")
        ok_ref = (refused["answer_status"] == "INSUFFICIENT_EVIDENCE" and not refused["citations"])
        ok_ans = (answered["answer_status"] == "ANSWERED" and validate_answer(lib, answered) == [])
        tampered = json.loads(json.dumps(answered))
        tampered["citations"][0]["quote"] = tampered["citations"][0]["quote"].replace("小时平均节点电价", "加权平均节点电价")
        ok_tamper = bool(validate_answer(lib, tampered))
        return (ok_ref and ok_ans and ok_tamper,
                "refused={} answered={} tamper_caught={}".format(
                    refused["answer_status"], answered["answer_status"], ok_tamper))

    def e7():
        first = build_alerts(lib, "2026-10-11")
        exp = [a for a in first if a["trigger"] == "EXPIRING_SOON" and a["doc_id"] == "RULE-1000-2021-001"]
        later = build_alerts(lib, "2026-10-20", existing={a["fingerprint"] for a in first})
        t7 = [a for a in build_alerts(lib, "2026-11-03") if a["trigger"] == "EXPIRING_SOON" and a["doc_id"] == "RULE-1000-2021-001"]
        t1 = [a for a in build_alerts(lib, "2026-11-10") if a["trigger"] == "EXPIRING_SOON" and a["doc_id"] == "RULE-1000-2021-001"]
        ok = (bool(exp) and not later and len(t7) == 1 and t7[0]["severity"] == "CRITICAL"
              and len(t1) == 1 and t1[0]["severity"] == "CRITICAL")
        return ok, "T30={} 同日重跑={} T7={} T1={}".format(
            bool(exp), len(later), t7[0]["severity"] if t7 else None, t1[0]["severity"] if t1 else None)

    def e8():
        gaps = evidence_gaps(lib)
        mid = [g for g in gaps if g["doc_id"] == "RULE-3000-2026-001"]
        ans = answer_question(lib, "绿电价格构成的规则依据？", "2026-11-05")
        pending_cited = [c for c in ans["citations"] if lib["by_uid"][c["clause_uid"]]["text_status"] != "VERBATIM"]
        ok = (bool(mid) and any("文号" in x for x in mid[0]["gaps"])
              and not pending_cited
              and ans["answer_status"] in ("INSUFFICIENT_EVIDENCE", "PARTIAL"))
        return ok, "缺口={} 问答={} 引用待补条款={}".format(
            mid[0]["gaps"] if mid else None, ans["answer_status"], len(pending_cited))

    check(results, "E1", "schema 与不变量校验", e1)
    check(results, "E2", "引用五要素可往返", e2)
    check(results, "E3", "as-of 现行版本判定", e3)
    check(results, "E4", "差异三类齐全", e4)
    check(results, "E5", "改序号识别", e5)
    check(results, "E6", "问答拒答/逐字/篡改拦截", e6)
    check(results, "E7", "提醒窗口与去重", e7)
    check(results, "E8", "Q01 缺口标注且不引用", e8)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="跑自检并写 verification.txt")
    args = parser.parse_args()
    results = run_checks()
    ok = all(r[2] for r in results)
    if args.verify:
        lines = [
            "规则知识库参考实现自检（#54）",
            "命令: python -X utf8 reference_rulekb.py --verify",
            "样例: {} 份文件 / {} 条条款".format(len(load_library(SAMPLES)["docs"]), len(load_library(SAMPLES)["clauses"])),
            "",
        ]
        for cid, name, passed, detail in results:
            lines.append("{} {} {}：{}".format(cid, "PASS" if passed else "FAIL", name, detail))
        lines.append("")
        lines.append("结论: {} / {} 项 PASS".format(sum(1 for r in results if r[2]), len(results)))
        VERIFY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
