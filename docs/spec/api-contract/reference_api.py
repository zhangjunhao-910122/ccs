#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对外 API 契约参考实现与自检（票 #46）。

只用标准库；依赖:
  docs/spec/data-contract/contract.schema.json   D01–D16 字段与精度
  docs/spec/auth-rbac/{rbac.matrix.json,reference_rbac.py}  权限与掩码
  docs/spec/api-contract/{openapi.json,errors.json,agents.json,page-data.schema.json}

运行: python -X utf8 docs/spec/api-contract/reference_api.py --verify
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
API_DIR = ROOT / "docs" / "spec" / "api-contract"
DATA_DIR = ROOT / "docs" / "spec" / "data-contract"
RBAC_DIR = ROOT / "docs" / "spec" / "auth-rbac"
sys.path.insert(0, str(RBAC_DIR))
import reference_rbac as rbac  # noqa: E402

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 5, 12, 0, 0, tzinfo=TZ)
SECRET = b"reference-api-secret"
ALLOWED_EFFECTS = {"READ", "PROPOSE", "SIMULATE"}
FORBIDDEN_EFFECT = "EXECUTE"
RETRYABLE = {"RATE_LIMITED", "DEPENDENCY_UNAVAILABLE", "TIMEOUT"}
MAX_LIMIT = 1000
DEFAULT_LIMIT = 200
GRANULARITY_DAYS = {"slot": 31, "day": 366, "month": 120, "year": 20}
ROW_CAP = 100_000
CURSOR_TTL = timedelta(minutes=15)

CONTRACT = json.loads((DATA_DIR / "contract.schema.json").read_text("utf-8"))
DATASETS = CONTRACT["datasets"]
OPENAPI = json.loads((API_DIR / "openapi.json").read_text("utf-8"))
ERRORS = {e["code"]: e for e in json.loads((API_DIR / "errors.json").read_text("utf-8"))["codes"]}
AGENT_DOC = json.loads((API_DIR / "agents.json").read_text("utf-8"))
PAGE_SCHEMA = json.loads((API_DIR / "page-data.schema.json").read_text("utf-8"))
PAGE_CATALOG = PAGE_SCHEMA["x-page-catalog"]
MATRIX = rbac.load_matrix(str(RBAC_DIR / "rbac.matrix.json"))

SENS_MAP = {"PUBLIC": "PUBLIC", "SENSITIVE": "SENSITIVE", "HIGHLY_SENSITIVE": "HIGHLY"}
DIM_ACTION_RE = r"^[A-Z_]+:[A-Z_]+$"
UNIT = {"energy_mwh": "MWh", "observed_sum_mwh": "MWh", "price": "CNY/MWh", "amount": "CNY"}
SCALE = {"energy": 3, "price": 4, "amount": 2, "ratio": 4}
WRITE_PATH_ALLOWLIST = {
    "/api/v1/datasets/{dataset}/query",
    "/api/v1/datasets/{dataset}/aggregate",
    "/api/v1/exports",
    "/api/v1/agents/{agent_id}/tools/{tool_id}/invocations",
}


class ApiError(Exception):
    def __init__(self, code: str, details: dict | None = None):
        if code not in ERRORS:
            raise ValueError("unknown error code: %s" % code)
        super().__init__(code)
        self.code = code
        self.details = details or {}

    @property
    def http(self) -> int:
        return ERRORS[self.code]["http"]

    def payload(self, request_id: str = "req_20261105ref") -> dict:
        return error_response(self.code, request_id, self.details)


# ---------------------------------------------------------------------------
# 基础：定点小数、时间、信封
# ---------------------------------------------------------------------------
def decimal_str(value, kind: str = "energy") -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError("NULL_NOT_DECIMAL")
    if isinstance(value, float):
        raise ValueError("FLOAT_FORBIDDEN")
    places = SCALE[kind]
    return format(Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP), "f")


def parse_dt(value: str, field: str = "as_of", require_tz: bool = True) -> datetime:
    if not isinstance(value, str):
        raise ApiError("VALIDATION_FAILED", {"field": field, "reason": "not_a_string"})
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ApiError("VALIDATION_FAILED", {"field": field, "reason": "bad_iso8601"})
    if require_tz and dt.tzinfo is None:
        raise ApiError("VALIDATION_FAILED", {"field": field, "reason": "timezone_required"})
    return dt


def parse_date(value: str, field: str = "from") -> datetime:
    if not isinstance(value, str) or len(value) != 10:
        raise ApiError("VALIDATION_FAILED", {"field": field, "reason": "expected_yyyy_mm_dd"})
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=TZ)
    except ValueError:
        raise ApiError("VALIDATION_FAILED", {"field": field, "reason": "bad_date"})


def meta(request_id: str = "req_20261105ref", **over) -> dict:
    m = {
        "request_id": request_id,
        "api_version": "v1",
        "contract_version": "1.0.0",
        "generated_at": NOW.isoformat(),
    }
    m.update(over)
    return m


def success(data, request_id: str = "req_20261105ref", **meta_over) -> dict:
    return {"data": data, "meta": meta(request_id, **meta_over)}


def error_response(code: str, request_id: str = "req_20261105ref", details: dict | None = None) -> dict:
    e = ERRORS[code]
    return {
        "error": {"code": code, "message": e["message"], "details": details or {}, "retryable": e["retryable"]},
        "meta": meta(request_id),
    }


def validate_envelope(payload: dict) -> list[str]:
    errors = []
    if not isinstance(payload, dict):
        return ["payload_not_object"]
    has_data, has_error = "data" in payload, "error" in payload
    if has_data == has_error:
        errors.append("must_have_exactly_one_of_data_error")
    m = payload.get("meta")
    if not isinstance(m, dict):
        return errors + ["meta_missing"]
    for k in ("request_id", "api_version", "contract_version", "generated_at"):
        if k not in m:
            errors.append("meta.%s_missing" % k)
    if m.get("api_version") != "v1":
        errors.append("meta.api_version")
    if has_error:
        e = payload["error"]
        for k in ("code", "message", "retryable"):
            if k not in e:
                errors.append("error.%s_missing" % k)
        if e.get("code") not in ERRORS:
            errors.append("error.code_unknown")
        elif e.get("retryable") != ERRORS[e["code"]]["retryable"]:
            errors.append("error.retryable_mismatch")
    return errors


