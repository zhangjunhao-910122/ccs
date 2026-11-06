#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""#24 数据导入器参考实现。

范围（对应 docs/spec/data-importer/importer.schema.json）：
  · 四类内部导入：CSV / XLSX / PDF / 标准模板（外加 JSON 外部特征通道）
  · 字段映射、单位换算、类型归一
  · 质量规则 Q01-Q06 / I01-I06 / W09
  · 幂等入库（dataset,file_hash）、整批隔离、人工确认后回补
  · 血缘（file -> row、batch -> row、版本替代 edge）与 ImportReport（14 字段）

用法（Windows 必须 -X utf8）：
  python -X utf8 docs/spec/data-importer/reference_importer.py --verify
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPECS = ROOT.parent
sys.path.insert(0, str(SPECS / "data-storage"))

import reference_store as store  # noqa: E402  存储层规格（#25）

SPEC_PATH = ROOT / "importer.schema.json"
CONTRACT_PATH = SPECS / "data-contract" / "contract.schema.json"
POLICY_PATH = SPECS / "data-storage" / "storage.schema.json"

# 契约单位 <-> 源单位 的换算表（未登记的单位一律按不可换算处理，命中 I03）
UNIT_TABLE = {
    "mwh": ("MWh", Decimal("1")),
    "kwh": ("MWh", Decimal("0.001")),
    "wh": ("MWh", Decimal("0.000001")),
    "度": ("MWh", Decimal("0.001")),
    "元/mwh": ("CNY/MWh", Decimal("1")),
    "cny/mwh": ("CNY/MWh", Decimal("1")),
    "元": ("CNY", Decimal("1")),
    "cny": ("CNY", Decimal("1")),
}

BOOL_TRUE = {"是", "true", "1", "y", "yes", "t"}
BOOL_FALSE = {"否", "false", "0", "n", "no", "f"}

REPORT_FIELDS = [
    "source_id", "dataset", "batch_id", "file_hash", "status", "rows_read",
    "rows_committed", "rows_quarantined", "quality_issues", "registry_version",
    "importer_version", "started_at", "finished_at", "evidence_path",
]


class ImporterError(Exception):
    pass


class FileLevelError(Exception):
    """文件级阻断（读不了 / 指纹不符 / 行数超限 / 映射非法）。"""

    def __init__(self, rule_id: str, message: str, severity: str = "BLOCK"):
        super().__init__(message)
        self.rule_id = rule_id
        self.message = message
        self.severity = severity


@dataclass
class Issue:
    rule_id: str
    severity: str
    row_no: int | None
    field: str | None
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ImportReport:
    source_id: str
    dataset: str
    batch_id: str | None
    file_hash: str
    status: str
    rows_read: int
    rows_committed: int
    rows_quarantined: int
    quality_issues: list
    registry_version: str
    importer_version: str
    started_at: str
    finished_at: str
    evidence_path: str | None = None

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in REPORT_FIELDS}


# ------------------------------------------------------------------ 基础工具

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_spec() -> dict:
    return load_json(SPEC_PATH)


def load_contract() -> dict:
    return load_json(CONTRACT_PATH)


