#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""结算复现与对账口径（#55）参考实现与自检。

用法（Windows，必须 -X utf8）：
  python -X utf8 docs/spec/settlement-replay/reference_settlement.py --verify

自检 E1–E12 全 PASS 才允许发布本规格；输出可重定向为 verification.txt。
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = json.loads((HERE / "settlement.schema.json").read_text(encoding="utf-8"))
SAMPLE = json.loads((HERE / "samples" / "settlement_sample.json").read_text(encoding="utf-8"))

D = Decimal
ZERO = D("0")
P2 = D("0.01")
P3 = D("0.001")
P4 = D("0.0001")
P6 = D("0.000001")
CTX = {"prec": 28, "rounding": ROUND_HALF_UP}


class Blocked(Exception):
    """不可继续：输入/规则缺口，必须人处理，不得猜默认值。"""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def dec(v) -> D:
    if isinstance(v, D):
        return v
    try:
        return D(str(v))
    except InvalidOperation as exc:
        raise ValueError(f"不是合法小数: {v!r}") from exc


def q2(v) -> D:
    return dec(v).quantize(P2, rounding=ROUND_HALF_UP)


def q3(v) -> D:
    return dec(v).quantize(P3, rounding=ROUND_HALF_UP)


def q4(v) -> D:
    return dec(v).quantize(P4, rounding=ROUND_HALF_UP)


def q6(v) -> D:
    return dec(v).quantize(P6, rounding=ROUND_HALF_UP)


def iso(d: date) -> str:
    return d.isoformat()


def parse_date(s) -> date:
    return date.fromisoformat(str(s)[:10])