# ---------------------------------------------------------------------------
# 游标
# ---------------------------------------------------------------------------
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload_b64: str) -> str:
    return hmac.new(SECRET, payload_b64.encode("ascii"), hashlib.sha256).hexdigest()[:32]


def query_hash(query: dict) -> str:
    base = {k: query.get(k) for k in ("select", "filter", "time_range", "granularity", "as_of")}
    return hashlib.sha256(json.dumps(base, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def make_cursor(dataset: str, last_key: str, qhash: str, now: datetime = NOW) -> str:
    payload = {"dataset": dataset, "last_key": last_key, "query_hash": qhash,
               "exp": int((now + CURSOR_TTL).timestamp())}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    b64 = _b64e(raw)
    return b64 + "." + _sign(b64)


def read_cursor(token: str, dataset: str, qhash: str, now: datetime = NOW) -> dict:
    if not isinstance(token, str) or token.count(".") != 1:
        raise ApiError("CURSOR_INVALID")
    b64, sig = token.split(".", 1)
    if not hmac.compare_digest(sig, _sign(b64)):
        raise ApiError("CURSOR_INVALID")
    try:
        payload = json.loads(_b64d(b64))
    except Exception:
        raise ApiError("CURSOR_INVALID")
    if payload.get("dataset") != dataset or payload.get("query_hash") != qhash:
        raise ApiError("CURSOR_QUERY_MISMATCH")
    if now.timestamp() >= int(payload.get("exp", 0)):
        raise ApiError("CURSOR_EXPIRED")
    return payload


# ---------------------------------------------------------------------------
# 查询校验
# ---------------------------------------------------------------------------
def dataset_fields(dataset: str) -> dict:
    return {f["name"]: f for f in DATASETS[dataset]["fields"]}


def decimal_fields(dataset: str) -> list[str]:
    return [f["name"] for f in DATASETS[dataset]["fields"] if f["type"] == "decimal"]


def validate_query(dataset: str, body: dict, masked: set[str] | None = None) -> dict:
    if dataset not in DATASETS:
        raise ApiError("DATASET_NOT_FOUND", {"dataset": dataset})
    if not isinstance(body, dict):
        raise ApiError("INVALID_ARGUMENT", {"reason": "body_not_object"})
    allowed = {"select", "filter", "time_range", "granularity", "as_of", "limit", "cursor"}
    unknown = set(body) - allowed
    if unknown:
        raise ApiError("INVALID_ARGUMENT", {"unknown_keys": sorted(unknown)})
    fields = dataset_fields(dataset)
    gran = body.get("granularity")
    if gran not in GRANULARITY_DAYS:
        raise ApiError("VALIDATION_FAILED", {"field": "granularity"})
    tr = body.get("time_range")
    if not isinstance(tr, dict) or "from" not in tr or "to" not in tr:
        raise ApiError("VALIDATION_FAILED", {"field": "time_range"})
    frm, to = parse_date(tr["from"], "from"), parse_date(tr["to"], "to")
    if frm >= to:
        raise ApiError("VALIDATION_FAILED", {"field": "time_range", "reason": "from_not_before_to"})
    days = (to - frm).days
    if days > GRANULARITY_DAYS[gran]:
        raise ApiError("RANGE_TOO_LARGE", {"granularity": gran, "days": days, "max": GRANULARITY_DAYS[gran]})
    if days * 24 > ROW_CAP:
        raise ApiError("RANGE_TOO_LARGE", {"reason": "row_cap", "cap": ROW_CAP})
    as_of = body.get("as_of")
    if as_of is not None:
        parse_dt(as_of, "as_of", require_tz=True)
    select = body.get("select") or list(fields)
    if not isinstance(select, list) or not select:
        raise ApiError("VALIDATION_FAILED", {"field": "select"})
    if len(set(select)) != len(select):
        raise ApiError("VALIDATION_FAILED", {"field": "select", "reason": "duplicate"})
    for name in select:
        if name not in fields:
            raise ApiError("UNKNOWN_FIELD", {"field": name})
    flt = body.get("filter") or {}
    if not isinstance(flt, dict) or set(flt) - {"eq", "in", "range"}:
        raise ApiError("INVALID_ARGUMENT", {"field": "filter"})
    masked = masked or set()
    tbd = {f["name"] for f in DATASETS[dataset]["fields"] if f.get("confidence") == "TBD"}
    for op, arg in flt.items():
        if not isinstance(arg, dict):
            raise ApiError("INVALID_ARGUMENT", {"field": "filter." + op})
        if op == "in":
            for name, values in arg.items():
                if name not in fields:
                    raise ApiError("UNKNOWN_FILTER", {"field": name})
                if not isinstance(values, list) or len(values) > 100 or not values:
                    raise ApiError("INVALID_ARGUMENT", {"field": name, "reason": "in_1_to_100"})
        elif op == "range":
            for name, bounds in arg.items():
                if name not in fields:
                    raise ApiError("UNKNOWN_FILTER", {"field": name})
                if fields[name]["type"] not in ("date", "datetime", "int", "decimal"):
                    raise ApiError("INVALID_ARGUMENT", {"field": name, "reason": "range_type"})
                if not isinstance(bounds, dict) or set(bounds) - {"gte", "lte", "gt", "lt"}:
                    raise ApiError("INVALID_ARGUMENT", {"field": name, "reason": "range_bounds"})
        else:
            for name in arg:
                if name not in fields:
                    raise ApiError("UNKNOWN_FILTER", {"field": name})
        for name in arg:
            if name in tbd:
                raise ApiError("VALIDATION_FAILED", {"field": name, "reason": "tbd_not_filterable"})
            if name in masked:
                raise ApiError("FIELD_MASKED", {"field": name})
    limit = body.get("limit", DEFAULT_LIMIT)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
        raise ApiError("VALIDATION_FAILED", {"field": "limit", "max": MAX_LIMIT})
    out = {"select": list(select), "filter": flt, "time_range": {"from": tr["from"], "to": tr["to"]},
           "granularity": gran, "as_of": as_of, "limit": limit, "cursor": body.get("cursor")}
    out["query_hash"] = query_hash(out)
    return out


# ---------------------------------------------------------------------------
# 权限投影
# ---------------------------------------------------------------------------
def _map_reason(code: str) -> str:
    if code in ("NO_GRANT", "NOT_GRANTED"):
        return "NO_GRANT"
    if code == "OUT_OF_SETTLEMENT_SCOPE":
        return "OUT_OF_SETTLEMENT_SCOPE"
    return "FORBIDDEN"


def evaluate_read(subject: dict, dataset: str, energy_month: str | None, grants, now=NOW) -> dict:
    ds = DATASETS[dataset]
    decision = rbac.evaluate(
        MATRIX, subject, "SENSITIVE_DATA", "VIEW",
        resource={
            "dataset": dataset, "sensitivity": SENS_MAP[ds["sensitivity"]],
            "sensitive_fields": decimal_fields(dataset),
            "team": subject.get("team"), "owner": subject.get("account_id"),
            "energy_month": energy_month, "type": "DATASET", "id": dataset,
        },
        grants=grants, now=now, request_id="req_20261105ref",
    )
    if not decision["allowed"]:
        raise ApiError(_map_reason(decision["reason_code"]), {"dataset": dataset})
    return decision


def apply_mask(rows: list[dict], masked: set[str]) -> list[dict]:
    out = []
    for row in rows:
        copy_row = dict(row)
        for name in masked:
            if copy_row.get(name) is not None:
                copy_row[name] = rbac.mask_value(copy_row[name])
        out.append(copy_row)
    return out


def column_meta(dataset: str, select: list[str], masked: set[str]) -> list[dict]:
    cols = []
    for name in select:
        f = dataset_fields(dataset)[name]
        c = {"name": name, "type": f["type"], "masked": name in masked}
        if name in UNIT:
            c["unit"] = UNIT[name]
        if f["type"] == "decimal":
            c["scale"] = SCALE.get({"energy_mwh": "energy", "price": "price", "amount": "amount"}.get(name, "ratio"), 4)
        cols.append(c)
    return cols


def fixture_rows(dataset: str) -> list[dict]:
    if dataset == "D04":
        return [
            {"user_id": "U-0001", "business_date": "2026-11-04", "slot_no": 1,
             "interval_start": "2026-11-04T00:00:00+08:00", "energy_mwh": "12.345",
             "observed_sum_mwh": "12.345", "source_15min_count": 4, "quality_flag": "OK", "tariff_segment": None},
            {"user_id": "U-0002", "business_date": "2026-11-04", "slot_no": 1,
             "interval_start": "2026-11-04T00:00:00+08:00", "energy_mwh": "7.500",
             "observed_sum_mwh": "7.500", "source_15min_count": 4, "quality_flag": "OK", "tariff_segment": None},
            {"user_id": "U-0003", "business_date": "2026-11-04", "slot_no": 2,
             "interval_start": "2026-11-04T01:00:00+08:00", "energy_mwh": None,
             "observed_sum_mwh": None, "source_15min_count": 0, "quality_flag": "MISSING", "tariff_segment": None},
        ]
    if dataset == "D09":
        return [{"energy_date": "2026-11-04", "slot_no": 1, "subject_code": "SUBJ-0001", "direction": "SELL",
                 "energy_mwh": "100.000", "price": "450.0000", "amount": "45000.00",
                 "settle_date": "2026-11-05", "rule_ref": "RULE-001", "quality_flag": "OK"}]
    return []


def audit_event(subject: dict, action: str, dataset: str | None = None, rows: int = 0,
                request_id: str = "req_20261105ref", now: datetime = NOW) -> dict:
    return {
        "at": now.isoformat(), "actor": subject.get("account_id"), "roles": list(subject.get("roles", [])),
        "session_id": "S-REF-0001", "action": action, "resource_type": dataset or "NONE",
        "resource_id": "", "row_count": rows, "verdict": "ALLOW",
        "reason_code": "OK", "scope": "NONE", "masked_fields": [], "grant_id": None,
        "request_id": request_id,
    }


def run_query(dataset: str, body: dict, subject: dict, grants=(), now: datetime = NOW,
              rows: list[dict] | None = None) -> dict:
    masked_probe = set(decimal_fields(dataset)) if dataset in DATASETS else set()
    pre = validate_query(dataset, body, masked_probe)
    decision = evaluate_read(subject, dataset, pre["time_range"]["from"][:7], grants, now)
    masked = set(decision["masked_fields"])
    # 掩码字段不可过滤（在已知掩码集合后重校验）
    validate_query(dataset, body, masked)
    all_rows = fixture_rows(dataset) if rows is None else rows
    limit = pre["limit"]
    start = 0
    if pre["cursor"]:
        payload = read_cursor(pre["cursor"], dataset, pre["query_hash"], now)
        last = payload.get("last_key")
        for i, row in enumerate(all_rows):
            if row.get(pre["select"][0]) == last:
                start = i + 1
                break
    page = all_rows[start:start + limit]
    page = apply_mask(page, masked)
    next_cursor = None
    if start + limit < len(all_rows):
        next_cursor = make_cursor(dataset, page[-1][pre["select"][0]], pre["query_hash"], now)
    data = {"dataset": dataset, "granularity": pre["granularity"],
            "columns": column_meta(dataset, pre["select"], masked), "rows": page}
    out = success(data, next_cursor=next_cursor, row_count=len(page),
                  as_of=pre["as_of"] or now.isoformat(), masked_fields=sorted(masked),
                  quality={"valid": len(page), "partial": 0, "missing": 0, "coverage_ratio": "1.0000"},
                  units={"energy_mwh": "MWh"})
    out["meta"]["audit"] = audit_event(subject, "QUERY", dataset, len(page), now=now)
    return out


# ---------------------------------------------------------------------------
# 能力与页面
# ---------------------------------------------------------------------------
def compress_capabilities(roles: list[str]) -> list[dict]:
    out = []
    for dim in MATRIX["dimensions"]:
        cells = [c for c in MATRIX["cells"] if c["role"] in roles and c["dimension"] == dim and c["actions"]]
        if not cells:
            continue
        actions = sorted({a for c in cells for a in c["actions"]})
        scope = max((c["scope"] for c in cells), key=lambda s: rbac.SCOPE_RANK.get(s, 0))
        mask = "MASKED" if any(c["mask"] == "MASKED" for c in cells) else "NONE"
        out.append({"dimension": dim, "actions": actions, "scope": scope, "mask": mask})
    return out


def capability_codes(roles: list[str]) -> list[str]:
    codes = []
    for row in compress_capabilities(roles):
        codes.extend("%s:%s" % (row["dimension"], a) for a in row["actions"])
    return codes


def operation_index() -> dict:
    ops = {}
    for item in OPENAPI["paths"].values():
        for method, op in item.items():
            if method in ("get", "post") and isinstance(op, dict) and op.get("operationId"):
                ops[op["operationId"]] = op
    return ops


def build_catalog_page(page_id: str) -> dict:
    cat = PAGE_CATALOG[page_id]
    caps = sorted({c for b in cat["blocks"].values() for c in b.get("capabilities", [])})
    blocks = []
    for bid in cat["required_blocks"]:
        spec = cat["blocks"][bid]
        src = {"operation_id": spec["source"]}
        if spec.get("dataset"):
            src["dataset"] = spec["dataset"]
        if spec["type"] == "output_ref":
            src["output_ref"] = {"output_id": "out_pending_001", "contract": "#39", "status": "PENDING_CONTRACT"}
        blocks.append({
            "id": bid, "type": spec["type"], "title": bid, "source": src, "state": "ok",
            "freshness": {"as_of": NOW.isoformat(), "stale": False},
            "quality": {"valid": 0, "partial": 0, "missing": 0, "coverage_ratio": "1.0000"},
            "notes": [], "actions": [], "data": {"items": []},
        })
    return {"page_id": page_id, "title": cat["title"], "as_of": NOW.isoformat(),
            "generated_at": NOW.isoformat(), "capabilities": caps, "blocks": blocks}


def validate_page(page: dict) -> list[str]:
    errors = []
    if not isinstance(page, dict):
        return ["page_not_object"]
    required = ["page_id", "title", "as_of", "generated_at", "capabilities", "blocks"]
    for k in required:
        if k not in page:
            errors.append("missing:%s" % k)
    if errors:
        return errors
    page_id = page["page_id"]
    if page_id not in PAGE_CATALOG:
        return ["unknown_page:%s" % page_id]
    try:
        parse_dt(page["as_of"], "as_of")
        parse_dt(page["generated_at"], "generated_at")
    except ApiError:
        errors.append("bad_datetime")
    if not isinstance(page["capabilities"], list):
        errors.append("capabilities_not_list")
        page["capabilities"] = []
    for cap in page["capabilities"]:
        if not isinstance(cap, str) or not __import__("re").match(DIM_ACTION_RE, cap):
            errors.append("bad_capability:%r" % (cap,))
    ops = operation_index()
    seen = set()
    blocks = page.get("blocks") or []
    if not isinstance(blocks, list) or not blocks:
        errors.append("blocks_empty")
        return errors
    cat = PAGE_CATALOG[page_id]
    by_id = {}
    for b in blocks:
        bid = b.get("id")
        if bid in seen:
            errors.append("duplicate_block:%s" % bid)
        seen.add(bid)
        by_id[bid] = b
    for bid in cat["required_blocks"]:
        if bid not in by_id:
            errors.append("missing_block:%s" % bid)
    for b in blocks:
        bid = b.get("id")
        for k in ("id", "type", "title", "source", "state", "freshness", "quality", "notes", "actions"):
            if k not in b:
                errors.append("block_%s_missing:%s" % (bid, k))
        if b.get("type") not in ("kpi", "chart", "table", "quality", "actions", "output_ref"):
            errors.append("bad_type:%s" % bid)
        if b.get("state") not in ("ok", "empty", "partial", "error", "forbidden"):
            errors.append("bad_state:%s" % bid)
        if b.get("state") != "ok":
            notes = b.get("notes") or []
            if not isinstance(notes, list) or not any(str(n).strip() for n in notes):
                errors.append("non_ok_without_notes:%s" % bid)
        if b.get("state") == "empty":
            if b.get("data") is not None:
                errors.append("empty_with_data:%s" % bid)
        if b.get("state") == "ok" and b.get("data") is None:
            errors.append("ok_without_data:%s" % bid)
        src = b.get("source") or {}
        if src.get("operation_id") not in ops:
            errors.append("unknown_source:%s:%s" % (bid, src.get("operation_id")))
        if b.get("type") == "output_ref":
            ref = src.get("output_ref") or {}
            if ref.get("contract") != "#39" or ref.get("status") not in ("PENDING_CONTRACT", "READY"):
                errors.append("bad_output_ref:%s" % bid)
        spec = cat["blocks"].get(bid)
        if spec:
            if spec.get("dataset") and src.get("dataset") != spec["dataset"]:
                errors.append("dataset_mismatch:%s" % bid)
            if b.get("type") != spec["type"]:
                errors.append("catalog_type_mismatch:%s" % bid)
            for cap in spec.get("capabilities", []):
                if cap not in page["capabilities"]:
                    errors.append("catalog_capability_missing:%s:%s" % (bid, cap))
        for action in b.get("actions") or []:
            cap = action.get("capability")
            if cap not in page["capabilities"]:
                errors.append("action_not_in_capabilities:%s:%s" % (bid, cap))
    return errors


# ---------------------------------------------------------------------------
# Agent 工具
# ---------------------------------------------------------------------------
def load_agents(doc: dict = AGENT_DOC) -> dict:
    index = {}
    for agent in doc.get("agents", []):
        for tool in agent.get("tools", []):
            if tool.get("effect") not in ALLOWED_EFFECTS:
                raise ValueError("ILLEGAL_EFFECT:%s.%s" % (agent.get("id"), tool.get("id")))
            if not tool.get("input_schema", {}).get("required"):
                raise ValueError("INPUT_SCHEMA_MISSING:%s.%s" % (agent.get("id"), tool.get("id")))
            key = (agent["id"], tool["id"])
            if key in index:
                raise ValueError("DUPLICATE_TOOL:%s.%s" % key)
            index[key] = {**tool, "agent": agent["id"]}
    return index


def citation_fixture() -> dict:
    return {"doc_id": "DOC-HB-2026-001", "doc_no": "华中监能市场〔2026〕1号",
            "title": "湖北省电力中长期交易实施细则", "clause_no": "第二十五条",
            "version": "V1.0", "effective_from": "2026-01-01",
            "page_ref": "p.12", "text_span": "中长期交易按 24 时段组织申报。"}


def default_result(agent_id: str, tool_id: str, inp: dict) -> dict:
    if tool_id == "answer_rule_question":
        return {"answer": "按现行细则，中长期交易按 24 时段组织申报。", "citations": [citation_fixture()]}
    if tool_id == "explain_change":
        return {"changes": [{"kind": "TEXT_EDIT", "clause_no": "第二十五条"}], "citations": [citation_fixture()]}
    if tool_id == "inspect_batch":
        return {"batch_id": inp.get("batch_id"), "quality": {"blocked": 0, "warn": 0}, "issues": []}
    if tool_id == "predict_portfolio":
        return {"business_date": inp.get("business_date"), "forecast_origin": "P_BID",
                "points": [{"slot_no": i, "energy_mwh": "0.000"} for i in range(1, 25)]}
    if tool_id == "explain_error":
        return {"run_id": inp.get("run_id"), "mae": "3.2100", "attributions": []}
    if tool_id == "compute_coverage":
        return {"energy_month": inp.get("energy_month"), "coverage_ratio": "0.8500", "open_position_mwh": "120.000"}
    if tool_id == "draft_strategies":
        return {"strategies": [{"name": n} for n in ("保守", "平衡", "进取")]}
    if tool_id == "reproduce_day":
        return {"energy_date": inp.get("energy_date"), "differences": [], "tolerance": "0.01"}
    if tool_id == "check_limits":
        return {"subject_id": inp.get("subject_id"), "verdict": "ALLOW", "reasons": []}
    if tool_id == "score_strategy":
        return {"simulation_id": inp.get("simulation_id"), "scores": {"total": "80.00"}}
    return {}


def invoke_agent(agent_id: str, tool_id: str, idempotency_key: str, body: dict, subject: dict,
                 store: dict | None = None, now: datetime = NOW, result: dict | None = None) -> dict:
    store = store if store is not None else {}
    index = load_agents()
    tool = index.get((agent_id, tool_id))
    if tool is None:
        raise ApiError("TOOL_NOT_FOUND", {"agent": agent_id, "tool": tool_id})
    if not isinstance(idempotency_key, str) or len(idempotency_key) < 8:
        raise ApiError("INVALID_ARGUMENT", {"field": "Idempotency-Key"})
    if tool["effect"] not in ALLOWED_EFFECTS:
        raise ApiError("WRITE_FORBIDDEN", {"tool": tool_id})
    inp = (body or {}).get("input")
    if not isinstance(inp, dict):
        raise ApiError("VALIDATION_FAILED", {"field": "input"})
    schema = tool["input_schema"]
    for name in schema.get("required", []):
        if name not in inp:
            raise ApiError("VALIDATION_FAILED", {"field": "input." + name})
    if schema.get("additionalProperties") is False:
        unknown = set(inp) - set(schema.get("properties", {}))
        if unknown:
            raise ApiError("INVALID_ARGUMENT", {"unknown_input": sorted(unknown)})
    ctx_in = (body or {}).get("context") or {}
    ctx = {"as_of": ctx_in.get("as_of") or now.isoformat()}
    for name in ctx_in:
        if name not in ("as_of", "forecast_origin", "locale", "session_id"):
            raise ApiError("INVALID_ARGUMENT", {"unknown_context": name})
    if ctx_in.get("locale") not in (None, "zh-CN"):
        raise ApiError("INVALID_ARGUMENT", {"field": "context.locale"})
    if "forecast_origin" in ctx_in:
        if ctx_in["forecast_origin"] not in ("P_CURVE", "P_BID", "P_REVIEW"):
            raise ApiError("VALIDATION_FAILED", {"field": "context.forecast_origin"})
        ctx["forecast_origin"] = ctx_in["forecast_origin"]
    for need in tool.get("requires_context", []):
        if need == "forecast_origin" and "forecast_origin" not in ctx:
            raise ApiError("FORECAST_ORIGIN_REQUIRED", {"tool": tool_id})
    canonical = json.dumps({"agent": agent_id, "tool": tool_id, "input": inp, "context": ctx},
                           sort_keys=True, ensure_ascii=False)
    key = (subject.get("account_id"), agent_id, tool_id, idempotency_key)
    if key in store:
        prev = store[key]
        if prev["canonical"] != canonical:
            raise ApiError("IDEMPOTENCY_KEY_REUSED", {"idempotency_key": idempotency_key})
        return prev["response"]
    out = default_result(agent_id, tool_id, inp) if result is None else result
    citations = out.get("citations") or []
    if tool.get("must_cite") and not citations:
        raise ApiError("CITATION_REQUIRED", {"tool": tool_id})
    for name in tool["output_schema"].get("required", []):
        if name not in out:
            raise ApiError("TOOL_OUTPUT_INVALID", {"missing": name})
    masked_datasets = tool.get("sensitive_datasets", [])
    verdict = "MASKED" if any(DATASETS[d]["sensitivity"] == "HIGHLY_SENSITIVE" for d in masked_datasets) else "ALLOW"
    run = {
        "run_id": "run_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12],
        "status": "SUCCEEDED", "output": out, "citations": citations,
        "audit": {"model": "deepseek-chat", "prompt_tokens": 100, "completion_tokens": 50,
                  "latency_ms": 1200, "cost_cny": "0.0100", "data_export_verdict": verdict},
    }
    resp = success(run, request_id="req_20261105ref")
    store[key] = {"canonical": canonical, "response": resp}
    return resp


# ---------------------------------------------------------------------------
# 无写保证
# ---------------------------------------------------------------------------
def scan_no_write() -> list[str]:
    errors = []
    ops = operation_index()
    posts = set()
    for path, item in OPENAPI["paths"].items():
        for method, op in item.items():
            if method not in ("get", "post"):
                if isinstance(op, dict) and op.get("operationId"):
                    errors.append("method_not_allowed:%s %s" % (method, path))
                continue
            if not isinstance(op, dict) or not op.get("operationId"):
                continue
            if op.get("x-effect") not in ALLOWED_EFFECTS:
                errors.append("bad_effect:%s" % op["operationId"])
            if method == "post":
                posts.add(path)
    if posts != WRITE_PATH_ALLOWLIST:
        errors.append("post_allowlist_mismatch:%s" % sorted(posts))
    text = json.dumps(OPENAPI, ensure_ascii=False)
    for token in ("d04_load_hourly", "src_is_current", "INSERT INTO", "DELETE FROM", "UPDATE d0"):
        if token in text:
            errors.append("forbidden_token:%s" % token)
    for agent in AGENT_DOC["agents"]:
        for tool in agent["tools"]:
            if tool["effect"] == FORBIDDEN_EFFECT:
                errors.append("execute_tool:%s.%s" % (agent["id"], tool["id"]))
    if not OPENAPI.get("security"):
        errors.append("global_security_missing")
    return errors


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
def subject(account: str, role: str, team: str = "TEAM-A") -> dict:
    return {"account_id": account, "roles": [role], "status": "ACTIVE", "team": team}


def grant(subject_id: str, scope: str = "ALL", mask: str = "MASKED", expose: str = "NONE",
          approvals=()) -> dict:
    return rbac.make_grant(grant_id="G-" + subject_id[-4:], subject=subject_id, dimension="SENSITIVE_DATA",
                           scope=scope, mask=mask, grantor="U-ADMIN-01",
                           reason="分析组合负荷与结算数据所需授权", created_at=NOW - timedelta(days=1),
                           ttl_days=30, expose=expose, approvals=approvals)


def expect_error(code: str, fn) -> str:
    try:
        fn()
    except ApiError as exc:
        if exc.code != code:
            return "expected %s got %s" % (code, exc.code)
        return ""
    return "expected %s but no error" % code


def check_e1() -> list[str]:
    errs = []
    ops = operation_index()
    if len(opens := OPENAPI["paths"]) != 11:
        errs.append("paths=%d" % len(opens))
    if len(ops) != 11:
        errs.append("operations=%d" % len(ops))
    if len(set(ops)) != len(ops):
        errs.append("operationId_not_unique")
    for oid, op in ops.items():
        if op.get("x-effect") not in ALLOWED_EFFECTS:
            errs.append("effect:%s" % oid)
        if not op.get("responses"):
            errs.append("responses:%s" % oid)
    if not OPENAPI["servers"][0]["url"].endswith("/api/v1"):
        errs.append("server_url")
    if OPENAPI["components"]["securitySchemes"]["bearerAuth"]["scheme"] != "bearer":
        errs.append("security_scheme")
    if "record_id" in json.dumps(OPENAPI):
        errs.append("leaks_record_id")
    return errs


def check_e2() -> list[str]:
    errs = []
    ok = success({"dataset": "D04", "rows": []})
    errs += ["envelope:" + e for e in validate_envelope(ok)]
    bad = {"data": {}, "meta": meta(), "error": {"code": "X", "message": "", "retryable": False}}
    if not validate_envelope(bad):
        errs.append("both_data_error_accepted")
    errs += ["error_envelope:" + e for e in validate_envelope(error_response("NO_GRANT"))]
    if decimal_str("0.1", "energy") != "0.100":
        errs.append("decimal_scale")
    if decimal_str(1, "price") != "1.0000":
        errs.append("decimal_int")
    for bad_value in (0.1, True, None):
        try:
            decimal_str(bad_value)
            errs.append("float_or_null_accepted:%r" % (bad_value,))
        except (ValueError, TypeError):
            pass
    rows = apply_mask([{"energy_mwh": "12.345"}, {"energy_mwh": None}], {"energy_mwh"})
    if not str(rows[0]["energy_mwh"]).startswith("MASK-H"):
        errs.append("mask_prefix")
    if rows[1]["energy_mwh"] is not None:
        errs.append("null_zeroed")
    resp_path = API_DIR / "samples" / "query_response.json"
    if resp_path.exists():
        sample = json.loads(resp_path.read_text("utf-8"))
        errs += ["sample_envelope:" + e for e in validate_envelope(sample)]
        for row in sample.get("data", {}).get("rows", []):
            for key, value in row.items():
                if isinstance(value, float):
                    errs.append("sample_float:%s" % key)
    return errs


def check_e3() -> list[str]:
    errs = []
    qh = "abc123def456abcd"
    tok = make_cursor("D04", "U-0001", qh)
    if read_cursor(tok, "D04", qh)["dataset"] != "D04":
        errs.append("roundtrip")
    tampered = tok[:-1] + ("A" if tok[-1] != "A" else "B")
    errs.append(expect_error("CURSOR_INVALID", lambda: read_cursor(tampered, "D04", qh)))
    errs.append(expect_error("CURSOR_QUERY_MISMATCH", lambda: read_cursor(tok, "D04", "ffff0000ffff0000")))
    expired = make_cursor("D04", "U-0001", qh, NOW - timedelta(minutes=16))
    errs.append(expect_error("CURSOR_EXPIRED", lambda: read_cursor(expired, "D04", qh)))
    return [e for e in errs if e]


def check_e4() -> list[str]:
    errs = []
    good = {"time_range": {"from": "2026-10-05", "to": "2026-11-05"}, "granularity": "slot", "limit": 200}
    try:
        validate_query("D04", good)
    except ApiError as exc:
        errs.append("valid_query:%s" % exc.code)
    errs.append(expect_error("RANGE_TOO_LARGE", lambda: validate_query(
        "D04", {"time_range": {"from": "2026-10-01", "to": "2026-11-05"}, "granularity": "slot"})))
    errs.append(expect_error("UNKNOWN_FIELD", lambda: validate_query(
        "D04", {**good, "select": ["energy_kwh"]})))
    errs.append(expect_error("UNKNOWN_FILTER", lambda: validate_query(
        "D04", {**good, "filter": {"eq": {"slot": 1}}})))
    errs.append(expect_error("VALIDATION_FAILED", lambda: validate_query(
        "D04", {**good, "filter": {"eq": {"tariff_segment": "PEAK"}}})))
    errs.append(expect_error("VALIDATION_FAILED", lambda: validate_query(
        "D04", {**good, "as_of": "2026-11-05T00:00:00"})))
    errs.append(expect_error("VALIDATION_FAILED", lambda: validate_query(
        "D04", {**good, "limit": 0})))
    errs.append(expect_error("INVALID_ARGUMENT", lambda: validate_query(
        "D04", {**good, "columns": []})))
    return [e for e in errs if e]


def check_e5() -> list[str]:
    errs = []
    trader = subject("U-TRADER-01", "TRADER")
    trader_grant = grant("U-TRADER-01")
    body = {"time_range": {"from": "2026-11-04", "to": "2026-11-05"}, "granularity": "slot"}
    resp = run_query("D04", body, trader, [trader_grant])
    if not resp["meta"]["masked_fields"]:
        errs.append("trader_not_masked")
    if not str(resp["data"]["rows"][0]["energy_mwh"]).startswith("MASK-H"):
        errs.append("trader_value_plain")
    errs.append(expect_error("NO_GRANT", lambda: run_query("D04", body, trader, [])))
    auditor = subject("U-AUDITOR-01", "AUDITOR")
    resp = run_query("D04", body, auditor, [grant("U-AUDITOR-01")])
    if not resp["meta"]["masked_fields"]:
        errs.append("auditor_not_masked")
    admin = subject("U-ADMIN-01", "ADMIN")
    errs.append(expect_error("NO_GRANT", lambda: run_query("D04", body, admin, [])))
    settlement = subject("U-SETTLE-01", "SETTLEMENT")
    d09 = {"time_range": {"from": "2026-11-04", "to": "2026-11-05"}, "granularity": "slot"}
    try:
        run_query("D09", d09, settlement, [])
    except ApiError as exc:
        errs.append("settlement_d09:%s" % exc.code)
    errs.append(expect_error("OUT_OF_SETTLEMENT_SCOPE", lambda: run_query("D04", body, settlement, [])))
    exposed = grant("U-TRADER-01", mask="NONE", expose="HIGHLY", approvals=("U-RISK-01", "U-AUDIT-01"))
    resp = run_query("D04", body, trader, [exposed])
    if resp["meta"]["masked_fields"]:
        errs.append("two_approver_expose_still_masked")
    errs.append(expect_error("FIELD_MASKED", lambda: run_query(
        "D04", {**body, "filter": {"eq": {"energy_mwh": "12.345"}}}, trader, [trader_grant])))
    return [e for e in errs if e]


def check_e6() -> list[str]:
    errs = []
    used = set()
    for oid, op in operation_index().items():
        for status, resp in op["responses"].items():
            for code in resp.get("x-error-codes", []):
                used.add(code)
                if code not in ERRORS:
                    errs.append("unknown_code:%s" % code)
                elif str(ERRORS[code]["http"]) != str(status):
                    errs.append("http_mismatch:%s:%s/%s" % (code, status, ERRORS[code]["http"]))
    missing = set(ERRORS) - used
    if missing:
        errs.append("orphan_codes:%s" % sorted(missing))
    for code, meta_row in ERRORS.items():
        if meta_row["retryable"] != (code in RETRYABLE):
            errs.append("retryable:%s" % code)
    return errs


def check_e7() -> list[str]:
    errs = []
    store = {}
    subj = subject("U-TRADER-01", "TRADER")
    body = {"input": {"question": "中长期交易按几个时段申报？"},
            "context": {"as_of": NOW.isoformat()}}
    first = invoke_agent("rule_coach", "answer_rule_question", "idem-key-0001", body, subj, store)
    second = invoke_agent("rule_coach", "answer_rule_question", "idem-key-0001", body, subj, store)
    if first["data"]["run_id"] != second["data"]["run_id"]:
        errs.append("replay_changed_run")
    changed = {"input": {"question": "改一个问题"}, "context": {"as_of": NOW.isoformat()}}
    errs.append(expect_error("IDEMPOTENCY_KEY_REUSED", lambda: invoke_agent(
        "rule_coach", "answer_rule_question", "idem-key-0001", changed, subj, store)))
    if len(store) != 1:
        errs.append("store_grew:%d" % len(store))
    return [e for e in errs if e]


def check_e8() -> list[str]:
    return scan_no_write()


def check_e9() -> list[str]:
    errs = []
    sample_path = API_DIR / "samples" / "page_data.json"
    if sample_path.exists():
        sample_file = json.loads(sample_path.read_text("utf-8"))
        problems = validate_page(sample_file)
        if problems:
            errs.append("sample_page_invalid:%s" % problems)
    sample = build_catalog_page("data_explorer")
    problems = validate_page(sample)
    if problems:
        errs.append("catalog_page_invalid:%s" % problems)
    for page_id in PAGE_CATALOG:
        if validate_page(build_catalog_page(page_id)):
            errs.append("catalog_invalid:%s" % page_id)
    mutations = []
    m = copy.deepcopy(sample)
    m["blocks"] = [b for b in m["blocks"] if b["id"] != "rows"]
    mutations.append(("missing_block", m))
    m = copy.deepcopy(sample)
    for b in m["blocks"]:
        if b["id"] == "rows":
            b["state"] = "empty"
            b["notes"] = ["当日无数据"]
            b["data"] = {"rows": []}
    mutations.append(("empty_with_data", m))
    m = copy.deepcopy(sample)
    for b in m["blocks"]:
        if b["id"] == "rows":
            b["state"] = "error"
            b["notes"] = []
    mutations.append(("error_without_notes", m))
    m = copy.deepcopy(sample)
    for b in m["blocks"]:
        if b["id"] == "rows":
            b["actions"] = [{"id": "a", "label": "导出", "capability": "DATA_EXPORT:EXPORT"}]
    mutations.append(("action_outside_capabilities", m))
    m = copy.deepcopy(sample)
    for b in m["blocks"]:
        b["source"]["operation_id"] = "deleteTradingAccount"
    mutations.append(("unknown_operation", m))
    m = copy.deepcopy(sample)
    m["blocks"].append(copy.deepcopy(m["blocks"][0]))
    mutations.append(("duplicate_block", m))
    for name, page in mutations:
        if not validate_page(page):
            errs.append("mutation_passed:%s" % name)
    return [e for e in errs if e]


def check_e10() -> list[str]:
    errs = []
    subj = subject("U-TRADER-01", "TRADER")
    body = {"input": {"question": "24 时段如何划分？"}, "context": {"as_of": NOW.isoformat()}}
    run = invoke_agent("rule_coach", "answer_rule_question", "idem-key-0002", body, subj)
    if run["data"]["status"] != "SUCCEEDED" or not run["data"]["citations"]:
        errs.append("citation_happy_path")
    errs.append(expect_error("CITATION_REQUIRED", lambda: invoke_agent(
        "rule_coach", "answer_rule_question", "idem-key-0003", body, subject("U-TRADER-01", "TRADER"), {},
        result={"answer": "无引用", "citations": []})))
    errs.append(expect_error("FORECAST_ORIGIN_REQUIRED", lambda: invoke_agent(
        "load_forecast", "predict_portfolio", "idem-key-0004",
        {"input": {"business_date": "2026-11-06"}, "context": {"as_of": NOW.isoformat()}},
        subject("U-TRADER-01", "TRADER"))))
    errs.append(expect_error("TOOL_NOT_FOUND", lambda: invoke_agent(
        "rule_coach", "delete_rules", "idem-key-0005", body, subj)))
    errs.append(expect_error("CITATION_REQUIRED", lambda: invoke_agent(
        "rule_coach", "explain_change", "idem-key-0006",
        {"input": {"doc_id": "DOC-1"}, "context": {"as_of": NOW.isoformat()}},
        subj, result={"changes": [], "citations": []})))
    bad_registry = copy.deepcopy(AGENT_DOC)
    bad_registry["agents"][0]["tools"][0]["effect"] = "EXECUTE"
    try:
        load_agents(bad_registry)
        errs.append("execute_effect_accepted")
    except ValueError:
        pass
    return [e for e in errs if e]


def check_e11() -> list[str]:
    errs = []
    cases = [("12.3456", "energy", "12.346"), ("0.1", "price", "0.1000"),
             ("-3.5", "amount", "-3.50"), ("1", "ratio", "1.0000")]
    for raw, kind, want in cases:
        got = decimal_str(raw, kind)
        if got != want:
            errs.append("scale:%s->%s!=%s" % (raw, got, want))
    row = {"energy_mwh": "1.2345", "price": None}
    out = apply_mask([row], set())
    if out[0]["price"] is not None:
        errs.append("null_dropped")
    meta_ok = meta(next_cursor=None, row_count=0, masked_fields=[])
    if meta_ok["next_cursor"] is not None:
        errs.append("next_cursor_null")
    return errs


def check_e12() -> list[str]:
    errs = []
    out_schema = OPENAPI["components"]["schemas"]["OutputDocument"]
    props = out_schema.get("properties", {})
    if props.get("status", {}).get("enum") != ["PENDING_CONTRACT", "READY"]:
        errs.append("output_status_not_pending")
    query_text = json.dumps(OPENAPI["components"]["schemas"]["QueryData"], ensure_ascii=False)
    for token in ("forecast_origin", "predicted", "drift"):
        if token in query_text:
            errs.append("prediction_field_in_query:%s" % token)
    contract_const = PAGE_SCHEMA["$defs"]["OutputRef"]["properties"]["contract"].get("const")
    if contract_const != "#39":
        errs.append("page_output_ref_not_39")
    if not any(t["id"] == "predict_portfolio" and "forecast_origin" in t.get("requires_context", [])
               for a in AGENT_DOC["agents"] for t in a["tools"]):
        errs.append("predict_tool_missing_origin")
    return errs


CHECKS = [("E1", "契约结构", check_e1), ("E2", "信封与定点小数", check_e2),
          ("E3", "游标", check_e3), ("E4", "查询校验", check_e4),
          ("E5", "权限投影", check_e5), ("E6", "错误目录", check_e6),
          ("E7", "幂等", check_e7), ("E8", "无写保证", check_e8),
          ("E9", "页面契约", check_e9), ("E10", "Agent 工具", check_e10),
          ("E11", "序列化", check_e11), ("E12", "输出契约对齐", check_e12)]


def main() -> int:
    if "--verify" not in sys.argv:
        print(__doc__)
        return 0
    failed = 0
    print("对外 API 契约参考实现自检（票 #46）")
    print("时间基准:", NOW.isoformat())
    print("契约:", CONTRACT["$id"] if "$id" in CONTRACT else "contract.schema.json")
    print("-" * 68)
    for code, title, fn in CHECKS:
        errors = [e for e in fn() if e]
        status = "PASS" if not errors else "FAIL"
        print("%s %-14s %s" % (code, title, status))
        for e in errors:
            print("   -", e)
        failed += 0 if not errors else 1
    print("-" * 68)
    print("结果: %d/%d PASS" % (len(CHECKS) - failed, len(CHECKS)))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
