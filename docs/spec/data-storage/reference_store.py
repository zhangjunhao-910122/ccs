#!/usr/bin/env python3
"""业务数据存储层参考实现（#25）。

用 SQLite 建立 D01-D16 十六类数据对象的物理表，演示：
  - 契约字段到物理列的映射（decimal 存整数最小单位）
  - 批次幂等、整批隔离、当前版本唯一
  - 血缘边、as-of 查询、按 #26 规则重建日/月派生表
  - 列级敏感策略与导出闸门
运行：
  python reference_store.py --verify
  python reference_store.py --write-object-map object-map.csv
"""
import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONTRACT_PATH = ROOT.parent / "data-contract" / "contract.schema.json"
SAMPLES_PATH = ROOT.parent / "data-contract" / "samples.jsonl"
POLICY_PATH = ROOT / "storage.schema.json"

IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class StoreError(Exception):
    def __init__(self, rule, message):
        super().__init__(message)
        self.rule = rule


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_samples():
    rows = []
    with open(SAMPLES_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def connect(path=":memory:"):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def q(name):
    if not IDENT.match(name):
        raise StoreError("IDENT", "非法标识符: %s" % name)
    return '"%s"' % name


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


# ---------------------------------------------------------------- 类型与缩放

def decimal_scale(field_name, policy):
    scales = policy["decimal_storage"]["scales"]
    name = field_name.lower()
    for key in ("energy", "volume", "price", "amount", "ratio", "temperature", "humidity"):
        if key in name:
            return int(scales[key])
    return int(scales["default"])


def sql_type(field, policy):
    t = field["type"]
    if t in ("string", "enum", "date", "datetime"):
        return "TEXT"
    if t == "int":
        return "INTEGER"
    if t == "bool":
        return "INTEGER"
    if t == "decimal":
        return "INTEGER"
    if t in ("json", "array"):
        return "TEXT"
    raise StoreError("TYPE", "未知字段类型 %s（%s）" % (t, field.get("name")))


def to_physical(field, value, policy):
    if value is None:
        return None
    t = field["type"]
    if t == "decimal":
        try:
            d = Decimal(str(value))
        except InvalidOperation:
            raise StoreError("DECIMAL", "%s 不是合法小数: %r" % (field["name"], value))
        scale = decimal_scale(field["name"], policy)
        exp = -d.as_tuple().exponent
        if exp > scale:
            raise StoreError("DECIMAL_SCALE", "%s=%s 超过 %d 位小数" % (field["name"], value, scale))
        return int(d.scaleb(scale))
    if t == "bool":
        return 1 if bool(value) else 0
    if t == "int":
        if isinstance(value, bool):
            raise StoreError("INT", "%s 不能是布尔值" % field["name"])
        try:
            return int(value)
        except (TypeError, ValueError):
            raise StoreError("INT", "%s 不是整数: %r" % (field["name"], value))
    if t in ("json", "array"):
        return value if isinstance(value, str) else canonical(value)
    return str(value)


def from_physical(field, value, policy):
    if value is None:
        return None
    if field["type"] == "decimal":
        scale = decimal_scale(field["name"], policy)
        return Decimal(int(value)).scaleb(-scale)
    return value


# ---------------------------------------------------------------- 表结构

COMMON_FIELDS = [
    ("batch_id", "string"), ("source_system", "string"), ("source_file", "string"),
    ("source_row_no", "int"), ("file_hash", "string"), ("imported_at", "datetime"),
    ("importer_version", "string"), ("rule_version", "string"), ("valid_from", "date"),
    ("valid_to", "date"), ("is_current", "bool"), ("owner", "string"),
]


def business_fields(dataset):
    return dataset["fields"]


def physical_name(common_name, policy):
    return policy["common_column_prefix"] + common_name


def current_key_of(dataset, row):
    keys = []
    for name in dataset["business_key"]:
        if name == "version":
            continue
        keys.append([name, row.get(name)])
    return canonical(keys)


def record_id_of(dataset_key, dataset, row):
    parts = [dataset_key] + [row.get(k) for k in dataset["business_key"]] + [row.get("valid_from")]
    return sha256_text("|".join("" if p is None else str(p) for p in parts))


AUX_DDL = {
    "d01_rule_clause": """
CREATE TABLE IF NOT EXISTS d01_rule_clause (
  clause_uid TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL,
  chapter_path TEXT,
  clause_no TEXT,
  clause_key TEXT,
  text_raw TEXT,
  text_hash TEXT,
  text_status TEXT,
  topic TEXT,
  page_ref TEXT,
  src_batch_id TEXT,
  src_is_current INTEGER NOT NULL DEFAULT 1
)""",
}


def build_schema(conn, contract, policy):
    ddl = []
    for dkey, ds in contract["datasets"].items():
        table = policy["tables"][dkey]["table"]
        cols = ["record_id TEXT PRIMARY KEY", "dataset TEXT NOT NULL"]
        for f in ds["fields"]:
            null = " NOT NULL" if f.get("required", True) is not False else ""
            cols.append("%s %s%s" % (q(f["name"]), sql_type(f, policy), null))
        for name, ctype in COMMON_FIELDS:
            null = " NOT NULL" if name in ("batch_id", "valid_from", "is_current") else ""
            cols.append("%s %s%s" % (q(physical_name(name, policy)), "TEXT" if ctype in ("string", "date", "datetime") else "INTEGER", null))
        cols.append("current_key TEXT NOT NULL")
        cols.append("conflict_flag INTEGER NOT NULL DEFAULT 0")
        ddl.append("CREATE TABLE IF NOT EXISTS %s (\n  %s\n)" % (q(table), ",\n  ".join(cols)))
        ddl.append("CREATE UNIQUE INDEX IF NOT EXISTS %s ON %s (dataset, current_key) WHERE %s = 1" % (
            q("ux_%s_current" % table), q(table), q(physical_name("is_current", policy))))
        bk_cols = ", ".join(q(c) for c in ds["business_key"])
        ddl.append("CREATE INDEX IF NOT EXISTS %s ON %s (%s)" % (q("ix_%s_bk" % table), q(table), bk_cols))
        for i, idx in enumerate(policy["tables"][dkey].get("extra_indexes", [])):
            ddl.append("CREATE INDEX IF NOT EXISTS %s ON %s (%s)" % (
                q("ix_%s_%d" % (table, i)), q(table), ", ".join(q(c) for c in idx)))
        ddl.append("CREATE INDEX IF NOT EXISTS %s ON %s (dataset, %s)" % (
            q("ix_%s_valid" % table), q(table), q(physical_name("valid_from", policy))))
        quality_col = next((f["name"] for f in ds["fields"] if f["name"] == "quality_flag"), None)
        where = "src_is_current = 1"
        if quality_col:
            where += " AND %s IN ('OK', 'ESTIMATED')" % q(quality_col)
        ddl.append("CREATE VIEW IF NOT EXISTS %s AS SELECT * FROM %s WHERE %s" % (
            q("v_%s_valid" % table), q(table), where))
        if ds["sensitivity"] in ("PUBLIC", "SENSITIVE"):
            ddl.append("CREATE VIEW IF NOT EXISTS %s AS SELECT * FROM %s" % (
                q("v_%s_masked" % table), q("v_%s_valid" % table)))

    ddl.append("""CREATE TABLE IF NOT EXISTS ingest_batch (
  batch_id TEXT PRIMARY KEY,
  dataset TEXT NOT NULL,
  source_file TEXT,
  file_hash TEXT NOT NULL,
  importer_version TEXT,
  row_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  error_summary TEXT,
  imported_at TEXT NOT NULL,
  UNIQUE (dataset, file_hash)
)""")
    ddl.append("""CREATE TABLE IF NOT EXISTS quarantine_row (
  qid INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id TEXT NOT NULL,
  dataset TEXT NOT NULL,
  source_row_no INTEGER,
  rule_id TEXT NOT NULL,
  message TEXT,
  raw_json TEXT NOT NULL,
  created_at TEXT NOT NULL
)""")
    ddl.append("""CREATE TABLE IF NOT EXISTS lineage_edge (
  edge_id TEXT PRIMARY KEY,
  from_ref TEXT NOT NULL,
  to_ref TEXT NOT NULL,
  transform_id TEXT NOT NULL,
  run_id TEXT,
  created_at TEXT NOT NULL
)""")
    ddl.append("""CREATE TABLE IF NOT EXISTS column_policy (
  table_name TEXT NOT NULL,
  column_name TEXT NOT NULL,
  sensitivity TEXT NOT NULL,
  mask_rule TEXT NOT NULL,
  PRIMARY KEY (table_name, column_name)
)""")
    ddl.append("""CREATE TABLE IF NOT EXISTS schema_version (
  version TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL,
  note TEXT
)""")
    for stmt in ddl:
        conn.execute(stmt)

    for aux in ("d01_rule_clause",):
        conn.execute(AUX_DDL[aux])

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    for dkey, ds in contract["datasets"].items():
        table = policy["tables"][dkey]["table"]
        mask = "NO_ROW_LEVEL" if ds["sensitivity"] == "HIGHLY_SENSITIVE" else ("MASK_IDENTIFIERS" if ds["sensitivity"] == "SENSITIVE" else "NONE")
        cols = [dict(record_id="TEXT", dataset="TEXT")]
        names = ["record_id", "dataset"]
        names += [f["name"] for f in ds["fields"]]
        names += [physical_name(n, policy) for n, _ in COMMON_FIELDS]
        names += ["current_key", "conflict_flag"]
        for name in names:
            conn.execute("INSERT OR REPLACE INTO column_policy (table_name, column_name, sensitivity, mask_rule) VALUES (?, ?, ?, ?)",
                         (table, name, ds["sensitivity"], mask))
    conn.execute("INSERT OR REPLACE INTO schema_version (version, applied_at, note) VALUES (?, ?, ?)",
                 ("store-1.0.0", now, "D01-D16 物理表 + 批次/隔离/血缘/列策略"))
    conn.commit()
    return ddl


# ---------------------------------------------------------------- 校验

def enum_values(contract, field):
    if "enum" in field:
        return field["enum"]
    ref = field.get("enum_ref")
    if ref:
        return contract["enums"][ref]
    return None


def validate_row(contract, dkey, row, policy):
    ds = contract["datasets"][dkey]
    errors = []
    for f in ds["fields"]:
        name = f["name"]
        value = row.get(name)
        if value is None or value == "":
            if f.get("required", True) is not False:
                errors.append(("REQUIRED:%s" % name, "缺少必填字段 %s" % name))
            continue
        t = f["type"]
        if t == "enum":
            allowed = enum_values(contract, f)
            if value not in allowed:
                errors.append(("ENUM:%s" % name, "%s=%r 不在允许值内" % (name, value)))
            continue
        if t == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                errors.append(("INT:%s" % name, "%s 不是整数" % name))
            continue
        if t == "bool":
            if not isinstance(value, bool):
                errors.append(("BOOL:%s" % name, "%s 不是布尔值" % name))
            continue
        if t == "date":
            if not isinstance(value, str) or not DATE_RE.match(value):
                errors.append(("DATE:%s" % name, "%s 不是 YYYY-MM-DD" % name))
            continue
        if t == "datetime":
            if not isinstance(value, str):
                errors.append(("DATETIME:%s" % name, "%s 不是字符串" % name))
            else:
                try:
                    datetime.fromisoformat(value)
                except ValueError:
                    errors.append(("DATETIME:%s" % name, "%s 不是 ISO 时刻" % name))
            continue
        if t == "decimal":
            try:
                d = Decimal(str(value))
                exp = -d.as_tuple().exponent
                if exp > decimal_scale(name, policy):
                    errors.append(("DECIMAL_SCALE:%s" % name, "%s 小数位超限" % name))
            except InvalidOperation:
                errors.append(("DECIMAL:%s" % name, "%s 不是小数" % name))

    slot = row.get("slot_no")
    if slot is not None and (not isinstance(slot, int) or slot < 1 or slot > 24):
        errors.append(("SLOT_RANGE", "slot_no 必须是 1..24"))

    if dkey in ("D04", "D07", "D09") and row.get("business_date"):
        slot = row.get("slot_no")
        start = row.get("interval_start")
        if slot is None or start is None:
            errors.append(("SLOT_INTERVAL", "小时数据必须同时给 slot_no 与 interval_start"))
        else:
            expected = datetime.strptime(row["business_date"], "%Y-%m-%d") + timedelta(hours=slot - 1)
            actual_text = str(start)[:19]
            try:
                actual = datetime.fromisoformat(actual_text)
            except ValueError:
                actual = None
            if actual != expected:
                errors.append(("SLOT_INTERVAL", "interval_start 与 business_date+slot 不一致：期望 %s" % expected.isoformat()))
    return errors


def _mark_blocked(conn, dataset, batch_id, source_file, file_hash, importer_version, rows, errors, now):
    conn.execute("INSERT OR REPLACE INTO ingest_batch (batch_id, dataset, source_file, file_hash, importer_version, row_count, status, error_summary, imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (batch_id, dataset, source_file, file_hash, importer_version, len(rows), "BLOCKED", "; ".join(sorted({e[0] for e in errors}))[:1000], now))
    for row in rows:
        row_errors = errors_by_row(rows, row, errors)
        rule = row_errors[0][0] if row_errors else "BATCH_BLOCKED"
        msg = "; ".join(m for _, m in row_errors) if row_errors else "同批次其他行存在阻断错误"
        conn.execute("INSERT INTO quarantine_row (batch_id, dataset, source_row_no, rule_id, message, raw_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (batch_id, dataset, row.get("source_row_no"), rule, msg, canonical(row), now))
    conn.commit()


def errors_by_row(rows, row, errors):
    """本实现按行独立校验；批量错误在 import_batch 里已按行汇总，这里只做兜底。"""
    return [e for e in errors if e[1] and row.get("source_row_no") is not None and str(row.get("source_row_no")) in e[1]] or list(errors)


def import_batch(conn, contract, policy, dkey, rows, source_file, file_hash, batch_id=None, importer_version="1.0.0"):
    ds = contract["datasets"][dkey]
    table = policy["tables"][dkey]["table"]
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    if batch_id is None:
        batch_id = "IMP-%s-%04d" % (datetime.now().strftime("%Y%m%d"), conn.execute("SELECT COUNT(*) FROM ingest_batch").fetchone()[0] + 1)

    exists = conn.execute("SELECT batch_id FROM ingest_batch WHERE dataset=? AND file_hash=?", (dkey, file_hash)).fetchone()
    if exists:
        return {"status": "DUPLICATE", "batch_id": exists["batch_id"], "rows": 0}

    row_errors = []
    for row in rows:
        errs = validate_row(contract, dkey, row, policy)
        row_errors.append((row, errs))
    flat = [e for _, errs in row_errors for e in errs]

    prepared = []
    seen_keys = set()
    for row, errs in row_errors:
        if errs:
            continue
        ckey = current_key_of(ds, row)
        rid = record_id_of(dkey, ds, row)
        if (ckey, rid) in seen_keys:
            flat.append(("DUP_IN_BATCH", "同一批次内业务键重复: %s" % ckey))
            continue
        seen_keys.add((ckey, rid))
        if row.get("is_current"):
            hit = conn.execute("SELECT record_id FROM %s WHERE current_key=? AND %s=1" % (q(table), q(physical_name("is_current", policy))), (ckey,)).fetchone()
            if hit and hit["record_id"] != rid:
                flat.append(("CURRENT_CONFLICT", "业务键已有当前版本: %s" % ckey))
                continue
        prepared.append((row, ckey, rid))

    if flat:
        _mark_blocked(conn, dkey, batch_id, source_file, file_hash, importer_version, rows, flat, now)
        return {"status": "BLOCKED", "batch_id": batch_id, "rows": 0, "errors": flat}

    conn.execute("INSERT INTO ingest_batch (batch_id, dataset, source_file, file_hash, importer_version, row_count, status, error_summary, imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (batch_id, dkey, source_file, file_hash, importer_version, len(prepared), "LOADED", None, now))
    inserted = 0
    for row, ckey, rid in prepared:
        values = [rid, dkey]
        names = ["record_id", "dataset"]
        for f in ds["fields"]:
            names.append(q(f["name"]))
            values.append(to_physical(f, row.get(f["name"]), policy))
        for name, _ctype in COMMON_FIELDS:
            pname = physical_name(name, policy)
            if name == "batch_id":
                value = batch_id
            elif name == "source_file":
                value = source_file
            elif name == "file_hash":
                value = file_hash
            elif name == "importer_version":
                value = importer_version
            elif name == "imported_at":
                value = now
            elif name == "is_current":
                value = 1 if row.get("is_current") else 0
            else:
                value = row.get(name)
            names.append(q(pname))
            values.append(value)
        names += [q("current_key"), q("conflict_flag")]
        values += [ckey, 0]
        sql = "INSERT INTO %s (%s) VALUES (%s)" % (q(table), ", ".join(names), ", ".join("?" for _ in values))
        try:
            conn.execute(sql, values)
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            flat = [("INSERT_CONFLICT", str(exc))]
            _mark_blocked(conn, dkey, batch_id, source_file, file_hash, importer_version, rows, flat, now)
            return {"status": "BLOCKED", "batch_id": batch_id, "rows": 0, "errors": flat}
        inserted += 1
        for from_ref, transform in (("file:%s#%s" % (file_hash, row.get("source_row_no")), "IMPORT"),
                                    ("batch:%s" % batch_id, "ASSIGN_BATCH")):
            edge = sha256_text("%s|%s|%s" % (from_ref, rid, transform))
            conn.execute("INSERT OR IGNORE INTO lineage_edge (edge_id, from_ref, to_ref, transform_id, run_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                         (edge, from_ref, "row:%s" % rid, transform, batch_id, now))
    conn.commit()
    return {"status": "LOADED", "batch_id": batch_id, "rows": inserted}


# ---------------------------------------------------------------- 查询与派生

def query_as_of(conn, policy, dkey, keys, as_of):
    table = policy["tables"][dkey]["table"]
    where = " AND ".join("%s = ?" % q(k) for k in keys)
    sql = "SELECT * FROM %s WHERE %s AND src_valid_from <= ? AND (src_valid_to IS NULL OR src_valid_to > ?) ORDER BY src_valid_from DESC LIMIT 1" % (q(table), where)
    return conn.execute(sql, list(keys.values()) + [as_of, as_of]).fetchone()


def trace_lineage(conn, ref, max_depth=4):
    edges = []
    frontier = [ref]
    seen = {ref}
    for _ in range(max_depth):
        if not frontier:
            break
        marks = ",".join("?" for _ in frontier)
        rows = conn.execute("SELECT * FROM lineage_edge WHERE to_ref IN (%s)" % marks, frontier).fetchall()
        nxt = []
        for row in rows:
            edges.append(dict(row))
            if row["from_ref"] not in seen:
                seen.add(row["from_ref"])
                nxt.append(row["from_ref"])
        frontier = nxt
    return edges


QUALITY_RANK = {"OK": 1, "ESTIMATED": 2, "PARTIAL": 3, "SUSPECT": 4, "MISSING": 5}
RANK_QUALITY = {v: k for k, v in QUALITY_RANK.items()}


def rebuild_daily(conn, run_id=None):
    run_id = run_id or "RB-" + datetime.now().strftime("%Y%m%d%H%M%S")
    conn.execute("""CREATE TABLE IF NOT EXISTS d04_load_daily (
      user_id TEXT NOT NULL, business_date TEXT NOT NULL,
      energy_mwh INTEGER, quality_flag TEXT NOT NULL,
      rebuild_run_id TEXT NOT NULL,
      PRIMARY KEY (user_id, business_date, rebuild_run_id))""")
    conn.execute("DELETE FROM d04_load_daily WHERE rebuild_run_id=?", (run_id,))
    rows = conn.execute("""SELECT user_id, business_date, SUM(energy_mwh) AS energy, quality_flag
                           FROM d04_load_hourly
                           WHERE src_is_current=1 AND energy_mwh IS NOT NULL AND quality_flag IN ('OK','ESTIMATED')
                           GROUP BY user_id, business_date""").fetchall()
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    for row in rows:
        conn.execute("INSERT INTO d04_load_daily (user_id, business_date, energy_mwh, quality_flag, rebuild_run_id) VALUES (?, ?, ?, ?, ?)",
                     (row["user_id"], row["business_date"], row["energy"], row["quality_flag"], run_id))
        target = "agg:d04_load_daily:%s:%s:%s" % (row["user_id"], row["business_date"], run_id)
        for hour in conn.execute("SELECT record_id FROM d04_load_hourly WHERE user_id=? AND business_date=? AND src_is_current=1",
                                 (row["user_id"], row["business_date"])).fetchall():
            edge = sha256_text("%s|%s|ROLLUP_HOUR_DAY" % (hour["record_id"], target))
            conn.execute("INSERT OR IGNORE INTO lineage_edge (edge_id, from_ref, to_ref, transform_id, run_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                         (edge, "row:%s" % hour["record_id"], target, "ROLLUP_HOUR_DAY", run_id, now))
    conn.commit()
    return run_id


def rebuild_monthly(conn, run_id):
    conn.execute("""CREATE TABLE IF NOT EXISTS d04_load_monthly (
      user_id TEXT NOT NULL, settle_month TEXT NOT NULL,
      energy_mwh INTEGER, rebuild_run_id TEXT NOT NULL,
      PRIMARY KEY (user_id, settle_month, rebuild_run_id))""")
    conn.execute("DELETE FROM d04_load_monthly WHERE rebuild_run_id=?", (run_id,))
    rows = conn.execute("""SELECT user_id, substr(business_date, 1, 7) AS m, SUM(energy_mwh) AS energy
                           FROM d04_load_hourly
                           WHERE src_is_current=1 AND energy_mwh IS NOT NULL AND quality_flag IN ('OK','ESTIMATED')
                           GROUP BY user_id, m""").fetchall()
    for row in rows:
        conn.execute("INSERT INTO d04_load_monthly (user_id, settle_month, energy_mwh, rebuild_run_id) VALUES (?, ?, ?, ?)",
                     (row["user_id"], row["m"], row["energy"], run_id))
    conn.commit()
    return run_id


def export_rows(conn, policy, table, limit=10):
    sens = conn.execute("SELECT DISTINCT sensitivity FROM column_policy WHERE table_name=?", (table,)).fetchone()
    if sens and sens["sensitivity"] == "HIGHLY_SENSITIVE":
        raise StoreError("HIGHLY_SENSITIVE", "高度敏感表 %s 不允许行级导出；请改用 k>=5 聚合视图" % table)
    return conn.execute("SELECT * FROM %s LIMIT ?" % q(table), (limit,)).fetchall()


# ---------------------------------------------------------------- 对象映射表

def object_map(contract, policy):
    rows = []
    for dkey, ds in contract["datasets"].items():
        table = policy["tables"][dkey]["table"]
        for f in ds["fields"]:
            rows.append([dkey, ds["title"], f["name"], table, f["name"], sql_type(f, policy),
                         decimal_scale(f["name"], policy) if f["type"] == "decimal" else "",
                         ds["sensitivity"], "否" if f.get("required", True) is False else "是", f.get("desc", "")])
        for name, ctype in COMMON_FIELDS:
            rows.append([dkey, ds["title"], name, table, physical_name(name, policy),
                         "TEXT" if ctype in ("string", "date", "datetime") else "INTEGER",
                         "", ds["sensitivity"], "公共列", "契约公共列，物理加 src_ 前缀"])
    return rows


def write_object_map(path, contract, policy):
    header = ["dataset", "title", "contract_field", "physical_table", "physical_column", "sql_type", "scale", "sensitivity", "required", "desc"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(object_map(contract, policy))


# ---------------------------------------------------------------- 自检

def seeds(conn, contract, policy):
    """导入 16 行契约样例。"""
    samples = load_samples()
    by_ds = {}
    for row in samples:
        by_ds.setdefault(row["_dataset"], []).append(row)
    for dkey, rows in by_ds.items():
        first = rows[0]
        import_batch(conn, contract, policy, dkey, rows, first["source_file"], first["file_hash"],
                     batch_id=first["batch_id"], importer_version=first.get("importer_version", "1.0.0"))
    return len(samples)


def run_checks():
    contract = load_json(CONTRACT_PATH)
    policy = load_json(POLICY_PATH)
    conn = connect()
    build_schema(conn, contract, policy)
    results = []

    def check(code, name, fn):
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
        results.append((code, name, ok, detail))

    imported = seeds(conn, contract, policy)

    def e1():
        missing = []
        mapped = 0
        for dkey, ds in contract["datasets"].items():
            table = policy["tables"][dkey]["table"]
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % q(table))}
            for f in ds["fields"]:
                mapped += 1
                if f["name"] not in cols:
                    missing.append("%s.%s" % (dkey, f["name"]))
            if policy["tables"][dkey].get("aux"):
                for aux in policy["tables"][dkey]["aux"]:
                    if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (aux,)).fetchone():
                        missing.append("aux:%s" % aux)
        policy_cols = conn.execute("SELECT COUNT(*) FROM column_policy").fetchone()[0]
        return (not missing and mapped == 143 and imported == 16 and policy_cols > 0,
                "dataset=%d 字段映射=%d 样例=%d 列策略=%d 缺失=%s" % (len(contract["datasets"]), mapped, imported, policy_cols, missing or "无"))

    def e2():
        before = conn.execute("SELECT COUNT(*) FROM d02_user").fetchone()[0]
        row = next(r for r in load_samples() if r["_dataset"] == "D02")
        row = dict(row); row["user_id"] = "USER-4201-999999-9"; row["source_row_no"] = 9001
        file_hash = sha256_text("idem-test")
        one = import_batch(conn, contract, policy, "D02", [row], "幂等测试.xlsx", file_hash)
        two = import_batch(conn, contract, policy, "D02", [row], "幂等测试.xlsx", file_hash)
        after = conn.execute("SELECT COUNT(*) FROM d02_user").fetchone()[0]
        ok = one["status"] == "LOADED" and two["status"] == "DUPLICATE" and after == before + 1
        return ok, "首次=%s 重复=%s 行数 %d→%d" % (one["status"], two["status"], before, after)

    def e3():
        before = conn.execute("SELECT COUNT(*) FROM d04_load_hourly").fetchone()[0]
        bad = dict(next(r for r in load_samples() if r["_dataset"] == "D04"))
        bad["slot_no"] = 25
        bad["source_row_no"] = 777
        res = import_batch(conn, contract, policy, "D04", [bad], "坏数据.csv", sha256_text("bad-batch"))
        after = conn.execute("SELECT COUNT(*) FROM d04_load_hourly").fetchone()[0]
        qn = conn.execute("SELECT COUNT(*) FROM quarantine_row WHERE batch_id=? AND rule_id LIKE 'SLOT%'", (res["batch_id"],)).fetchone()[0]
        raw = conn.execute("SELECT raw_json FROM quarantine_row WHERE batch_id=? LIMIT 1", (res["batch_id"],)).fetchone()
        ok = res["status"] == "BLOCKED" and before == after and qn == 1 and raw and "slot_no" in raw["raw_json"]
        return ok, "批次=%s 主表行数 %d→%d 隔离=%d" % (res["status"], before, after, qn)

    def e4():
        sample = next(r for r in load_samples() if r["_dataset"] == "D04")
        rid = record_id_of("D04", contract["datasets"]["D04"], sample)
        edges = trace_lineage(conn, "row:%s" % rid)
        refs = [e["from_ref"] for e in edges]
        has_file = any(r.startswith("file:") for r in refs)
        has_batch = any(r.startswith("batch:") for r in refs)
        daily = conn.execute("SELECT * FROM d04_load_daily LIMIT 1").fetchone()
        daily_ref = "agg:d04_load_daily:%s:%s:%s" % (daily["user_id"], daily["business_date"], daily["rebuild_run_id"]) if daily else ""
        child = trace_lineage(conn, daily_ref) if daily_ref else []
        return (has_file and has_batch and child, "行血缘 %d 条(文件=%s 批次=%s)，日汇总上行 %d 条" % (len(edges), has_file, has_batch, len(child)))

    def e5():
        sample = dict(next(r for r in load_samples() if r["_dataset"] == "D04"))
        sample["source_row_no"] = 888
        bad = dict(sample); bad["interval_start"] = "2026-11-04T09:00:00+08:00"
        res = import_batch(conn, contract, policy, "D04", [bad], "时标错误.csv", sha256_text("time-bad"))
        dup = dict(sample)
        res2 = import_batch(conn, contract, policy, "D04", [dup], "重复业务键.csv", sha256_text("dup-key"))
        ok = res["status"] == "BLOCKED" and any(e[0] == "SLOT_INTERVAL" for e in res.get("errors", [])) and res2["status"] == "BLOCKED"
        return ok, "时标错误=%s 当前版本冲突=%s" % (res["status"], res2["status"])

    def e6():
        missing = conn.execute("""SELECT COUNT(*) FROM pragma_table_info('d04_load_hourly')""").fetchone()[0]
        policy_count = conn.execute("SELECT COUNT(*) FROM column_policy WHERE table_name='d04_load_hourly'").fetchone()[0]
        try:
            export_rows(conn, policy, "d04_load_hourly")
            blocked = False
        except StoreError:
            blocked = True
        public = export_rows(conn, policy, "d11_weather")
        return (blocked and missing == policy_count and len(public) >= 1,
                "D04 列=%d 策略=%d 导出拦截=%s 公开表导出=%d" % (missing, policy_count, blocked, len(public)))

    def e7():
        v1 = {"version": 1, "price": 400, "valid_from": "2025-01-01", "is_current": False, "period_start": "2025-01-01", "period_end": "2025-12-31"}
        v2 = {"version": 2, "price": 430, "valid_from": "2026-01-01", "is_current": True, "period_start": "2026-01-01", "period_end": "2026-12-31"}
        base = dict(next(r for r in load_samples() if r["_dataset"] == "D05"))
        base.update({"contract_id": "CONTRACT-4201-000999-9", "source_row_no": 501})
        for i, patch in enumerate((v1, v2)):
            row = dict(base); row.update(patch); row["source_row_no"] = 501 + i
            import_batch(conn, contract, policy, "D05", [row], "合同版本.xlsx", sha256_text("ver-%d" % i))
        old = query_as_of(conn, policy, "D05", {"contract_id": "CONTRACT-4201-000999-9"}, "2025-06-01")
        new = query_as_of(conn, policy, "D05", {"contract_id": "CONTRACT-4201-000999-9"}, "2026-06-01")
        ok = old and new and old["version"] == 1 and new["version"] == 2
        return ok, "2025-06→v%s，2026-06→v%s" % (old["version"] if old else None, new["version"] if new else None)

    def e8():
        hourly = conn.execute("SELECT SUM(energy_mwh) FROM d04_load_hourly WHERE src_is_current=1 AND quality_flag IN ('OK','ESTIMATED')").fetchone()[0]
        daily = conn.execute("SELECT SUM(energy_mwh) FROM d04_load_daily").fetchone()[0]
        monthly = conn.execute("SELECT SUM(energy_mwh) FROM d04_load_monthly").fetchone()[0]
        conn.execute("DELETE FROM d04_load_daily")
        rebuild_daily(conn, "RB-CHECK")
        daily2 = conn.execute("SELECT SUM(energy_mwh) FROM d04_load_daily").fetchone()[0]
        return (hourly == daily == monthly == daily2, "小时=%s 日=%s 月=%s 重建后=%s" % (hourly, daily, monthly, daily2))

    # 先建一次日/月派生，供 E4/E8 使用
    rebuild_daily(conn, "RB-SEED")
    rebuild_monthly(conn, "RB-SEED")

    check("E1", "16 类对象与 143 个业务字段全覆盖", e1)
    check("E2", "批次幂等", e2)
    check("E3", "脏数据整批隔离且可查", e3)
    check("E4", "血缘可追到文件与派生", e4)
    check("E5", "时标一致与当前版本唯一", e5)
    check("E6", "列级敏感策略与导出闸门", e6)
    check("E7", "as-of 版本查询", e7)
    check("E8", "日/月派生可重建且与小时一致", e8)

    lines = []
    for code, name, ok, detail in results:
        lines.append("%s %s %s：%s" % (code, "PASS" if ok else "FAIL", name, detail))
    passed = sum(1 for _, _, ok, _ in results if ok)
    lines.append("SUMMARY %d/%d PASS" % (passed, len(results)))
    return results, lines, conn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="运行 E1-E8 并写 verification.txt")
    parser.add_argument("--write-object-map", metavar="PATH", help="导出契约字段到物理列映射 CSV")
    args = parser.parse_args()
    contract = load_json(CONTRACT_PATH)
    policy = load_json(POLICY_PATH)
    if args.write_object_map:
        write_object_map(args.write_object_map, contract, policy)
        print("object map written:", args.write_object_map)
        return 0
    if args.verify:
        results, lines, _conn = run_checks()
        text = "\n".join(lines) + "\n"
        print(text, end="")
        (ROOT / "verification.txt").write_text(text, encoding="utf-8")
        return 0 if all(ok for _, _, ok, _ in results) else 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
