#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据源注册表与导入器接口 · 参考实现 v1.0（#44 / map #22）

用法：
    python -X utf8 docs/spec/data-source-registry/reference_registry.py --verify

只依赖标准库。自检 E1–E8 覆盖票面四条验收：
E1 注册表结构        E2 阶段矩阵
E3 只读闸            E4 凭据与企业侧配置
E5 幂等              E6 换源不改下游
E7 失败整批隔离与重试上限
E8 字段映射覆盖与 TBD 不映射
"""
from __future__ import annotations

import copy
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Protocol

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = ROOT / "docs" / "spec" / "data-contract" / "contract.schema.json"
SCHEMA_PATH = ROOT / "docs" / "spec" / "data-source-registry" / "registry.schema.json"
SAMPLES_PATH = ROOT / "docs" / "spec" / "data-source-registry" / "samples.registry.json"
NOW = "2026-11-05T21:30:00+08:00"

STAGE_MIN = {("FILE", "INTERNAL"): "S1", ("FILE", "EXTERNAL"): "S1",
             ("DB", "INTERNAL"): "S2", ("DB", "EXTERNAL"): "S2",
             ("STREAM", "INTERNAL"): "S3", ("STREAM", "EXTERNAL"): "S3"}
SENS_RANK = {"PUBLIC": 0, "SENSITIVE": 1, "HIGHLY_SENSITIVE": 2}
CAP_ALLOWED = {"READ", "LIST", "STREAM"}
CRED_RE = re.compile(r"^(ENV|VAULT|FILE):[A-Za-z0-9_./:-]+$")
SOURCE_ID_RE = re.compile(r"^SRC-(INT|EXT)-[A-Z0-9-]+$")
FILE_TRANSPORTS_NEED_CRED = {"DB", "STREAM"}


# ---------------------------------------------------------------- 契约索引

def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def contract_fields(contract: dict) -> dict[str, dict[str, dict]]:
    return {k: {f["name"]: f for f in v["fields"]} for k, v in contract["datasets"].items()}


def tbd_targets(contract: dict) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for ds, meta in contract["datasets"].items():
        for f in meta["fields"]:
            if f.get("confidence") == "TBD":
                out.add((ds, f["name"]))
    return out


# ---------------------------------------------------------------- 注册表校验

def required_stage(entry: dict) -> str:
    t, ch = entry["transport"], entry["channel"]
    if t == "API":
        return "S1" if (ch == "EXTERNAL" and entry["availability"].get("anonymous")) else "S2"
    return STAGE_MIN[(t, ch)]


def validate_entry(entry: dict, contract: dict, registry: dict | None = None) -> list[str]:
    errs: list[str] = []
    fields = contract_fields(contract)
    tbd = tbd_targets(contract)

    # 结构 / 枚举
    if not SOURCE_ID_RE.match(entry.get("source_id", "")):
        errs.append("source_id 必须形如 SRC-INT-NAME")
    if entry.get("channel") not in ("INTERNAL", "EXTERNAL"):
        errs.append("channel 非法")
    if entry.get("transport") not in ("FILE", "DB", "API", "STREAM"):
        errs.append("transport 非法")
    if entry.get("lifecycle") not in ("DRAFT", "ACTIVE", "DEPRECATED", "RETIRED"):
        errs.append("lifecycle 非法")
    if entry.get("cadence") not in ("ONCE", "DAILY", "WEEKLY", "MONTHLY", "REALTIME"):
        errs.append("cadence 非法")

    # 数据集与敏感级别
    ds_list = entry.get("datasets") or []
    if not ds_list:
        errs.append("datasets 不能为空")
    for ds in ds_list:
        if ds not in fields:
            errs.append(f"未知数据集 {ds}")
    if ds_list and all(ds in fields for ds in ds_list) and entry.get("sensitivity") in SENS_RANK:
        worst = max(SENS_RANK[contract["datasets"][ds]["sensitivity"]] for ds in ds_list)
        if SENS_RANK[entry["sensitivity"]] < worst:
            errs.append(f"条目敏感级别 {entry['sensitivity']} 低于数据集要求")

    # 阶段矩阵
    avail = entry.get("availability") or {}
    since = avail.get("since_stage")
    if since not in ("S1", "S2", "S3"):
        errs.append("availability.since_stage 非法")
    else:
        need = required_stage(entry)
        if since != need:
            errs.append(f"{entry['transport']}/{entry['channel']} 的最早阶段是 {need}，登记为 {since}")
    if entry.get("channel") == "INTERNAL" and avail.get("anonymous") is True:
        errs.append("INTERNAL 通道不能标 anonymous=true")

    # 只读闸
    caps = set(entry.get("capabilities") or [])
    if not caps:
        errs.append("capabilities 不能为空")
    bad = caps - CAP_ALLOWED
    if bad:
        errs.append(f"出现写能力 {sorted(bad)}：任何通道不得写交易系统")
    if entry.get("writes_to_trading_system") is not False:
        errs.append("writes_to_trading_system 必须显式为 false")

    # 凭据
    cred = entry.get("credentials_ref")
    need_cred = entry.get("transport") in FILE_TRANSPORTS_NEED_CRED or (
        entry.get("transport") == "API" and not avail.get("anonymous"))
    if need_cred and not cred:
        errs.append("该 transport 需要 credentials_ref（企业侧部署注入）")
    if cred is not None:
        if not isinstance(cred, str) or not CRED_RE.match(cred):
            errs.append("credentials_ref 只接受 ENV:/VAULT:/FILE: 引用，禁明文")
        if entry.get("transport") == "FILE":
            errs.append("FILE 源不应带凭据")

    # 映射
    mapping = entry.get("mapping") or {}
    if mapping.get("time_basis") not in ("interval_end", "interval_start"):
        errs.append("mapping.time_basis 非法")
    if not mapping.get("template_fingerprint"):
        errs.append("mapping.template_fingerprint 必填")
    units = mapping.get("unit_map") or {}
    for key, val in units.items():
        if key == val:
            errs.append(f"unit_map 无意义换算 {key}->{val}")
    columns = mapping.get("columns") or {}
    if not columns:
        errs.append("mapping.columns 不能为空")
    pending = set(mapping.get("pending_confirmation") or [])
    declared_tbd: set[str] = set()
    for src, dst in columns.items():
        hit_ds = [ds for ds in ds_list if ds in fields and dst in fields[ds]]
        if not hit_ds:
            errs.append(f"映射目标 {dst} 不在所声明数据集中")
            continue
        if any((ds, dst) in tbd for ds in hit_ds):
            declared_tbd.add(dst)
            if dst not in pending:
                errs.append(f"映射目标 {dst} 是 TBD 字段：允许直通，但须在 mapping.pending_confirmation 声明")
    for dst in sorted(pending - declared_tbd):
        errs.append(f"pending_confirmation 含非 TBD 字段 {dst}")

    # 失败处理
    fp = entry.get("failure_policy") or {}
    if fp.get("on_block") != "QUARANTINE_BATCH":
        errs.append("failure_policy.on_block 必须 QUARANTINE_BATCH")
    mr = fp.get("max_retries")
    if not isinstance(mr, int) or not (0 <= mr <= 5):
        errs.append("max_retries 必须 0..5")
    elif len(fp.get("backoff_seconds") or []) < mr:
        errs.append("backoff_seconds 少于重试次数")
    if fp.get("alert") not in ("WARN", "ERROR"):
        errs.append("alert 非法")

    # 注册表级唯一性
    if registry and entry.get("source_id"):
        dup = [s for s in registry["sources"] if s.get("source_id") == entry["source_id"]]
        if len(dup) > 1:
            errs.append(f"source_id 重复 {entry['source_id']}")
    return errs


def validate_registry(doc: dict, contract: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for s in doc.get("sources", []):
        errs = validate_entry(s, contract, doc)
        if errs:
            out[s.get("source_id", "?")] = errs
    if not doc.get("sources"):
        out["<registry>"] = ["注册表为空"]
    return out


def public_export(doc: dict) -> dict:
    """对外导出：凭据引用一律脱敏，其余保留。"""
    out = copy.deepcopy(doc)
    for s in out["sources"]:
        if s.get("credentials_ref"):
            s["credentials_ref"] = "***"
    return out


# ---------------------------------------------------------------- 导入器接口

@dataclass
class ImportReport:
    source_id: str
    dataset: str
    batch_id: str
    file_hash: str
    status: str
    rows_read: int
    rows_committed: int
    rows_quarantined: int
    quality_issues: list[str]
    registry_version: str
    importer_version: str
    started_at: str
    finished_at: str
    evidence_path: str


REPORT_FIELDS = tuple(ImportReport.__dataclass_fields__.keys())


class Adapter(Protocol):
    source_id: str
    importer_version: str
    file_hash: str

    def parse(self) -> Iterator[dict]: ...
    def describe(self) -> dict: ...


@dataclass
class Repo:
    batches: dict[tuple[str, str], dict] = field(default_factory=dict)
    rows: list[dict] = field(default_factory=list)
    quarantine: list[dict] = field(default_factory=list)

    def find_batch(self, dataset: str, file_hash: str) -> dict | None:
        return self.batches.get((dataset, file_hash))

    def register(self, entry: dict, file_hash: str, seq: int) -> str:
        batch_id = f"IMP-20261105-{seq:04d}"
        self.batches[(entry["datasets"][0], file_hash)] = {
            "batch_id": batch_id, "status": "PENDING", "rows": 0}
        return batch_id


def _check_row(row: dict, ds: str, contract: dict) -> list[str]:
    errs: list[str] = []
    meta = contract["datasets"][ds]
    present = set(row.keys()) - {"extras"}
    for f in meta["fields"]:
        name = f["name"]
        required = f.get("required", True)
        if required and (name not in present or row.get(name) is None):
            errs.append(f"{ds}.{name} 缺失")
        if name not in present or row.get(name) is None:
            continue
        val = row[name]
        typ = f.get("type")
        if typ == "date" and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(val)):
            errs.append(f"{ds}.{name} 日期格式错误 {val!r}")
        if typ == "datetime" and not re.match(r"^\d{4}-\d{2}-\d{2}T", str(val)):
            errs.append(f"{ds}.{name} 时间格式错误 {val!r}")
        if typ == "enum" and f.get("enum") and val not in f["enum"]:
            errs.append(f"{ds}.{name} 不在枚举 {val!r}")
        if name == "slot_no" and not (isinstance(val, int) and 1 <= val <= 24):
            errs.append(f"{ds}.slot_no 越界 {val!r}")
    return errs


def run_pipeline(entry: dict, adapter: Adapter, repo: Repo, contract: dict,
                 now: str = NOW, force: bool = False) -> ImportReport:
    """固定六步：登记 -> 解析 -> 映射 -> 校验 -> 入库 -> 血缘/报告。"""
    assert entry["writes_to_trading_system"] is False, "只读闸：拒绝任何写交易系统的条目"
    assert entry["lifecycle"] in ("ACTIVE", "DRAFT"), "RETIRED/DEPRECATED 源不得启动新批次"
    ds = entry["datasets"][0]
    columns = entry["mapping"]["columns"]

    prior = repo.find_batch(ds, adapter.file_hash)
    if prior and prior["status"] == "COMMITTED" and not force:
        return ImportReport(entry["source_id"], ds, prior["batch_id"], adapter.file_hash,
                            "DUPLICATE", 0, 0, 0, [], entry["registry_version"],
                            adapter.importer_version, now, now, "")

    batch_id = repo.register(entry, adapter.file_hash, len(repo.batches) + 1)
    raw_rows = list(adapter.parse())
    mapped: list[dict] = []
    issues: list[str] = []
    for raw in raw_rows:
        row: dict[str, Any] = {}
        extras: dict[str, Any] = {}
        for key, val in raw.items():
            if key in columns:
                row[columns[key]] = val
            else:
                extras[key] = val
        if extras:
            row["extras"] = extras
        mapped.append(row)

    valid: list[dict] = []
    bad: list[str] = []
    for row in mapped:
        errs = _check_row(row, ds, contract)
        if errs:
            bad.extend(errs)
        else:
            valid.append(row)

    if bad:
        repo.quarantine.append({"batch_id": batch_id, "source_id": entry["source_id"],
                                "file_hash": adapter.file_hash, "issues": bad})
        repo.batches[(ds, adapter.file_hash)]["status"] = "QUARANTINED"
        return ImportReport(entry["source_id"], ds, batch_id, adapter.file_hash, "QUARANTINED",
                            len(raw_rows), 0, len(raw_rows), sorted(set(bad)),
                            entry["registry_version"], adapter.importer_version,
                            now, now, f"quarantine/{batch_id}")

    pending = entry["mapping"].get("pending_confirmation") or []
    tbd_notes = sorted({f"TBD_PASSTHROUGH:{f}" for row in valid for f in pending if f in row})
    repo.rows.extend(valid)
    repo.batches[(ds, adapter.file_hash)].update(status="COMMITTED", rows=len(valid))
    return ImportReport(entry["source_id"], ds, batch_id, adapter.file_hash, "COMMITTED",
                        len(raw_rows), len(valid), 0, tbd_notes, entry["registry_version"],
                        adapter.importer_version, now, now, f"batches/{batch_id}")


def run_with_retries(entry: dict, adapter, repo: Repo, contract: dict,
                     now: str = NOW) -> tuple[ImportReport | None, int]:
    """仅对可重试故障重试；校验失败不重试。返回 (报告, 尝试次数)。"""
    attempts = 0
    last: ImportReport | None = None
    max_retries = entry["failure_policy"]["max_retries"]
    while attempts <= max_retries:
        attempts += 1
        try:
            last = run_pipeline(entry, adapter, repo, contract, now)
            return last, attempts
        except (TimeoutError, ConnectionError) as exc:  # 可重试
            if attempts > max_retries:
                return None, attempts
            _ = exc
    return last, attempts


# ---------------------------------------------------------------- 自检用适配器

class CalendarExcel:
    source_id = "SRC-EXT-CALENDAR-CSV"
    importer_version = "excel-1.0.0"
    file_hash = "sha256:calendar-2026"

    def parse(self) -> Iterator[dict]:
        yield {"日期": "2026-01-01", "类型": "STATUTORY_HOLIDAY", "名称": "元旦", "是否工作日": False, "来源": "国办通知"}
        yield {"日期": "2026-01-04", "类型": "MAKEUP_WORKDAY", "名称": "", "是否工作日": True, "调休来源": "春节", "来源": "国办通知"}

    def describe(self) -> dict:
        return {"template": "xlsx", "rows": 2}


class CalendarCsv:
    source_id = "SRC-EXT-CALENDAR-CSV"
    importer_version = "csv-1.0.0"
    file_hash = "sha256:calendar-2026"

    def parse(self) -> Iterator[dict]:
        yield {"calendar_date": "2026-01-01", "day_type": "STATUTORY_HOLIDAY",
               "holiday_name": "元旦", "is_workday": False, "source": "国办通知"}
        yield {"calendar_date": "2026-01-04", "day_type": "MAKEUP_WORKDAY",
               "holiday_name": "", "is_workday": True, "makeup_for": "春节", "source": "国办通知"}

    def describe(self) -> dict:
        return {"template": "csv", "rows": 2}


class BadSlot:
    source_id = "SRC-INT-LOAD-XLSX"
    importer_version = "excel-1.0.0"
    file_hash = "sha256:bad-slot"

    def parse(self) -> Iterator[dict]:
        yield {"用户编号": "USER-000001", "日期": "2026-01-01", "时段": 25, "电量kWh": 100}

    def describe(self) -> dict:
        return {"template": "xlsx", "rows": 1}


class Flaky:
    source_id = "SRC-INT-LOAD-XLSX"
    importer_version = "excel-1.0.0"
    file_hash = "sha256:flaky"

    def parse(self):
        raise TimeoutError("network timeout")
        yield {}

    def describe(self) -> dict:
        return {}


# ---------------------------------------------------------------- 自检

def main() -> int:
    contract = load_json(CONTRACT_PATH)
    schema = load_json(SCHEMA_PATH)
    doc = load_json(SAMPLES_PATH)
    by_id = {s["source_id"]: s for s in doc["sources"]}
    fails = 0

    def chk(name: str, cond: bool, detail: str = "") -> None:
        nonlocal fails
        print(f"{name} {'PASS' if cond else 'FAIL'}" + (f"  {detail}" if detail else ""))
        if not cond:
            fails += 1

    # E1 注册表结构：8 条全部通过，schema 必填项齐全
    errs = validate_registry(doc, contract)
    required = set(schema["$defs"]["entry"]["required"])
    missing_required = [s["source_id"] for s in doc["sources"] if not required <= set(s)]
    chk("E1 注册表结构", not errs and not missing_required and len(doc["sources"]) == 8,
        f"8 条，错误 {errs}；缺必填 {missing_required}")

    # E2 阶段矩阵
    def mut(sid: str, **kw) -> dict:
        e = copy.deepcopy(by_id[sid])
        for path, val in kw.items():
            node = e
            parts = path.split(".")
            for p in parts[:-1]:
                node = node[p]
            node[parts[-1]] = val
        return e

    cases = [
        ("FILE 标 S2 应拒", mut("SRC-INT-LOAD-XLSX", **{"availability.since_stage": "S2"}), False),
        ("DB 标 S1 应拒", mut("SRC-INT-DB-SETTLE", **{"availability.since_stage": "S1"}), False),
        ("STREAM 标 S2 应拒", mut("SRC-INT-SPOT-STREAM", **{"availability.since_stage": "S2"}), False),
        ("公开匿名 API 可 S1", by_id["SRC-EXT-WEATHER-API"], True),
        ("内部 API 须 S2", mut("SRC-INT-DB-SETTLE", transport="API", **{"availability.since_stage": "S2"}), True),
    ]
    ok2 = all((not validate_entry(e, contract)) == want for _, e, want in cases)
    chk("E2 阶段矩阵", ok2, "；".join(f"{n}:{'通过' if not validate_entry(e, contract) else '拒绝'}" for n, e, _ in cases))

    # E3 只读闸
    write_entry = mut("SRC-INT-LOAD-XLSX")
    write_entry["capabilities"] = ["READ", "WRITE"]
    w2 = mut("SRC-INT-LOAD-XLSX")
    w2["writes_to_trading_system"] = True
    gate = ("WRITE" in CAP_ALLOWED) is False and bool(validate_entry(write_entry, contract)) and \
        bool(validate_entry(w2, contract))
    chk("E3 只读闸", gate, f"WRITE: {validate_entry(write_entry, contract)}; 写交易: {validate_entry(w2, contract)}")

    # E4 凭据
    plain = mut("SRC-INT-DB-SETTLE")
    plain["credentials_ref"] = "password=123456"
    ok_cred = not validate_entry(by_id["SRC-INT-DB-SETTLE"], contract)
    masked = json.dumps(public_export(doc), ensure_ascii=False)
    chk("E4 凭据与企业侧", ok_cred and bool(validate_entry(plain, contract)) and "CCS_SETTLE_DB_DSN" not in masked,
        f"明文拒绝={bool(validate_entry(plain, contract))}；导出脱敏={'CCS_SETTLE_DB_DSN' not in masked}")

    # E5 幂等
    repo = Repo()
    entry = by_id["SRC-EXT-CALENDAR-CSV"]
    r1 = run_pipeline(entry, CalendarExcel(), repo, contract)
    n1 = len(repo.rows)
    r2 = run_pipeline(entry, CalendarExcel(), repo, contract)
    chk("E5 幂等", r1.status == "COMMITTED" and r2.status == "DUPLICATE" and len(repo.rows) == n1 and n1 == 2,
        f"{r1.status}->{r2.status}, rows={n1}")

    # E6 换源不改下游：Excel 与 CSV 两个适配器产出同一契约行、同一报告字段集
    repo2 = Repo()
    rx = run_pipeline(entry, CalendarExcel(), repo2, contract)
    csv_entry = copy.deepcopy(entry)
    csv_entry["source_id"] = "SRC-EXT-CALENDAR-CSV-ALT"
    csv_entry["mapping"] = dict(entry["mapping"])
    csv_entry["mapping"]["columns"] = {c: c for c in ("calendar_date", "day_type", "holiday_name", "is_workday", "makeup_for", "source")}
    csv_entry["mapping"]["template_fingerprint"] = "sha256:calendar-csv-v1"
    repo3 = Repo()
    rc = run_pipeline(csv_entry, CalendarCsv(), repo3, contract)
    same_fields = tuple(asdict(rx).keys()) == REPORT_FIELDS and tuple(asdict(rc).keys()) == REPORT_FIELDS
    same_rows = [tuple(sorted(r.items(), key=lambda kv: kv[0])) for r in repo2.rows] == \
        [tuple(sorted(r.items(), key=lambda kv: kv[0])) for r in repo3.rows]
    chk("E6 换源不改下游", same_fields and same_rows and rx.status == rc.status == "COMMITTED",
        f"报告字段固定={same_fields}；行一致={same_rows}")

    # E7 失败隔离 + 重试上限
    repo4 = Repo()
    rbad = run_pipeline(by_id["SRC-INT-LOAD-XLSX"], BadSlot(), repo4, contract)
    flaky_entry = by_id["SRC-INT-LOAD-XLSX"]
    _, attempts = run_with_retries(flaky_entry, Flaky(), Repo(), contract)
    chk("E7 失败隔离与重试上限",
        rbad.status == "QUARANTINED" and rbad.rows_committed == 0 and len(repo4.rows) == 0
        and len(repo4.quarantine) == 1 and attempts == 1 + flaky_entry["failure_policy"]["max_retries"],
        f"{rbad.status}, committed={rbad.rows_committed}, 重试尝试={attempts}")

    # E8 映射覆盖 + TBD 不映射
    fields = contract_fields(contract)
    tbd = tbd_targets(contract)
    bad_map = []
    for s in doc["sources"]:
        pending = set(s["mapping"].get("pending_confirmation") or [])
        for _, dst in s["mapping"]["columns"].items():
            hit = any(dst in fields[ds] for ds in s["datasets"])
            if not hit:
                bad_map.append((s["source_id"], dst))
            elif any((ds, dst) in tbd for ds in s["datasets"]) and dst not in pending:
                bad_map.append((s["source_id"], f"{dst} 未声明待确认"))
    undeclared = {dst for dst in ("industry_code", "subject_code") if not any(
        dst in (s["mapping"].get("pending_confirmation") or []) for s in doc["sources"])}
    chk("E8 映射覆盖与 TBD", not bad_map and not undeclared and len(tbd) == 8,
        f"非法目标 {bad_map}；未声明用例={undeclared or '无'}；TBD 字段 {len(tbd)} 个")

    print(f"\n结果：{'全部通过' if fails == 0 else f'{fails} 项失败'}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())