def load_policy() -> dict:
    return load_json(POLICY_PATH)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def to_jsonable(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value


def canonical(value) -> str:
    return json.dumps(to_jsonable(value), sort_keys=True, ensure_ascii=False)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def today() -> str:
    return date.today().isoformat()


UNIT_ALIAS = {"元/mwh": "cny/mwh", "元": "cny"}


def unit_canonical(unit: str) -> str:
    return re.sub(r"\s+", "", str(unit or "")).lower()


def expected_unit(field_name: str, defaults: dict) -> str | None:
    n = field_name.lower()
    if "energy" in n or "volume" in n:
        return defaults.get("energy_unit")
    if "price" in n:
        return defaults.get("price_unit")
    if "amount" in n:
        return defaults.get("amount_unit")
    return None


def template_fingerprint(headers) -> str:
    return hashlib.sha256("\n".join(str(h) for h in headers).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ 读取器

def read_csv(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, restkey="__extra__")
        headers = [(h or "").strip() for h in (reader.fieldnames or [])]
        records = []
        for row in reader:
            rec = {}
            for key, val in row.items():
                if key is None:
                    continue
                rec[(key or "").strip() if isinstance(key, str) else key] = val
            records.append(rec)
    return headers, records


def read_xlsx(path: Path, sheet: str | None = None):
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    names = list(wb.sheetnames)
    ws = wb[sheet] if sheet and sheet in names else wb.worksheets[0]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    headers = [("" if c is None else str(c).strip()) for c in (rows[0] if rows else [])]
    records = []
    for raw in rows[1:]:
        if all(c is None or str(c).strip() == "" for c in raw):
            continue
        rec = {}
        for idx, h in enumerate(headers):
            if not h:
                continue
            val = raw[idx] if idx < len(raw) else None
            rec[h] = "" if val is None else val
        records.append(rec)
    return headers, records, names


def read_pdf_text(path: Path) -> str:
    """最小 PDF 文本层提取：只取 Tj 文本，不做 OCR。"""
    data = path.read_bytes()
    out: list[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        chunk = match.group(1).decode("latin-1")
        for token in re.finditer(r"\((?:\\.|[^\\()])*\)\s*Tj|<([0-9A-Fa-f\s]+)>\s*Tj|T\*", chunk):
            text = token.group(0)
            if text.strip() == "T*":
                out.append("\n")
            elif text.startswith("<"):
                hexs = re.sub(r"\s", "", token.group(1))
                try:
                    out.append(bytes.fromhex(hexs).decode("utf-16-be"))
                except ValueError:
                    out.append("")
            else:
                inner = text[1:text.rindex(")")]
                inner = inner.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
                out.append(inner)
    return "".join(out)


def read_pdf_pairs(path: Path) -> list:
    text = read_pdf_text(path)
    if not text.strip():
        raise FileLevelError("I02", "PDF 无文本层（扫描件），不做 OCR")
    pairs = []
    for line in text.split("\n"):
        if ": " not in line:
            continue
        key, value = line.split(": ", 1)
        pairs.append((key.strip(), value.strip()))
    if not pairs:
        raise FileLevelError("I02", "PDF 未解析出 label: value 结构")
    return pairs


def read_json_rows(path: Path) -> list:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise FileLevelError("W09", "外部通道返回不可解析的 JSON：%s" % exc, severity="WARN")
    if isinstance(payload, dict):
        payload = payload.get("data", payload.get("items", []))
    if not isinstance(payload, list):
        raise FileLevelError("W09", "外部通道返回的不是数组", severity="WARN")
    return [r for r in payload if isinstance(r, dict)]


# ------------------------------------------------------------------ 类型归一

def coerce_value(contract: dict, field: dict, raw, issues: list, row_no):
    name = field["name"]
    ftype = field["type"]
    if raw is None:
        return None
    text = raw.strip() if isinstance(raw, str) else raw
    if isinstance(text, str) and text == "":
        return None
    if ftype == "int":
        try:
            return int(str(text))
        except (TypeError, ValueError):
            issues.append(Issue("Q03", "BLOCK", row_no, name, "%s 不是整数：%r" % (name, raw)))
            return None
    if ftype == "decimal":
        try:
            return Decimal(str(text))
        except InvalidOperation:
            issues.append(Issue("Q03", "BLOCK", row_no, name, "%s 不是合法小数：%r" % (name, raw)))
            return None
    if ftype == "bool":
        if isinstance(text, bool):
            return text
        key = str(text).strip().lower()
        if key in BOOL_TRUE:
            return True
        if key in BOOL_FALSE:
            return False
        issues.append(Issue("Q03", "BLOCK", row_no, name, "%s 不是布尔值：%r" % (name, raw)))
        return None
    if ftype == "date":
        try:
            datetime.strptime(str(text)[:10], "%Y-%m-%d")
        except ValueError:
            issues.append(Issue("Q03", "BLOCK", row_no, name, "%s 不是 YYYY-MM-DD：%r" % (name, raw)))
            return None
        return str(text)[:10]
    if ftype == "datetime":
        value = str(text)
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            issues.append(Issue("Q03", "BLOCK", row_no, name, "%s 不是 ISO 时刻：%r" % (name, raw)))
            return None
        if not re.search(r"[+-]\d{2}:\d{2}$", value):
            issues.append(Issue("Q03", "BLOCK", row_no, name, "%s 缺少时区偏移：%r" % (name, raw)))
            return None
        return value
    if ftype == "json":
        try:
            json.loads(str(text))
        except ValueError:
            issues.append(Issue("Q03", "BLOCK", row_no, name, "%s 不是合法 JSON" % name))
            return None
        return str(text)
    if ftype == "array":
        try:
            parsed = json.loads(str(text))
        except ValueError:
            issues.append(Issue("Q03", "BLOCK", row_no, name, "%s 不是合法 JSON 数组" % name))
            return None
        if not isinstance(parsed, list):
            issues.append(Issue("Q03", "BLOCK", row_no, name, "%s 不是数组" % name))
            return None
        return parsed
    if ftype == "enum":
        allowed = store.enum_values(contract, field)
        if allowed is not None and str(text) not in allowed:
            issues.append(Issue("Q03", "BLOCK", row_no, name, "%s=%r 不在允许值内" % (name, text)))
            return None
        return str(text)
    return str(text)


# ------------------------------------------------------------------ 映射

def map_source(source: dict, records: list, contract: dict, policy: dict, spec: dict,
               issues: list) -> list:
    dkey = source["dataset"]
    ds = contract["datasets"][dkey]
    fields = {f["name"]: f for f in ds["fields"]}
    tbd = {f["name"] for f in ds["fields"] if f.get("confidence") == "TBD"}
    derivable = set((spec.get("derivable_fields") or {}).get(dkey, []))
    defaults = contract["defaults"]
    cols = source.get("columns")
    pending = set(source.get("pending_confirmation") or [])
    rows = []

    if cols is None:
        # 直通来源（JSON 外部通道）：源键即契约字段
        for idx, rec in enumerate(records, start=1):
            row = {}
            for key, val in rec.items():
                if key not in fields:
                    continue
                row[key] = coerce_value(contract, fields[key], val, issues, idx)
            for key, val in (source.get("params") or {}).items():
                if key in fields and row.get(key) in (None, ""):
                    row[key] = coerce_value(contract, fields[key], val, issues, idx)
            row["source_row_no"] = idx
            rows.append(row)
        return rows

    targets = {c["target"] for c in cols}
    for name in sorted(pending):
        if name not in tbd:
            issues.append(Issue("I04", "BLOCK", None, name,
                                "pending_confirmation 声明了非 TBD 字段 %s" % name))
    for name in sorted(targets & tbd):
        if name not in pending:
            issues.append(Issue("I04", "BLOCK", None, name,
                                "TBD 字段 %s 直通但未在 pending_confirmation 声明" % name))
        else:
            issues.append(Issue("TBD_PASSTHROUGH", "INFO", None, name,
                                "TBD 字段 %s 直通，待客户确认" % name))
    for f in ds["fields"]:
        if f.get("required", True) is False:
            continue
        if f["name"] in targets or f["name"] in derivable:
            continue
        issues.append(Issue("I04", "BLOCK", None, f["name"],
                            "映射缺少必填字段 %s" % f["name"]))

    for idx, rec in enumerate(records, start=1):
        row = {}
        for col in cols:
            target = col["target"]
            raw = rec.get(col["source"])
            if target not in fields:
                row[target] = "" if raw is None else raw
                continue
            value = coerce_value(contract, fields[target], raw, issues, idx)
            if value is None:
                row[target] = None
                continue
            want = expected_unit(target, defaults)
            unit = col.get("unit")
            if not unit:
                found = re.search(r"\(([^()]+)\)\s*$", str(col["source"]))
                unit = found.group(1) if found else None
            if unit and want:
                src_canon = unit_canonical(unit)
                src_canon = UNIT_ALIAS.get(src_canon, src_canon)
                want_canon = unit_canonical(want)
                if src_canon != want_canon:
                    factor = col.get("factor")
                    if factor is not None:
                        value = quantize(value * Decimal(str(factor)), fields[target], policy)
                    else:
                        conv = UNIT_TABLE.get(src_canon)
                        if conv is None:
                            issues.append(Issue("I03", "BLOCK", idx, target,
                                                "单位 %s 无法换算为契约单位 %s" % (unit, want)))
                            row[target] = value
                            continue
                        value = quantize(value * conv[1], fields[target], policy)
                        issues.append(Issue("Q05", "WARN", idx, target,
                                            "源单位 %s 与契约单位 %s 不一致，按 %s 换算（来源未登记换算率）"
                                            % (unit, want, conv[1])))
            row[target] = value
        row["source_row_no"] = idx
        rows.append(row)
    return rows


def quantize(value: Decimal, field: dict, policy: dict) -> Decimal:
    scale = store.decimal_scale(field["name"], policy)
    return value.quantize(Decimal(1).scaleb(-scale))


# ------------------------------------------------------------------ 15 分钟聚合

def aggregate_15min(rows: list, issues: list) -> list:
    """15 分钟点 -> 小时行（#26 T5/T7）：电量求和，点数不足 4 降级 PARTIAL。"""
    groups = {}
    order = []
    for row in rows:
        user = row.get("user_id")
        day = row.get("business_date")
        point = row.get("point_time")
        if not user or not day or not point:
            issues.append(Issue("Q01", "BLOCK", row.get("source_row_no"), "point_time",
                                "15 分钟源缺少用户 / 日期 / 时刻"))
            continue
        hh, mm = str(point).split(":")[:2]
        total = int(hh) * 60 + int(mm)
        day_obj = datetime.strptime(str(day), "%Y-%m-%d").date()
        if total == 0:
            day_obj = day_obj - timedelta(days=1)
            hour = 24
        else:
            hour = (total + 59) // 60
        key = (user, day_obj.isoformat(), hour)
        if key not in groups:
            groups[key] = {"user_id": user, "business_date": day_obj.isoformat(),
                           "slot_no": hour, "points": [], "energy": Decimal("0"),
                           "rows": []}
            order.append(key)
        groups[key]["points"].append(row.get("energy_mwh"))
        groups[key]["rows"].append(row.get("source_row_no"))
        if row.get("energy_mwh") is not None:
            groups[key]["energy"] += Decimal(str(row["energy_mwh"]))

    out = []
    for key in order:
        g = groups[key]
        count = len(g["points"])
        energy = g["energy"]
        row = {
            "user_id": g["user_id"],
            "business_date": g["business_date"],
            "slot_no": g["slot_no"],
            "interval_start": "%sT%02d:00:00+08:00" % (g["business_date"], g["slot_no"] - 1),
            "source_15min_count": count,
            "source_row_no": g["rows"][0],
            "source_row_nos": list(g["rows"]),
        }
        if count >= 4:
            row["energy_mwh"] = energy
            row["observed_sum_mwh"] = energy
            row["quality_flag"] = "OK"
        else:
            row["energy_mwh"] = None
            row["observed_sum_mwh"] = energy
            row["quality_flag"] = "PARTIAL"
            issues.append(Issue("I06", "WARN", g["rows"][0], "source_15min_count",
                                "%s %s 第 %d 时段只有 %d 个 15 分钟点，小时电量置空并降级 PARTIAL"
                                % (g["user_id"], g["business_date"], g["slot_no"], count)))
        out.append(row)
    return out


# ------------------------------------------------------------------ 校验

def validate_rows(dkey: str, rows: list, contract: dict, policy: dict, issues: list,
                  skip_required: bool = False) -> None:
    ds = contract["datasets"][dkey]
    # Q01 必填 + Q03 枚举/小数位/类型（复用存储层 validate_row 的判定）
    for row in rows:
        errs = store.validate_row(contract, dkey, row, policy)
        for code, message in errs:
            if skip_required and code.startswith("REQUIRED"):
                continue
            rule = "Q04" if code == "SLOT_INTERVAL" else ("Q01" if code.startswith("REQUIRED") else "Q03")
            issues.append(Issue(rule, "BLOCK", row.get("source_row_no"), code.split(":")[-1], message))
    # Q02 业务键重复
    seen = {}
    for row in rows:
        key = "|".join("" if row.get(k) is None else str(row.get(k)) for k in ds["business_key"])
        if key in seen:
            issues.append(Issue("Q02", "BLOCK", row.get("source_row_no"), "*", "业务键重复：%s" % key))
        seen[key] = True
    # Q04 时标错位（提交/出清日必须落在交易日；结算日不早于电量归属日）
    for row in rows:
        if dkey == "D07" and row.get("submitted_at") and row.get("trade_date"):
            if str(row["submitted_at"])[:10] != str(row["trade_date"])[:10]:
                issues.append(Issue("Q04", "BLOCK", row.get("source_row_no"), "submitted_at",
                                    "提交时刻 %s 与交易日 %s 不一致" % (row["submitted_at"], row["trade_date"])))
        if dkey == "D08" and row.get("cleared_at") and row.get("trade_date"):
            if str(row["cleared_at"])[:10] != str(row["trade_date"])[:10]:
                issues.append(Issue("Q04", "BLOCK", row.get("source_row_no"), "cleared_at",
                                    "出清时刻 %s 与交易日 %s 不一致" % (row["cleared_at"], row["trade_date"])))
        if dkey == "D09" and row.get("settle_date") and row.get("energy_date"):
            if str(row["settle_date"])[:10] < str(row["energy_date"])[:10]:
                issues.append(Issue("Q04", "BLOCK", row.get("source_row_no"), "settle_date",
                                    "结算日 %s 早于电量归属日 %s" % (row["settle_date"], row["energy_date"])))
    # Q06 逻辑异常：金额 = 电量 x 价格；日历与工作日标志
    tolerance = Decimal(str(load_spec().get("amount_tolerance", "0.01")))
    for row in rows:
        if dkey == "D09" and row.get("energy_mwh") is not None and row.get("price") is not None \
                and row.get("amount") is not None:
            expect = Decimal(str(row["energy_mwh"])) * Decimal(str(row["price"]))
            if abs(expect - Decimal(str(row["amount"]))) > tolerance:
                issues.append(Issue("Q06", "BLOCK", row.get("source_row_no"), "amount",
                                    "金额 %s 与 电量x价格 %s 偏差超过 %s"
                                    % (row["amount"], expect, tolerance)))
        if dkey == "D12" and row.get("day_type") and row.get("is_workday") is not None:
            workday = row["day_type"] in ("NORMAL_WORKDAY", "MAKEUP_WORKDAY")
            if bool(row["is_workday"]) != workday:
                issues.append(Issue("Q06", "BLOCK", row.get("source_row_no"), "is_workday",
                                    "is_workday=%s 与 day_type=%s 矛盾" % (row["is_workday"], row["day_type"])))


# ------------------------------------------------------------------ 入库

def next_batch_id(conn) -> str:
    count = conn.execute("SELECT COUNT(*) FROM ingest_batch").fetchone()[0]
    return "IMP-%s-%04d" % (datetime.now().strftime("%Y%m%d"), count + 1)


def ensure_aux(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS quarantine_resolution (
        qid INTEGER PRIMARY KEY,
        decision TEXT NOT NULL,
        resolved_by TEXT NOT NULL,
        resolved_at TEXT NOT NULL,
        overrides_json TEXT,
        new_batch_id TEXT,
        new_record_id TEXT,
        note TEXT)""")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(quarantine_row)").fetchall()}
    if cols and "file_hash" not in cols:
        conn.execute("ALTER TABLE quarantine_row ADD COLUMN file_hash TEXT")


def row_valid_from(spec: dict, dkey: str, row: dict) -> str:
    field = (spec.get("valid_from_fields") or {}).get(dkey)
    if field and row.get(field):
        return str(row[field])[:10]
    return today()


def quarantine_batch(conn, dkey, batch_id, source_file, fh, importer_version, rows, issues, stamp):
    ensure_aux(conn)
    summary = "; ".join(sorted({i.rule_id for i in issues if i.severity == "BLOCK"}))[:1000]
    conn.execute("INSERT OR REPLACE INTO ingest_batch (batch_id, dataset, source_file, file_hash, "
                 "importer_version, row_count, status, error_summary, imported_at) VALUES (?,?,?,?,?,?,?,?,?)",
                 (batch_id, dkey, source_file, fh, importer_version, len(rows), "BLOCKED", summary, stamp))
    for row in rows:
        row_issues = [i for i in issues if i.severity == "BLOCK" and i.row_no == row.get("source_row_no")]
        rule = row_issues[0].rule_id if row_issues else "BATCH_BLOCKED"
        message = "; ".join(i.message for i in row_issues) or "同批次其他行存在阻断错误"
        conn.execute("INSERT INTO quarantine_row (batch_id, dataset, source_row_no, rule_id, message, "
                     "raw_json, created_at, file_hash) VALUES (?,?,?,?,?,?,?,?)",
                     (batch_id, dkey, row.get("source_row_no"), rule, message,
                      canonical(row), stamp, fh))
    conn.commit()


def insert_rows(conn, contract, policy, spec, dkey, rows, source, fh, batch_id, issues, stamp):
    """写主表 + 血缘 + 版本替代。返回 (插入行数, 跳过行数)。"""
    ds = contract["datasets"][dkey]
    table = policy["tables"][dkey]["table"]
    source_system = source.get("registry_source") or source["source_id"]
    inserted = 0
    skipped = 0
    for row in rows:
        row.setdefault("is_current", True)
        row["valid_from"] = row_valid_from(spec, dkey, row)
        ckey = store.current_key_of(ds, row)
        rid = store.record_id_of(dkey, ds, row)
        row["_record_id"] = rid
        exists = conn.execute("SELECT record_id FROM %s WHERE record_id=?" % store.q(table), (rid,)).fetchone()
        if exists:
            skipped += 1
            issues.append(Issue("DUPLICATE_RECORD_SKIPPED", "INFO", row.get("source_row_no"), "*",
                                "记录已存在，跳过：%s" % rid[:12]))
            continue
        hit = conn.execute("SELECT record_id, src_valid_from FROM %s WHERE dataset=? AND current_key=? "
                           "AND src_is_current=1" % store.q(table), (dkey, ckey)).fetchone()
        if hit is not None:
            old_valid_from = hit["src_valid_from"]
            if old_valid_from and row["valid_from"] <= old_valid_from:
                issues.append(Issue("Q04", "BLOCK", row.get("source_row_no"), "valid_from",
                                    "版本时序倒置：新版本 valid_from=%s 不晚于在册版本 %s"
                                    % (row["valid_from"], old_valid_from)))
                continue
            conn.execute("UPDATE %s SET src_valid_to=?, src_is_current=0 WHERE record_id=?"
                         % store.q(table), (row["valid_from"], hit["record_id"]))
            edge = store.sha256_text("%s|%s|SUPERSEDE" % (hit["record_id"], rid))
            conn.execute("INSERT OR IGNORE INTO lineage_edge (edge_id, from_ref, to_ref, transform_id, "
                         "run_id, created_at) VALUES (?,?,?,?,?,?)",
                         (edge, "row:%s" % hit["record_id"], "row:%s" % rid, "SUPERSEDE", batch_id, stamp))
        names = ["record_id", "dataset"]
        values = [rid, dkey]
        warn_rows = {i.row_no for i in issues if i.severity == "WARN" and i.row_no == row.get("source_row_no")}
        conflict = 1 if (warn_rows or row.get("quality_flag") not in (None, "OK")) else 0
        for f in ds["fields"]:
            names.append(store.q(f["name"]))
            values.append(store.to_physical(f, row.get(f["name"]), policy))
        for name, _ctype in store.COMMON_FIELDS:
            pname = store.physical_name(name, policy)
            if name == "batch_id":
                value = batch_id
            elif name == "source_system":
                value = source_system
            elif name == "source_file":
                value = source.get("path")
            elif name == "source_row_no":
                value = row.get("source_row_no")
            elif name == "file_hash":
                value = fh
            elif name == "imported_at":
                value = stamp
            elif name == "importer_version":
                value = spec["importer_version"]
            elif name == "rule_version":
                value = spec["rule_version"]
            elif name == "valid_from":
                value = row["valid_from"]
            elif name == "valid_to":
                value = None
            elif name == "is_current":
                value = 1 if row.get("is_current") else 0
            else:
                value = row.get(name)
            names.append(store.q(pname))
            values.append(value)
        names += [store.q("current_key"), store.q("conflict_flag")]
        values += [ckey, conflict]
        sql = "INSERT INTO %s (%s) VALUES (%s)" % (store.q(table), ", ".join(names),
                                                  ", ".join("?" for _ in values))
        try:
            conn.execute(sql, values)
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            issues.append(Issue("Q03", "BLOCK", row.get("source_row_no"), "*", "入库失败：%s" % exc))
            continue
        inserted += 1
        row_nos = row.get("source_row_nos") or [row.get("source_row_no")]
        for source_row_no in row_nos:
            for from_ref, transform in (("file:%s#%s" % (fh, source_row_no), "IMPORT"),
                                        ("batch:%s" % batch_id, "ASSIGN_BATCH")):
                edge = store.sha256_text("%s|%s|%s" % (from_ref, rid, transform))
                conn.execute("INSERT OR IGNORE INTO lineage_edge (edge_id, from_ref, to_ref, transform_id, "
                             "run_id, created_at) VALUES (?,?,?,?,?,?)",
                             (edge, from_ref, "row:%s" % rid, transform, batch_id, stamp))
    conn.commit()
    return inserted, skipped


# ------------------------------------------------------------------ 主流程

def import_source(conn, contract, policy, spec, source_id, base_dir, evidence_dir=None) -> ImportReport:
    started = now_iso()
    source = next((s for s in spec["sources"] if s["source_id"] == source_id), None)
    if source is None:
        raise ImporterError("未登记的来源：%s" % source_id)
    dkey = source["dataset"]
    path = (base_dir / source["path"]).resolve()
    fh = sha256_file(path)
    issues: list = []
    rows: list = []
    dup = conn.execute("SELECT batch_id FROM ingest_batch WHERE dataset=? AND file_hash=?",
                       (dkey, fh)).fetchone()
    if dup is not None:
        report = ImportReport(
            source_id=source_id, dataset=dkey, batch_id=dup["batch_id"], file_hash=fh,
            status="DUPLICATE", rows_read=0, rows_committed=0, rows_quarantined=0,
            quality_issues=[], registry_version=spec["registry_version"],
            importer_version=spec["importer_version"], started_at=started, finished_at=now_iso())
        if evidence_dir is not None:
            evidence_dir.mkdir(parents=True, exist_ok=True)
            out = evidence_dir / ("%s-%s.json" % (source_id, datetime.now().strftime("%Y%m%d%H%M%S")))
            out.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            report.evidence_path = str(out)
        return report
    rows_read = 0
    status = "FAILED"
    batch_id = None
    committed = 0
    quarantined = 0

    try:
        kind = source["kind"]
        if kind == "csv":
            headers, records = read_csv(path)
        elif kind == "xlsx":
            headers, records, _names = read_xlsx(path)
        elif kind == "json":
            headers, records = [], read_json_rows(path)
        elif kind == "pdf":
            pairs = read_pdf_pairs(path)
            headers = []
            records = [{label: value for label, value in pairs}]
        elif kind == "template":
            headers, records, names = read_xlsx(path)
            expected_fp = source.get("template_fingerprint")
            actual_fp = template_fingerprint(headers)
            if expected_fp and actual_fp != expected_fp:
                raise FileLevelError("I01", "标准模板指纹不符：期望 %s，实际 %s" % (expected_fp, actual_fp))
            sheet = source.get("dictionary_sheet")
            if sheet and sheet not in names:
                raise FileLevelError("I04", "标准模板缺少数据字典 sheet：%s" % sheet)
            else:
                dict_headers, dict_rows, _ = read_xlsx(path, sheet)
                key_field = "列名" if "列名" in dict_headers else (dict_headers[0] if dict_headers else None)
                documented = set()
                for rec in dict_rows:
                    if key_field and key_field in rec:
                        documented.add(str(rec[key_field]))
                    elif len(rec) == 1:
                        documented.add(str(next(iter(rec.values()))))
                for col in source.get("columns", []):
                    if col["source"] not in documented:
                        raise FileLevelError("I04", "数据字典与映射不一致：%s 未在字典中登记" % col["source"])
        else:
            raise FileLevelError("I04", "不支持的来源类型：%s" % kind)

        rows_read = len(records)
        limit = source.get("max_rows", spec.get("max_rows"))
        if limit is not None and rows_read > int(limit):
            raise FileLevelError("I05", "批次行数 %d 超过上限 %d" % (rows_read, int(limit)))

        if kind == "pdf":
            labels = source.get("labels") or {}
            mapped = []
            for rec in records:
                row = {}
                for target, label in labels.items():
                    row[target] = rec.get(label)
                row["file_hash"] = fh
                row["source_row_no"] = 1
                mapped.append(row)
            rows = mapped
            for row in rows:
                ds = contract["datasets"][dkey]
                for f in ds["fields"]:
                    if f["name"] in row:
                        row[f["name"]] = coerce_value(contract, f, row[f["name"]], issues, 1)
            validate_rows(dkey, rows, contract, policy, issues, skip_required=True)
        else:
            rows = map_source(source, records, contract, policy, spec, issues)
            if source.get("grain") == "15min":
                rows = aggregate_15min(rows, issues)
                rows_read = len(records)
            validate_rows(dkey, rows, contract, policy, issues)

        file_block = [i for i in issues if i.severity == "BLOCK" and i.row_no is None]
        row_block = [i for i in issues if i.severity == "BLOCK" and i.row_no is not None]
        stamp = now_iso()
        if file_block:
            issues.extend(file_block)
            batch_id = next_batch_id(conn)
            conn.execute("INSERT OR REPLACE INTO ingest_batch (batch_id, dataset, source_file, file_hash, "
                         "importer_version, row_count, status, error_summary, imported_at) VALUES (?,?,?,?,?,?,?,?,?)",
                         (batch_id, dkey, source.get("path"), fh, spec["importer_version"], 0, "BLOCKED",
                          "; ".join(sorted({i.rule_id for i in file_block})), stamp))
            conn.commit()
            status = "FAILED"
        elif row_block:
            batch_id = next_batch_id(conn)
            quarantine_batch(conn, dkey, batch_id, source.get("path"), fh, spec["importer_version"],
                             rows, issues, stamp)
            quarantined = len(rows)
            status = "QUARANTINED"
        else:
            # WARN 行：质量标识降级 + conflict_flag=1（存储层策略）
            warn_rows = {i.row_no for i in issues if i.severity == "WARN" and i.row_no is not None}
            for row in rows:
                if row.get("source_row_no") in warn_rows:
                    if row.get("quality_flag") == "OK":
                        row["quality_flag"] = "ESTIMATED"
            rows.sort(key=lambda r: (str(r.get("valid_from") or ""), r.get("source_row_no") or 0))
            batch_id = next_batch_id(conn)
            committed, _skipped = insert_rows(conn, contract, policy, spec, dkey, rows, source, fh,
                                              batch_id, issues, stamp)
            conn.execute("INSERT OR REPLACE INTO ingest_batch (batch_id, dataset, source_file, file_hash, "
                         "importer_version, row_count, status, error_summary, imported_at) VALUES (?,?,?,?,?,?,?,?,?)",
                         (batch_id, dkey, source.get("path"), fh, spec["importer_version"], committed, "COMMITTED",
                          "; ".join(sorted({i.rule_id for i in issues})), stamp))
            conn.commit()
            status = "COMMITTED"
    except FileLevelError as exc:
        issues.append(Issue(exc.rule_id, exc.severity, None, None, exc.message))
        status = "FAILED"

    report = ImportReport(
        source_id=source_id,
        dataset=dkey,
        batch_id=batch_id,
        file_hash=fh,
        status=status,
        rows_read=rows_read,
        rows_committed=committed,
        rows_quarantined=quarantined,
        quality_issues=[i.as_dict() for i in issues],
        registry_version=spec["registry_version"],
        importer_version=spec["importer_version"],
        started_at=started,
        finished_at=now_iso(),
    )
    if evidence_dir is not None:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        out = evidence_dir / ("%s-%s.json" % (source_id, datetime.now().strftime("%Y%m%d%H%M%S")))
        out.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        report.evidence_path = str(out)
    return report


def resolve_quarantine(conn, contract, policy, spec, qid, decision, resolved_by,
                       overrides=None, note="") -> dict:
    """人工确认后回补（F012）：ACCEPT 生成新批次入库，REJECT 只留痕。"""
    ensure_aux(conn)
    row = conn.execute("SELECT * FROM quarantine_row WHERE qid=?", (qid,)).fetchone()
    if row is None:
        raise ImporterError("隔离行不存在：%s" % qid)
    stamp = now_iso()
    if decision == "REJECT":
        conn.execute("INSERT OR REPLACE INTO quarantine_resolution (qid, decision, resolved_by, resolved_at, "
                     "overrides_json, new_batch_id, new_record_id, note) VALUES (?,?,?,?,?,?,?,?)",
                     (qid, "REJECT", resolved_by, stamp, canonical(overrides or {}), None, None, note))
        conn.commit()
        return {"qid": qid, "decision": "REJECT", "new_batch_id": None, "new_record_id": None}
    if decision != "ACCEPT":
        raise ImporterError("处置结论只能是 ACCEPT 或 REJECT")

    dkey = row["dataset"]
    ds = contract["datasets"][dkey]
    payload = json.loads(row["raw_json"])
    payload = {k: v for k, v in payload.items() if not k.startswith("_")}
    for key, val in (overrides or {}).items():
        payload[key] = val
    issues: list = []
    fixed = {}
    for f in ds["fields"]:
        if f["name"] in payload:
            fixed[f["name"]] = coerce_value(contract, f, payload[f["name"]], issues, payload.get("source_row_no"))
    fixed["source_row_no"] = payload.get("source_row_no")
    fixed["is_current"] = True
    validate_rows(dkey, [fixed], contract, policy, issues)
    if any(i.severity == "BLOCK" for i in issues):
        conn.execute("INSERT OR REPLACE INTO quarantine_resolution (qid, decision, resolved_by, resolved_at, "
                     "overrides_json, new_batch_id, new_record_id, note) VALUES (?,?,?,?,?,?,?,?)",
                     (qid, "REJECT", resolved_by, stamp, canonical(overrides or {}), None, None,
                      "修正后仍不合规：" + "; ".join(i.message for i in issues)))
        conn.commit()
        raise ImporterError("修正后仍不合规：%s" % "; ".join(i.message for i in issues))

    source = {"source_id": "MANUAL-FIX", "dataset": dkey, "path": "quarantine:%s" % qid}
    batch_id = next_batch_id(conn)
    row_fh = row["file_hash"] if ("file_hash" in row.keys() and row["file_hash"]) else None
    if not row_fh:
        hit = conn.execute("SELECT file_hash FROM ingest_batch WHERE batch_id=?",
                           (row["batch_id"],)).fetchone()
        row_fh = hit["file_hash"] if hit is not None else ""
    inserted, _skipped = insert_rows(conn, contract, policy, spec, dkey, [fixed], source,
                                     row_fh, batch_id, issues, stamp)
    record_id = fixed.get("_record_id")
    conn.execute("INSERT OR REPLACE INTO quarantine_resolution (qid, decision, resolved_by, resolved_at, "
                 "overrides_json, new_batch_id, new_record_id, note) VALUES (?,?,?,?,?,?,?,?)",
                 (qid, "ACCEPT", resolved_by, stamp, canonical(overrides or {}), batch_id, record_id, note))
    conn.commit()
    return {"qid": qid, "decision": "ACCEPT", "new_batch_id": batch_id, "new_record_id": record_id,
            "rows": inserted}


def trace_row(conn, record_id) -> list:
    rows = conn.execute("SELECT from_ref, transform_id FROM lineage_edge WHERE to_ref=?",
                        ("row:%s" % record_id,)).fetchall()
    return [{"from_ref": r["from_ref"], "transform_id": r["transform_id"]} for r in rows]


def query_as_of(conn, policy, contract, dkey, keys, as_of) -> list:
    ds = contract["datasets"][dkey]
    table = policy["tables"][dkey]["table"]
    where = ["dataset=?"]
    params = [dkey]
    for name, val in keys.items():
        where.append("%s=?" % store.q(name))
        params.append(val)
    where.append("src_valid_from<=?")
    params.append(as_of)
    where.append("(src_valid_to IS NULL OR src_valid_to>?)")
    params.append(as_of)
    sql = "SELECT record_id, %s FROM %s WHERE %s" % (
        ", ".join(store.q(f["name"]) for f in ds["fields"]), store.q(table), " AND ".join(where))
    out = []
    for row in conn.execute(sql, params).fetchall():
        item = {"record_id": row["record_id"]}
        for f in ds["fields"]:
            item[f["name"]] = store.from_physical(f, row[f["name"]], policy)
        out.append(item)
    return out


# ------------------------------------------------------------------ 自检

def run_checks(verbose=True) -> list:
    spec = load_spec()
    contract = load_contract()
    policy = load_policy()
    conn = store.connect(":memory:")
    store.build_schema(conn, contract, policy)
    ensure_aux(conn)
    results = []

    def check(code, name, fn):
        try:
            ok, message = fn()
        except Exception as exc:  # noqa: BLE001
            ok, message = False, "异常：%s" % exc
        results.append({"code": code, "name": name, "ok": bool(ok), "message": message})
        if verbose:
            print("[%s] %s %s" % (code, "PASS" if ok else "FAIL", message))
        return ok

    evidence_dir = ROOT / spec.get("evidence_dir", "_evidence")

    def imp(source_id):
        return import_source(conn, contract, policy, spec, source_id, ROOT, evidence_dir)

    def table_count(dkey):
        table = policy["tables"][dkey]["table"]
        return conn.execute("SELECT COUNT(*) FROM %s" % store.q(table)).fetchone()[0]

    good = ["SRC-INT-USER-CSV", "SRC-INT-USER-XLSX", "SRC-INT-METER-CSV", "SRC-INT-LOAD-15MIN-CSV",
            "SRC-INT-LOAD-TEMPLATE", "SRC-INT-WHALE-XLSX", "SRC-INT-RETAIL-XLSX", "SRC-INT-DECL-CSV",
            "SRC-INT-TRADE-CSV", "SRC-INT-DAILY-CSV", "SRC-INT-MONTHLY-CSV", "SRC-INT-RULE-PDF",
            "SRC-EXT-WEATHER-JSON", "SRC-EXT-CALENDAR-CSV", "SRC-EXT-PLAN-CSV"]

    def e1():
        reports = [imp(sid) for sid in good]
        bad = [(r.source_id, r.status) for r in reports if r.status != "COMMITTED"]
        datasets = ["D02", "D03", "D04", "D05", "D06", "D07", "D08", "D09", "D10", "D01",
                    "D11", "D12", "D13"]
        empty = [d for d in datasets if table_count(d) == 0]
        return (not bad and not empty,
                "15 个来源全部入库；非 COMMITTED=%s；空表=%s；行数=%s"
                % (bad, empty, {d: table_count(d) for d in datasets}))
    check("E1", "八类内部数据 + 外部通道各有可跑样例", e1)

    def e2():
        before = table_count("D02")
        rep = imp("SRC-INT-USER-CSV")
        after = table_count("D02")
        return (rep.status == "DUPLICATE" and before == after,
                "重复导入 status=%s，行数 %s -> %s" % (rep.status, before, after))
    check("E2", "重复导入幂等：数据量与报告不变", e2)

    def e3():
        expect = {
            "SRC-TEST-DIRTY-Q01": ("Q01", "QUARANTINED"),
            "SRC-TEST-DIRTY-Q02": ("Q02", "QUARANTINED"),
            "SRC-TEST-DIRTY-Q03": ("Q03", "QUARANTINED"),
            "SRC-TEST-DIRTY-Q04": ("Q04", "QUARANTINED"),
            "SRC-TEST-DIRTY-Q05": ("Q05", "COMMITTED"),
            "SRC-TEST-DIRTY-Q06": ("Q06", "QUARANTINED"),
            "SRC-TEST-DIRTY-I03": ("I03", "QUARANTINED"),
            "SRC-TEST-I04-MISSING-REQUIRED": ("I04", "FAILED"),
            "SRC-TEST-I05-TOO-LARGE": ("I05", "FAILED"),
            "SRC-TEST-I02-NO-TEXT-LAYER": ("I02", "FAILED"),
            "SRC-TEST-W09-BROKEN-JSON": ("W09", "FAILED"),
        }
        detail = []
        ok_all = True
        for sid, (rule, want_status) in expect.items():
            rep = imp(sid)
            rules = {q["rule_id"] for q in rep.quality_issues}
            ok = rule in rules and rep.status == want_status
            ok_all = ok_all and ok
            detail.append("%s:%s/%s%s" % (sid.replace("SRC-TEST-", ""), rep.status, rule,
                                           "" if ok else "✗"))
        return ok_all, "；".join(detail)
    check("E3", "质量报告能定位异常行与类型（Q/I/W 规则）", e3)

    def e4():
        watched = ["D02", "D03", "D04", "D05", "D06", "D07", "D08", "D09", "D10", "D01", "D11", "D12", "D13"]
        before = {d: table_count(d) for d in watched}
        for sid in ["SRC-TEST-DIRTY-Q01", "SRC-TEST-DIRTY-Q02", "SRC-TEST-DIRTY-Q03",
                    "SRC-TEST-DIRTY-Q04", "SRC-TEST-DIRTY-Q06", "SRC-TEST-DIRTY-I03"]:
            imp(sid)
        after = {d: table_count(d) for d in watched}
        changed = {d: (before[d], after[d]) for d in watched if before[d] != after[d]}
        quarantined = conn.execute("SELECT COUNT(*) FROM quarantine_row").fetchone()[0]
        return (not changed and quarantined > 0,
                "主表行数变化=%s；隔离区 %d 行" % (changed or "无", quarantined))
    check("E4", "阻断级异常不进主表，只进隔离区", e4)

    def e5():
        row = conn.execute("SELECT record_id FROM d09_daily_settlement LIMIT 1").fetchone()
        edges = trace_row(conn, row["record_id"])
        refs = {e["from_ref"].split("#")[0] for e in edges}
        has_file = any(e["from_ref"].startswith("file:") for e in edges)
        has_batch = any(e["from_ref"].startswith("batch:") for e in edges)
        return (has_file and has_batch,
                "血缘边 %d 条：%s" % (len(edges), sorted(refs)))
    check("E5", "血缘能答「来自哪个文件哪一批次」", e5)

    def e6():
        row = conn.execute("SELECT energy_mwh, quality_flag, conflict_flag FROM d04_load_hourly "
                           "WHERE user_id='USER-4201-000123-X' AND slot_no=1").fetchone()
        scale = store.decimal_scale("energy_mwh", policy)
        value = Decimal(int(row["energy_mwh"])).scaleb(-scale)
        dirty = conn.execute("SELECT energy_mwh, quality_flag, conflict_flag FROM d04_load_hourly "
                             "WHERE user_id='USER-4201-000129-1' AND slot_no=1").fetchone()
        dirty_value = Decimal(int(dirty["energy_mwh"])).scaleb(-scale)
        return (value == Decimal("0.100") and dirty_value == Decimal("0.150")
                and dirty["conflict_flag"] == 1 and dirty["quality_flag"] == "ESTIMATED",
                "15 分钟源 100 kWh -> %s MWh；未登记换算率行 -> %s MWh（quality=%s, conflict=%s）"
                % (value, dirty_value, dirty["quality_flag"], dirty["conflict_flag"]))
    check("E6", "单位换算：kWh->MWh 且未登记换算率记 Q05", e6)

    def e7():
        v1 = query_as_of(conn, policy, contract, "D05", {"contract_id": "CONTRACT-4201-000001-5"}, "2026-01-15")
        v2 = query_as_of(conn, policy, contract, "D05", {"contract_id": "CONTRACT-4201-000001-5"}, "2026-03-01")
        ok = len(v1) == 1 and len(v2) == 1 and v1[0]["version"] == 1 and v2[0]["version"] == 2
        return (ok, "as-of 2026-01-15 -> v%s；as-of 2026-03-01 -> v%s"
                % (v1[0]["version"] if v1 else None, v2[0]["version"] if v2 else None))
    check("E7", "版本替代：同合同两版本按 as-of 命中不同版本", e7)

    def e8():
        before = table_count("D09")
        qrow = conn.execute("SELECT qid, dataset FROM quarantine_row WHERE rule_id='Q01' LIMIT 1").fetchone()
        result = resolve_quarantine(conn, contract, policy, spec, qrow["qid"], "ACCEPT", "trader-01",
                                    overrides={"energy_mwh": "12.500"}, note="客户确认后回补")
        after = table_count("D09")
        recorded = conn.execute("SELECT * FROM quarantine_resolution WHERE qid=?", (qrow["qid"],)).fetchone()
        return (after == before + 1 and recorded["decision"] == "ACCEPT"
                and recorded["new_batch_id"] == result["new_batch_id"],
                "隔离行 %s 回补：批次 %s -> %s，主表 %s -> %s"
                % (qrow["qid"], result["new_batch_id"], recorded["new_batch_id"], before, after))
    check("E8", "人工确认后回补：新批次入库并留痕", e8)

    def e9():
        good_rep = imp("SRC-INT-LOAD-TEMPLATE")
        bad_rep = imp("SRC-INT-LOAD-TEMPLATE-BAD")
        bad_rules = {q["rule_id"] for q in bad_rep.quality_issues}
        good_ok = good_rep.status in ("COMMITTED", "DUPLICATE") and table_count("D04") > 0
        return (good_ok and bad_rep.status == "FAILED" and "I01" in bad_rules,
                "好模板 %s（D04 共 %s 行）；坏模板 %s（%s）"
                % (good_rep.status, table_count("D04"), bad_rep.status, sorted(bad_rules)))
    check("E9", "标准模板指纹不符命中 I01", e9)

    def e10():
        rep = imp("SRC-INT-RULE-PDF")
        doc = conn.execute("SELECT doc_no, clause_count, src_file_hash FROM d01_rule_doc LIMIT 1").fetchone()
        ok = doc is not None and doc["doc_no"].startswith("鄂电交易") and (doc["clause_count"] or 0) > 0
        return (ok and rep.status in ("COMMITTED", "DUPLICATE"),
                "PDF 规则文件入库：%s %s（条款 %s）"
                % (rep.status, doc["doc_no"] if doc else None, doc["clause_count"] if doc else None))
    check("E10", "PDF 规则文件入库（文本层）", e10)

    def e11():
        rep = imp("SRC-EXT-WEATHER-JSON")
        broken = imp("SRC-TEST-W09-BROKEN-JSON")
        rules = {q["rule_id"] for q in broken.quality_issues}
        weather_rows = table_count("D11")
        other = table_count("D02")
        return (rep.status in ("COMMITTED", "DUPLICATE") and weather_rows > 0
                and broken.status == "FAILED" and "W09" in rules and other > 0,
                "正常通道 %s（%s 行）；断流 %s（%s），不影响其它数据集（D02=%s 行）"
                % (rep.status, weather_rows, broken.status, sorted(rules), other))
    check("E11", "外部特征通道失败降级 W09 且不牵连其它数据集", e11)

    def e12():
        rep = imp("SRC-INT-TRADE-CSV")
        missing = [k for k in REPORT_FIELDS if k not in rep.as_dict()]
        actual = sha256_file(ROOT / "samples/d08_trade_result.csv")
        evidence_ok = rep.evidence_path is not None and Path(rep.evidence_path).exists()
        return (not missing and rep.file_hash == actual and evidence_ok,
                "报告字段 %d/%d 齐全；file_hash 与文件一致=%s；证据文件=%s"
                % (len(REPORT_FIELDS) - len(missing), len(REPORT_FIELDS), rep.file_hash == actual, evidence_ok))
    check("E12", "ImportReport 14 字段齐全且证据可查", e12)

    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="数据导入器参考实现")
    parser.add_argument("--verify", action="store_true", help="运行 E1-E12 自检")
    parser.add_argument("--source", help="导入单个来源（source_id）")
    args = parser.parse_args(argv)
    spec = load_spec()
    contract = load_contract()
    policy = load_policy()
    if args.verify:
        results = run_checks()
        failed = [r for r in results if not r["ok"]]
        print("")
        print("自检结果：%d/%d PASS" % (len(results) - len(failed), len(results)))
        return 1 if failed else 0
    if args.source:
        conn = store.connect(":memory:")
        store.build_schema(conn, contract, policy)
        ensure_aux(conn)
        report = import_source(conn, contract, policy, spec, args.source, ROOT,
                               ROOT / spec.get("evidence_dir", "_evidence"))
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return 0
    print("用法：python -X utf8 reference_importer.py --verify [--source <source_id>]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