def daterange(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def digest(obj) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ 参数注册表
def resolve_parameter(params: list[dict], code: str, as_of: date, required: bool = True):
    """按 (code, as_of) 解析唯一有效参数；缺失/重叠一律 BLOCK，不给默认值。"""
    hits = []
    for p in params:
        if p.get("code") != code:
            continue
        eff_from = parse_date(p["effective_from"])
        eff_to = parse_date(p["effective_to"]) if p.get("effective_to") else None
        if eff_from <= as_of and (eff_to is None or as_of <= eff_to):
            hits.append(p)
    if not hits:
        if required:
            raise Blocked("MISSING_PARAMETER", f"{code} @ {iso(as_of)}")
        return None
    if len(hits) > 1:
        raise Blocked("PARAMETER_OVERLAP", f"{code} @ {iso(as_of)} 命中 {len(hits)} 条")
    return hits[0]["value"]


def param_evidence(p: dict) -> dict:
    return {
        "rule_doc_id": p.get("rule_doc_id"),
        "clause_no": p.get("clause_no"),
        "doc_version": p.get("doc_version"),
        "effective_from": p.get("effective_from"),
        "effective_to": p.get("effective_to"),
        "source_url": p.get("source_url"),
        "evidence": p.get("evidence", "VERBATIM"),
    }


# ------------------------------------------------------------------ 曲线分解
def largest_remainder(total: D, weights: list[D], quantum: D) -> list[D]:
    """把 total 按 weights 分成 quantum 的整数倍，和严格等于 total。"""
    total = dec(total)
    if not weights:
        return []
    if any(w < 0 for w in weights):
        raise ValueError("weights 不能为负")
    wsum = sum(weights, ZERO)
    if wsum <= 0:
        raise ValueError("weights 合计必须为正")
    units_total = total / quantum
    if units_total != units_total.to_integral_value():
        raise ValueError(f"total {total} 不是 {quantum} 的整数倍")
    units_total = int(units_total)
    exact = [D(units_total) * w / wsum for w in weights]
    floors = [int(x.to_integral_value(rounding="ROUND_FLOOR")) for x in exact]
    rem = units_total - sum(floors)
    order = sorted(range(len(weights)), key=lambda i: (-(exact[i] - floors[i]), i))
    for i in order[:rem]:
        floors[i] += 1
    out = [D(x) * quantum for x in floors]
    if sum(out, ZERO) != total:
        raise AssertionError("largest_remainder 不守恒")
    return out


def decompose_contract(contract: dict) -> list[dict]:
    """D05 合同 → 日 × 24 时段电量行；三种模式显式标注，分解前后守恒。"""
    start, end = parse_date(contract["period_start"]), parse_date(contract["period_end"])
    days = list(daterange(start, end))
    total = dec(contract["total_energy_mwh"])
    rows: list[dict] = []

    if contract.get("curve"):
        for r in contract["curve"]:
            rows.append({
                "business_date": str(r["business_date"]), "slot_no": int(r["slot_no"]),
                "energy_mwh": q3(r["energy_mwh"]), "mode": "CURVE", "quality": "OK",
            })
    elif contract.get("curve_profile"):
        profile = [q3(x) for x in contract["curve_profile"]]
        if len(profile) != 24:
            raise Blocked("CURVE_NOT_24", contract["contract_id"])
        for d in days:
            for slot, e in enumerate(profile, start=1):
                rows.append({"business_date": iso(d), "slot_no": slot, "energy_mwh": e,
                             "mode": "CURVE", "quality": "OK"})
    elif contract.get("monthly_total_mwh"):
        monthly = dec(contract["monthly_total_mwh"])
        daily = largest_remainder(monthly, [D(1)] * len(days), P3)
        for d, e in zip(days, daily):
            slots = largest_remainder(e, [D(1)] * 24, P3)
            for slot, se in enumerate(slots, start=1):
                rows.append({"business_date": iso(d), "slot_no": slot, "energy_mwh": se,
                             "mode": "EQUAL_MONTH_DAY", "quality": "ESTIMATED"})
    else:
        daily = largest_remainder(total, [D(1)] * len(days), P3)
        for d, e in zip(days, daily):
            slots = largest_remainder(e, [D(1)] * 24, P3)
            for slot, se in enumerate(slots, start=1):
                rows.append({"business_date": iso(d), "slot_no": slot, "energy_mwh": se,
                             "mode": "EQUAL_SLOT", "quality": "ESTIMATED"})

    got = sum((r["energy_mwh"] for r in rows), ZERO)
    if got != total:
        raise Blocked("CURVE_NOT_CONSERVED", f"{contract['contract_id']}: {got} != {total}")
    if any(r["energy_mwh"] < 0 for r in rows):
        raise Blocked("NEGATIVE_CURVE_ENERGY", contract["contract_id"])
    return rows


def curve_by_day(contracts: list[dict]) -> dict[tuple[str, int], list[dict]]:
    out: dict[tuple[str, int], list[dict]] = {}
    for c in contracts:
        if not c.get("settle", True):
            continue
        for r in decompose_contract(c):
            out.setdefault((r["business_date"], r["slot_no"]), []).append({**r, "contract": c})
    return out


# ------------------------------------------------------------------ 日清
def compute_daily(day: str, contracts: list[dict], actual_profile: list[float],
                  ref_profile: list[float]) -> list[dict]:
    if len(actual_profile) != 24 or len(ref_profile) != 24:
        raise Blocked("SLOT_PROFILE_NOT_24", day)
    curve = curve_by_day(contracts)
    lines: list[dict] = []
    for slot in range(1, 25):
        qa = q3(actual_profile[slot - 1])
        pref = q4(ref_profile[slot - 1])
        parts = curve.get((day, slot), [])
        qc = sum((r["energy_mwh"] for r in parts), ZERO)
        c_amount = sum((r["energy_mwh"] * dec(r["contract"]["price"]) for r in parts), ZERO)
        c_price = (c_amount / qc) if qc != 0 else ZERO
        lines.append({
            "period": day, "slot_no": slot, "item_code": "W_CONTRACT",
            "direction": "BUY", "energy_mwh": q3(qc), "price": q4(c_price),
            "amount": q2(c_amount), "evidence": {"contracts": [r["contract"]["contract_id"] for r in parts]},
        })
        spot_q = qa - qc
        lines.append({
            "period": day, "slot_no": slot, "item_code": "W_SPOT_REF",
            "direction": "BUY", "energy_mwh": q3(spot_q), "price": pref,
            "amount": q2(spot_q * pref), "evidence": {"ref_price": str(pref)},
        })
    return lines


# ------------------------------------------------------------------ 月度科目
def tiered_amount(q_actual: D, q_contract: D, tiers: list[dict]) -> D:
    if q_contract == 0:
        if q_actual != 0:
            raise Blocked("ZERO_CONTRACT_DENOMINATOR", "合同电量为 0，不能算偏差率")
        return ZERO
    r = (q_actual - q_contract) / q_contract
    sign = D(1) if r >= 0 else D(-1)
    ar = abs(r)
    amount = ZERO
    for t in tiers:
        lo = dec(t["lower"])
        hi = dec(t["upper"]) if t.get("upper") is not None else None
        upper = hi if hi is not None else ar
        if upper <= lo:
            continue
        seg = min(ar, upper) - lo
        if seg <= 0:
            continue
        amount += q_contract * seg * dec(t["rate"])
    return amount * sign


def monthly_items(period: dict, contracts: list[dict], users: list[dict],
                  actual_profile: list[float], ref_profile: list[float],
                  params: list[dict], as_of: date) -> tuple[list[dict], dict]:
    start, end = parse_date(period["start"]), parse_date(period["end"])
    days = list(daterange(start, end))
    daily_all = [compute_daily(iso(d), contracts, actual_profile, ref_profile) for d in days]

    total_contract = sum((ln["energy_mwh"] for d in daily_all for ln in d if ln["item_code"] == "W_CONTRACT"), ZERO)
    total_spot = sum((ln["energy_mwh"] for d in daily_all for ln in d if ln["item_code"] == "W_SPOT_REF"), ZERO)
    contract_amount = sum((ln["amount"] for d in daily_all for ln in d if ln["item_code"] == "W_CONTRACT"), ZERO)
    spot_amount = sum((ln["amount"] for d in daily_all for ln in d if ln["item_code"] == "W_SPOT_REF"), ZERO)
    total_actual = sum((dec(p) for p in actual_profile), ZERO) * len(days)

    loss_rate = dec(resolve_parameter(params, "loss_rate", as_of))
    trans = dec(resolve_parameter(params, "transmission_price", as_of))
    sys_p = dec(resolve_parameter(params, "system_price", as_of))
    gov = dec(resolve_parameter(params, "gov_fund_price", as_of))
    share_pool = dec(resolve_parameter(params, "share_pool", as_of))
    share_ratio = dec(resolve_parameter(params, "share_ratio", as_of))
    fee = dec(resolve_parameter(params, "market_fee_price", as_of))
    anc = dec(resolve_parameter(params, "ancillary_price", as_of))
    tiers = resolve_parameter(params, "deviation_tiers", as_of)

    items = [
        {"item_code": "W_CONTRACT", "direction": "BUY", "energy_mwh": q3(total_contract),
         "price": q4(contract_amount / total_contract) if total_contract else ZERO,
         "amount": q2(contract_amount), "evidence": {"source": "daily_rollup"}},
        {"item_code": "W_SPOT_REF", "direction": "BUY", "energy_mwh": q3(total_spot),
         "price": q4(spot_amount / total_spot) if total_spot else ZERO,
         "amount": q2(spot_amount), "evidence": {"source": "daily_rollup"}},
        {"item_code": "W_LOSS", "direction": "BUY", "energy_mwh": q3(total_contract),
         "price": q4(contract_amount / total_contract) if total_contract else ZERO,
         "amount": q2(contract_amount * loss_rate / (D(1) - loss_rate)),
         "evidence": {"loss_rate": str(loss_rate)}},
        {"item_code": "W_TRANSMISSION", "direction": "BUY", "energy_mwh": q3(total_actual),
         "price": q4(trans), "amount": q2(total_actual * trans), "evidence": {}},
        {"item_code": "W_SYSTEM", "direction": "BUY", "energy_mwh": q3(total_actual),
         "price": q4(sys_p), "amount": q2(total_actual * sys_p), "evidence": {}},
        {"item_code": "W_GOV_FUND", "direction": "BUY", "energy_mwh": q3(total_actual),
         "price": q4(gov), "amount": q2(total_actual * gov), "evidence": {}},
        {"item_code": "W_SHARE", "direction": "BUY", "energy_mwh": ZERO,
         "price": ZERO, "amount": q2(share_pool * share_ratio),
         "evidence": {"pool": str(share_pool), "ratio": str(share_ratio)}},
        {"item_code": "W_DEVIATION", "direction": "BUY", "energy_mwh": q3(total_actual - total_contract),
         "price": ZERO, "amount": q2(tiered_amount(total_actual, total_contract, tiers)),
         "evidence": {"tiers": "deviation_tiers"}},
        {"item_code": "W_ANCILLARY", "direction": "BUY", "energy_mwh": q3(total_actual),
         "price": q4(anc), "amount": q2(total_actual * anc), "evidence": {}},
        {"item_code": "W_MARKET_FEE", "direction": "BUY", "energy_mwh": q3(total_actual),
         "price": q4(fee), "amount": q2(total_actual * fee), "evidence": {}},
    ]

    retail_revenue = ZERO
    green_q_total = ZERO
    green_energy = ZERO
    green_env = ZERO
    for u in users:
        price = dec(u["retail_price"])
        retail_revenue += total_actual * price
        green_q = dec(u.get("green_energy_mwh", "0"))
        green_q_total += green_q
        green_energy += green_q * dec(u.get("green_energy_price", "0"))
        green_env += green_q * dec(u.get("green_env_price", "0"))
    items.append({"item_code": "R_ENERGY", "direction": "SELL", "energy_mwh": q3(total_actual),
                  "price": q4(retail_revenue / total_actual) if total_actual else ZERO,
                  "amount": q2(retail_revenue), "evidence": {"users": [u["user_id"] for u in users]}})
    items.append({"item_code": "R_GREEN_ENERGY", "direction": "SELL", "energy_mwh": q3(green_q_total),
                  "price": q4(green_energy / green_q_total) if green_q_total else ZERO,
                  "amount": q2(green_energy), "evidence": {"included_in": "R_ENERGY"}})
    items.append({"item_code": "R_GREEN_ENV", "direction": "SELL", "energy_mwh": ZERO,
                  "price": ZERO, "amount": q2(green_env), "evidence": {"included_in": "R_ENERGY"}})

    catalog = {i["code"]: i for i in SCHEMA["item_catalog"]}
    wholesale = sum((i["amount"] for i in items
                     if catalog[i["item_code"]]["direction"] == "BUY"
                     and catalog[i["item_code"]].get("included_in_totals", True)), ZERO)
    retail = sum((i["amount"] for i in items
                  if catalog[i["item_code"]]["direction"] == "SELL"
                  and catalog[i["item_code"]].get("included_in_totals", True)), ZERO)
    adjustment = ZERO
    totals = {
        "T_WHOLESALE_COST": q2(wholesale),
        "T_RETAIL_REVENUE": q2(retail),
        "T_ADJUSTMENT": q2(adjustment),
        "T_GROSS_MARGIN": q2(retail - wholesale + adjustment),
    }
    for it in items:
        it["period"] = period["month"]
    return items, totals

# ------------------------------------------------------------------ 对账与差异
def _key_daily(line: dict) -> tuple:
    return (line.get("period") or line.get("energy_date"), int(line["slot_no"]), line["item_code"])


def _key_monthly(line: dict) -> tuple:
    return (line.get("period") or line.get("settle_month"), line["item_code"], int(line.get("retro_seq", 0)))


def classify_diff(qi: D, qo: D, pi: D, po: D, ai: D, ao: D, amount_tol: D, energy_tol: D) -> str | None:
    adiff, qdiff, pdiff = abs(ai - ao), abs(qi - qo), abs(pi - po)
    if adiff <= amount_tol and qdiff <= energy_tol:
        return None
    if qdiff > energy_tol:
        return "QUANTITY_DIFF"
    if pdiff > P4:
        return "PRICE_DIFF"
    if adiff > amount_tol and qdiff <= energy_tol and pdiff <= P4:
        return "FORMULA_DIFF"
    if adiff <= amount_tol:
        return "ROUNDING"
    return "UNEXPLAINED"


def diff_row(period: str, slot, item: str, subject: str | None, qi, qo, pi, po, ai, ao,
             category: str, evidence: dict) -> dict:
    qi, qo, pi, po, ai, ao = dec(qi), dec(qo), dec(pi), dec(po), dec(ai), dec(ao)
    return {
        "period": period,
        "slot_no": slot,
        "item_code": item,
        "official_subject_code": subject,
        "internal_energy_mwh": str(q3(qi)),
        "official_energy_mwh": str(q3(qo)),
        "internal_price": str(q4(pi)),
        "official_price": str(q4(po)),
        "internal_amount": str(q2(ai)),
        "official_amount": str(q2(ao)),
        "delta": str(q2(ai - ao)),
        "category": category,
        "evidence": evidence,
        "action": "核对原始账单/规则参数/合同版本后重跑；不得改容差掩盖。",
    }


def compare_daily(internal: list[dict], official: list[dict], subject_map: dict,
                  tolerance: dict, day: str) -> list[dict]:
    amt_tol = dec(tolerance["amount"])
    en_tol = dec(tolerance["energy_mwh"])
    idx_i = {_key_daily(x): x for x in internal if x.get("period") == day}
    idx_o: dict[tuple, dict] = {}
    diffs: list[dict] = []
    for row in official:
        if row.get("energy_date") != day:
            continue
        item = subject_map.get(row.get("subject_code"))
        if item is None:
            diffs.append(diff_row(day, row.get("slot_no"), "UNMAPPED", row.get("subject_code"),
                                  "0", row.get("energy_mwh", 0), "0", row.get("price", 0),
                                  "0", row.get("amount", 0), "UNMAPPED_ITEM",
                                  {"official_subject_code": row.get("subject_code")}))
            continue
        idx_o[(day, int(row["slot_no"]), item)] = row
    for key in sorted(set(idx_i) | set(idx_o)):
        i, o = idx_i.get(key), idx_o.get(key)
        if o is None:
            diffs.append(diff_row(day, key[1], key[2], None,
                                  i["energy_mwh"], 0, i["price"], 0, i["amount"], 0,
                                  "MISSING_OFFICIAL", {"internal_only": True}))
            continue
        if i is None:
            diffs.append(diff_row(day, key[1], key[2], o.get("subject_code"),
                                  0, o.get("energy_mwh", 0), 0, o.get("price", 0),
                                  0, o.get("amount", 0), "MISSING_INTERNAL", {"official_only": True}))
            continue
        cat = classify_diff(dec(i["energy_mwh"]), dec(o.get("energy_mwh", 0)),
                            dec(i["price"]), dec(o.get("price", 0)),
                            dec(i["amount"]), dec(o.get("amount", 0)), amt_tol, en_tol)
        if cat:
            diffs.append(diff_row(day, key[1], key[2], o.get("subject_code"),
                                  i["energy_mwh"], o.get("energy_mwh", 0),
                                  i["price"], o.get("price", 0),
                                  i["amount"], o.get("amount", 0), cat,
                                  {"same_quantity_and_price": cat == "FORMULA_DIFF"}))
    return diffs


def compare_monthly(internal: list[dict], official: list[dict], subject_map: dict,
                    tolerance: dict, month: str) -> list[dict]:
    amt_tol = dec(tolerance["amount"])
    en_tol = dec(tolerance["energy_mwh"])
    idx_i = {_key_monthly(x): x for x in internal}
    idx_o: dict[tuple, dict] = {}
    diffs: list[dict] = []
    for row in official:
        item = subject_map.get(row.get("subject_code"))
        if item is None:
            diffs.append(diff_row(month, None, "UNMAPPED", row.get("subject_code"),
                                  "0", row.get("energy_mwh", 0), "0", 0,
                                  "0", row.get("amount", 0), "UNMAPPED_ITEM",
                                  {"official_subject_code": row.get("subject_code")}))
            continue
        idx_o[(month, item, int(row.get("retro_seq", 0)))] = row
    for key in sorted(set(idx_i) | set(idx_o), key=lambda k: (k[1], k[2])):
        i, o = idx_i.get(key), idx_o.get(key)
        if o is None:
            diffs.append(diff_row(month, None, key[1], None,
                                  i["energy_mwh"], 0, i["price"], 0, i["amount"], 0,
                                  "MISSING_OFFICIAL", {"internal_only": True}))
            continue
        if i is None:
            diffs.append(diff_row(month, None, key[1], o.get("subject_code"),
                                  0, o.get("energy_mwh", 0), 0, 0,
                                  0, o.get("amount", 0), "MISSING_INTERNAL", {"official_only": True}))
            continue
        cat = classify_diff(dec(i["energy_mwh"]), dec(o.get("energy_mwh", 0)),
                            dec(i["price"]), dec(o.get("price", 0)),
                            dec(i["amount"]), dec(o.get("amount", 0)), amt_tol, en_tol)
        if cat:
            if key[1] == "A_RETRO" and o.get("retro_seq", 0) > 0:
                cat = "OFFICIAL_ADJUSTMENT"
            diffs.append(diff_row(month, None, key[1], o.get("subject_code"),
                                  i["energy_mwh"], o.get("energy_mwh", 0),
                                  i["price"], o.get("price", 0),
                                  i["amount"], o.get("amount", 0), cat,
                                  {"retro_seq": key[2]}))
    return diffs


QUALITY_MIN_COVERAGE = D("0.99")


def assess_quality(period: dict, official_d09: list[dict]) -> dict:
    start, end = parse_date(period["start"]), parse_date(period["end"])
    days = {iso(d) for d in daterange(start, end)}
    covered = {str(r.get("energy_date")) for r in official_d09 if r.get("energy_date") in days}
    expected = len(days)
    ratio = (D(len(covered)) / D(expected)) if expected else ZERO
    return {
        "expected_days": expected,
        "covered_days": len(covered),
        "coverage_ratio": str(q6(ratio)),
        "min_coverage": str(QUALITY_MIN_COVERAGE),
        "complete": len(covered) == expected and ratio >= QUALITY_MIN_COVERAGE,
        "rule": "官方日清覆盖自然日比例必须≥99%；不足即 DATA_INCOMPLETE，不进入容差判定。",
    }


# ------------------------------------------------------------------ 运行与报告
def tolerance_profile(name: str) -> dict:
    profile = SCHEMA["tolerance"]["profiles"].get(name)
    if profile is None:
        raise Blocked("UNKNOWN_TOLERANCE_PROFILE", name)
    if name == "CONFIRMED":
        if len(profile.get("confirmed_by") or []) < 2:
            raise Blocked("TOLERANCE_NOT_CONFIRMED", "CONFIRMED 档缺少双方确认人")
        if profile.get("amount") is None or profile.get("energy_mwh") is None:
            raise Blocked("TOLERANCE_NOT_CONFIRMED", "CONFIRMED 档数值为空（Q03 未确认）")
    return profile


def build_report(period: dict, contracts: list[dict], users: list[dict],
                 actual_profile: list[float], ref_profile: list[float],
                 params: list[dict], official_d09: list[dict], official_d10: list[dict],
                 subject_map: dict, profile_name: str = "PROVISIONAL",
                 representative_confirmed: bool = False,
                 complete_month: bool = True, as_of: date | None = None) -> dict:
    as_of = as_of or parse_date(period["start"])
    tolerance = tolerance_profile(profile_name)
    start, end = parse_date(period["start"]), parse_date(period["end"])
    days = list(daterange(start, end))

    quality = assess_quality(period, official_d09)
    effective_complete = bool(complete_month) and dec(quality["coverage_ratio"]) >= QUALITY_MIN_COVERAGE

    daily_all: list[dict] = []
    daily_diffs: list[dict] = []
    for d in days:
        lines = compute_daily(iso(d), contracts, actual_profile, ref_profile)
        daily_all.extend(lines)
        daily_diffs.extend(compare_daily(lines, official_d09, subject_map, tolerance, iso(d)))
    internal, totals = monthly_items(period, contracts, users, actual_profile, ref_profile, params, as_of)
    monthly_diffs = compare_monthly(internal, official_d10, subject_map, tolerance, period["month"])

    # 日清只覆盖 DAILY 科目；月度对账覆盖全部科目。
    daily_catalog = {i["code"] for i in SCHEMA["item_catalog"] if i["cadence"] == "DAILY"}
    daily_diffs = [d for d in daily_diffs if d["item_code"] in daily_catalog or d["item_code"] == "UNMAPPED"]
    diffs = daily_diffs + monthly_diffs

    unexplained = sum(1 for d in diffs if d["category"] == "UNEXPLAINED")
    unmapped = [d for d in diffs if d["category"] == "UNMAPPED_ITEM"]
    max_delta = max((abs(dec(d["delta"])) for d in diffs), default=ZERO)
    all_within = all(abs(dec(d["delta"])) <= dec(tolerance["amount"]) for d in diffs)
    internal_total = sum((dec(i["amount"]) for i in internal), ZERO)
    official_total = sum((dec(r.get("amount", 0)) for r in official_d10), ZERO)

    if not effective_complete:
        status = "DATA_INCOMPLETE"
    elif unexplained > 0 or unmapped:
        status = "FAIL"
    elif profile_name != "CONFIRMED" or not representative_confirmed:
        status = "PROVISIONAL_PASS"
    elif all_within:
        status = "PASS"
    else:
        status = "FAIL"

    input_digest = digest({"period": period, "contracts": contracts, "users": users,
                           "actual": actual_profile, "ref": ref_profile,
                           "params": params, "official_d09": official_d09,
                           "official_d10": official_d10, "subject_map": subject_map,
                           "profile": profile_name, "representative": representative_confirmed})
    report = {
        "run_id": "RUN-" + input_digest[:16],
        "subject_scope": SCHEMA["scope"]["subject_scope"],
        "period_start": period["start"],
        "period_end": period["end"],
        "energy_month": period["month"],
        "engine_version": "reference-1.0.0",
        "schema_version": SCHEMA["version"],
        "parameter_set_id": "PS-" + digest(params)[:12],
        "parameter_set_digest": digest(params),
        "input_digest": input_digest,
        "tolerance_profile": profile_name,
        "representative_month_confirmed": representative_confirmed,
        "run_status": status,
        "summary": {
            "internal_total": str(q2(internal_total)),
            "official_total": str(q2(official_total)),
            "delta": str(q2(internal_total - official_total)),
            "diff_count": len(diffs),
            "explained_count": len(diffs) - unexplained,
            "unexplained_count": unexplained,
            "unmapped_count": len(unmapped),
            "within_tolerance": all_within,
            "max_abs_delta": str(q2(max_delta)),
            "complete_month": effective_complete,
            "coverage_days": quality["covered_days"],
            "expected_days": quality["expected_days"],
        },
        "quality": quality,
        "diffs": diffs,
        "totals": totals,
        "monthly_items": [{k: (str(v) if isinstance(v, D) else v) for k, v in i.items()} for i in internal],
        "margin": {
            "revenue": str(totals["T_RETAIL_REVENUE"]),
            "wholesale_cost": str(totals["T_WHOLESALE_COST"]),
            "adjustment": str(totals["T_ADJUSTMENT"]),
            "gross_margin": str(totals["T_GROSS_MARGIN"]),
            "residual": str(q2(totals["T_RETAIL_REVENUE"] - totals["T_WHOLESALE_COST"]
                               + totals["T_ADJUSTMENT"] - totals["T_GROSS_MARGIN"])),
            "by_item": [{"item_code": i["item_code"], "amount": str(q2(i["amount"]))} for i in internal],
        },
        "green": {
            "energy_value": str(q2(sum(dec(i["amount"]) for i in internal if i["item_code"] == "R_GREEN_ENERGY"))),
            "env_value": str(q2(sum(dec(i["amount"]) for i in internal if i["item_code"] == "R_GREEN_ENV"))),
            "env_excluded_from_peak_valley": True,
            "note": "环境价值单列，已包含在 R_ENERGY，不重复计入总收入。",
        },
        "reproducibility": {"input_digest": input_digest, "output_digest": None, "rerun_equal": None},
    }
    body = dict(report)
    body["reproducibility"] = {**report["reproducibility"], "output_digest": None}
    report["reproducibility"]["output_digest"] = digest(body)
    report["output_digest"] = report["reproducibility"]["output_digest"]
    return report


def official_maps(sample: dict):
    subject_map = sample["subject_map"]
    return subject_map


# ------------------------------------------------------------------ 自检
def _eq(got, exp, label):
    if dec(got) != dec(exp):
        raise AssertionError(f"{label}: {got} != {exp}")


def _report_kwargs(**over):
    s = SAMPLE
    kw = {
        "period": s["period"], "contracts": s["contracts"], "users": s["users"],
        "actual_profile": s["actual_profile"], "ref_profile": s["ref_price_profile"],
        "params": s["parameters"], "official_d09": s["official_d09"],
        "official_d10": s["official_d10"], "subject_map": s["subject_map"],
    }
    kw.update(over)
    return kw


def e1_schema():
    codes = [i["code"] for i in SCHEMA["item_catalog"]]
    assert len(codes) == 19, len(codes)
    assert len(set(codes)) == 19, "科目编码重复"
    for item in SCHEMA["item_catalog"]:
        assert item["family"] in SCHEMA["formula_families"], item["code"]
        assert item["direction"] in ("BUY", "SELL", "SIGNED"), item["code"]
        assert item["expression"], item["code"]
        assert item["cadence"] in ("DAILY", "MONTHLY"), item["code"]
    spot = [i["code"] for i in SCHEMA["item_catalog"] if i.get("exclusive_group") == "SPOT_BASIS"]
    assert sorted(spot) == ["W_CONTRACT_DIFF", "W_SPOT_DA", "W_SPOT_REF", "W_SPOT_RT"], spot
    assert set(SCHEMA["totals"]) == {"T_WHOLESALE_COST", "T_RETAIL_REVENUE", "T_ADJUSTMENT", "T_GROSS_MARGIN"}
    assert len(SCHEMA["diff_categories"]) == 14, len(SCHEMA["diff_categories"])
    assert SCHEMA["tolerance"]["profiles"]["CONFIRMED"]["amount"] is None
    return f"19 科目 / {len(SCHEMA['formula_families'])} 公式族 / {len(SCHEMA['diff_categories'])} 差异类"


def e2_decompose():
    c1 = decompose_contract(SAMPLE["contracts"][0])
    assert len(c1) == 28 * 24, len(c1)
    assert {r["mode"] for r in c1} == {"CURVE"}
    _eq(sum((r["energy_mwh"] for r in c1), ZERO), "5376.000", "C1 分解守恒")
    by_day = {}
    for r in c1:
        by_day[r["business_date"]] = by_day.get(r["business_date"], ZERO) + r["energy_mwh"]
    assert len(by_day) == 28 and all(v == D("192.000") for v in by_day.values())
    c2 = decompose_contract(SAMPLE["contracts"][1])
    assert {r["mode"] for r in c2} == {"EQUAL_MONTH_DAY"}
    _eq(sum((r["energy_mwh"] for r in c2), ZERO), "1200.000", "C2 分解守恒")
    lr = largest_remainder(D("1"), [D(1), D(1), D(1)], P3)
    _eq(sum(lr, ZERO), "1.000", "最大余数和")
    assert lr == [D("0.334"), D("0.333"), D("0.333")], lr
    return "CURVE 5376 / EQUAL_MONTH_DAY 1200 / 最大余数 0.334+0.333+0.333"


def e3_daily():
    lines = compute_daily("2026-02-01", SAMPLE["contracts"], SAMPLE["actual_profile"], SAMPLE["ref_price_profile"])
    assert len(lines) == 48, len(lines)
    by_item = {}
    for x in lines:
        acc = by_item.setdefault(x["item_code"], {"q": ZERO, "a": ZERO})
        acc["q"] += x["energy_mwh"]
        acc["a"] += x["amount"]
    _eq(by_item["W_CONTRACT"]["q"], "192.000", "日合同电量")
    _eq(by_item["W_CONTRACT"]["a"], "76800.00", "日合同电费")
    _eq(by_item["W_SPOT_REF"]["q"], "48.000", "日现货电量")
    _eq(by_item["W_SPOT_REF"]["a"], "16800.00", "日现货电费")
    _eq(sum((x["energy_mwh"] for x in lines if x["slot_no"] == 1), ZERO), "10.000", "时段电量平衡")
    return "2026-02-01 合同 192MWh/76800 元；合同外 48MWh/16800 元"


def e4_monthly():
    items, totals = monthly_items(SAMPLE["period"], SAMPLE["contracts"], SAMPLE["users"],
                                  SAMPLE["actual_profile"], SAMPLE["ref_price_profile"],
                                  SAMPLE["parameters"], parse_date("2026-02-15"))
    assert len(items) == 13, len(items)
    idx = {i["item_code"]: i for i in items}
    _eq(idx["W_CONTRACT"]["energy_mwh"], "5376.000", "月合同电量")
    _eq(idx["W_SPOT_REF"]["energy_mwh"], "1344.000", "月合同外电量")
    _eq(idx["W_LOSS"]["amount"], "108186.28", "线损")
    _eq(idx["W_DEVIATION"]["amount"], "34944.00", "偏差考核")
    _eq(totals["T_WHOLESALE_COST"], "3793290.28", "批发成本")
    _eq(totals["T_RETAIL_REVENUE"], "3360000.00", "零售收入")
    _eq(totals["T_GROSS_MARGIN"], "-433290.28", "批零毛利")
    _eq(idx["R_GREEN_ENERGY"]["amount"], "302400.00", "绿电电能量")
    _eq(idx["R_GREEN_ENV"]["amount"], "33600.00", "绿电环境价值")
    return "13 科目；批发 3793290.28 / 零售 3360000.00 / 毛利 -433290.28"


def e5_reconcile():
    r = build_report(**_report_kwargs(), profile_name="EXACT")
    assert r["run_status"] == "PROVISIONAL_PASS", r["run_status"]
    assert r["summary"]["diff_count"] == 0, r["summary"]
    assert r["summary"]["within_tolerance"] is True
    _eq(r["summary"]["internal_total"], "7489290.28", "内部合计")
    _eq(r["summary"]["official_total"], "7489290.28", "官方合计")
    _eq(r["summary"]["delta"], "0.00", "合计差")
    assert r["quality"]["covered_days"] == 28
    return "官方样例 0 差异；双方合计 7489290.28；覆盖 28/28"


def e6_tolerance_gate():
    try:
        tolerance_profile("CONFIRMED")
        raise AssertionError("CONFIRMED 空档应被拒绝")
    except Blocked as exc:
        assert exc.code == "TOLERANCE_NOT_CONFIRMED", exc.code
    saved = copy.deepcopy(SCHEMA["tolerance"]["profiles"]["CONFIRMED"])
    try:
        SCHEMA["tolerance"]["profiles"]["CONFIRMED"] = {
            **saved, "amount": "0.00", "energy_mwh": "0.000",
            "confirmed_by": ["甲方-业务", "乙方-财务"], "status": "CONFIRMED",
        }
        ok = build_report(**_report_kwargs(), profile_name="CONFIRMED", representative_confirmed=True)
        assert ok["run_status"] == "PASS", ok["run_status"]
        bad_rows = copy.deepcopy(SAMPLE["official_d10"])
        for row in bad_rows:
            if row["subject_code"] == "SUBJ-W-FEE":
                row["amount"] = str(dec(row["amount"]) + D("100"))
        bad = build_report(**_report_kwargs(official_d10=bad_rows), profile_name="CONFIRMED",
                           representative_confirmed=True)
        assert bad["run_status"] == "FAIL", bad["run_status"]
        assert bad["summary"]["unexplained_count"] == 0
    finally:
        SCHEMA["tolerance"]["profiles"]["CONFIRMED"] = saved
    return "空 CONFIRMED 拒绝；零容差 PASS；超差 FAIL"


def e7_classify():
    assert classify_diff(D("100"), D("110"), D("10"), D("10"), D("1000"), D("1100"), P2, P3) == "QUANTITY_DIFF"
    assert classify_diff(D("100"), D("100"), D("10"), D("11"), D("1000"), D("1100"), P2, P3) == "PRICE_DIFF"
    assert classify_diff(D("100"), D("100"), D("10"), D("10"), D("1000"), D("1100"), P2, P3) == "FORMULA_DIFF"
    assert classify_diff(D("100"), D("100"), D("10"), D("10"), D("1000"), D("1000.005"), P2, P3) is None
    items, _ = monthly_items(SAMPLE["period"], SAMPLE["contracts"], SAMPLE["users"],
                             SAMPLE["actual_profile"], SAMPLE["ref_price_profile"],
                             SAMPLE["parameters"], parse_date("2026-02-15"))
    tol = {"amount": "0.01", "energy_mwh": "0.001"}
    unmapped = compare_monthly(items, [{"settle_month": "2026-02", "subject_code": "SUBJ-X",
                                        "amount": "1.00", "energy_mwh": "0", "price": "0", "retro_seq": 0}],
                               SAMPLE["subject_map"], tol, "2026-02")
    um = [d for d in unmapped if d["category"] == "UNMAPPED_ITEM"]
    assert len(um) == 1 and um[0]["official_subject_code"] == "SUBJ-X", unmapped
    missing = compare_monthly(items, [], SAMPLE["subject_map"], tol, "2026-02")
    assert len(missing) == 13 and {d["category"] for d in missing} == {"MISSING_OFFICIAL"}
    missing_in = compare_monthly([], SAMPLE["official_d10"], SAMPLE["subject_map"], tol, "2026-02")
    assert len(missing_in) == 13 and {d["category"] for d in missing_in} == {"MISSING_INTERNAL"}
    return "数量/价格/公式/一致/未映射/缺失六类均可判定"


def e8_tiered():
    tiers = next(p["value"] for p in SAMPLE["parameters"] if p["code"] == "deviation_tiers")
    _eq(tiered_amount(D("1250"), D("1000"), tiers), "6500.00", "正向 25% 偏差")
    _eq(tiered_amount(D("750"), D("1000"), tiers), "-6500.00", "负向 25% 偏差")
    _eq(tiered_amount(D("1100"), D("1000"), tiers), "0.00", "10% 以内免考核")
    try:
        tiered_amount(D("1"), D("0"), tiers)
        raise AssertionError("合同电量为 0 应 BLOCK")
    except Blocked as exc:
        assert exc.code == "ZERO_CONTRACT_DENOMINATOR", exc.code
    return "25% 偏差 ±6500；10% 免考核；零合同 BLOCK"


def e9_margin():
    items, totals = monthly_items(SAMPLE["period"], SAMPLE["contracts"], SAMPLE["users"],
                                  SAMPLE["actual_profile"], SAMPLE["ref_price_profile"],
                                  SAMPLE["parameters"], parse_date("2026-02-15"))
    idx = {i["item_code"]: i for i in items}
    _eq(idx["W_LOSS"]["amount"], "108186.28", "线损公式")
    _eq(idx["R_GREEN_ENERGY"]["amount"] + idx["R_GREEN_ENV"]["amount"], "336000.00", "绿电合计")
    assert idx["R_GREEN_ENERGY"]["evidence"]["included_in"] == "R_ENERGY"
    _eq(totals["T_RETAIL_REVENUE"] - totals["T_WHOLESALE_COST"] + totals["T_ADJUSTMENT"],
         totals["T_GROSS_MARGIN"], "毛利恒等式")
    _eq(totals["T_GROSS_MARGIN"], "-433290.28", "毛利")
    return "线损 108186.28；绿电 336000 为 R_ENERGY 子集；毛利恒等式残差 0"


def e10_reproducible():
    a = build_report(**_report_kwargs(), profile_name="EXACT")
    b = build_report(**_report_kwargs(), profile_name="EXACT")
    assert a["input_digest"] == b["input_digest"]
    assert a["reproducibility"]["output_digest"] == b["reproducibility"]["output_digest"]
    assert a["run_id"] == b["run_id"]
    assert a["reproducibility"]["output_digest"] == b["reproducibility"]["output_digest"]
    _eq(sum((dec(i["amount"]) for i in a["monthly_items"]), ZERO), a["summary"]["internal_total"], "报告合计")
    return "两次运行 input/output digest 相同；报告合计自洽"


def e11_quality():
    r = build_report(**_report_kwargs(complete_month=False), profile_name="EXACT")
    assert r["run_status"] == "DATA_INCOMPLETE", r["run_status"]
    broken = [x for x in SAMPLE["official_d09"] if x.get("energy_date") != "2026-02-10"]
    r2 = build_report(**_report_kwargs(official_d09=broken), profile_name="EXACT")
    assert r2["run_status"] == "DATA_INCOMPLETE", r2["run_status"]
    assert r2["quality"]["covered_days"] == 27 and r2["quality"]["complete"] is False
    assert r2["summary"]["complete_month"] is False
    extra = copy.deepcopy(SAMPLE["official_d10"]) + [{
        "settle_month": "2026-02", "subject_code": "SUBJ-UNKNOWN", "amount": "1.00",
        "energy_mwh": "0", "price": "0", "retro_seq": 0}]
    r3 = build_report(**_report_kwargs(official_d10=extra), profile_name="EXACT")
    assert r3["run_status"] == "FAIL", r3["run_status"]
    assert r3["summary"]["unmapped_count"] == 1
    return "缺日 27/28 → DATA_INCOMPLETE；未映射科目 → FAIL"


def e12_parameters():
    _eq(resolve_parameter(SAMPLE["parameters"], "loss_rate", parse_date("2026-02-15")), "0.0479", "参数解析")
    try:
        resolve_parameter(SAMPLE["parameters"], "not_exists", parse_date("2026-02-15"))
        raise AssertionError("缺失参数应 BLOCK")
    except Blocked as exc:
        assert exc.code == "MISSING_PARAMETER", exc.code
    overlap = SAMPLE["parameters"] + [copy.deepcopy(SAMPLE["parameters"][0])]
    try:
        resolve_parameter(overlap, "loss_rate", parse_date("2026-02-15"))
        raise AssertionError("重叠参数应 BLOCK")
    except Blocked as exc:
        assert exc.code == "PARAMETER_OVERLAP", exc.code
    try:
        resolve_parameter(SAMPLE["parameters"], "loss_rate", parse_date("2025-12-31"))
        raise AssertionError("生效期外应 BLOCK")
    except Blocked as exc:
        assert exc.code == "MISSING_PARAMETER", exc.code
    assert resolve_parameter(SAMPLE["parameters"], "loss_rate", parse_date("2025-12-31"), required=False) is None
    return "唯一有效/as_of 边界/缺失/重叠/可选缺失均符合"


CHECKS = [
    ("E1", e1_schema), ("E2", e2_decompose), ("E3", e3_daily), ("E4", e4_monthly),
    ("E5", e5_reconcile), ("E6", e6_tolerance_gate), ("E7", e7_classify), ("E8", e8_tiered),
    ("E9", e9_margin), ("E10", e10_reproducible), ("E11", e11_quality), ("E12", e12_parameters),
]


def run_checks() -> int:
    failed = 0
    print(f"settlement-replay 自检 v{SCHEMA['version']}  schema={SCHEMA['status']}")
    for name, fn in CHECKS:
        try:
            detail = fn()
            print(f"{name} PASS  {detail}")
        except Exception as exc:  # noqa: BLE001 - 自检必须报告全部失败
            failed += 1
            print(f"{name} FAIL  {type(exc).__name__}: {exc}")
    print(f"RESULT {'ALL PASS' if failed == 0 else str(failed) + ' FAILED'}  ({len(CHECKS) - failed}/{len(CHECKS)})")
    return 0 if failed == 0 else 1


def main(argv: list[str]) -> int:
    if "--verify" in argv:
        return run_checks()
    print("用法: python -X utf8 reference_settlement.py --verify")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
