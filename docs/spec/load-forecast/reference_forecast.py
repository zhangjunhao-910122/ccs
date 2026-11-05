#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""#45 组合负荷预测特征与口径 —— 参考实现与自检（E1–E10）。

只用标准库。运行：
    python reference_forecast.py --verify
    python reference_forecast.py --generate-samples
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

BASE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(BASE, "feature-manifest.json")
SAMPLES = os.path.join(BASE, "samples")
HISTORY_CSV = os.path.join(SAMPLES, "load_history.csv")

SLOTS = list(range(1, 25))
ORIGIN_TIMES = {
    "P_CURVE": (-2, "12:00"),
    "P_BID": (-1, "08:00"),
    "P_REVIEW": (-1, "20:00"),
}
ALLOWED_KINDS = {"STATIC", "EVENT", "OBS_LAG", "NEVER"}
MISSING_REASONS = {
    "SOURCE_UNAVAILABLE",
    "NOT_YET_PUBLISHED",
    "NOT_PROVIDED",
    "FORECAST_NOT_ARCHIVED",
}
DEGRADE_ACTIONS = {"DROP_FEATURE", "FALLBACK_BASELINE"}
FIXED_SENTENCE = "基于历史统计关联，非因果判定"
FORBIDDEN_WORDS = [
    "因为", "因此", "原因", "导致", "保证", "必然", "一定会",
    "稳赚", "建议买入", "建议卖出", "预测一定", "肯定能", "由于",
]
CAUSAL_RE = re.compile(r"(?<!原)因(?!果)")


class FeatureLeakageError(RuntimeError):
    """特征在预测起点尚不可得，却被使用。"""


class FeatureNotAllowed(RuntimeError):
    """特征未登记为该起点允许使用。"""


class CannotForecast(RuntimeError):
    """必需特征缺失，无法给出预测。"""


# ---------------------------------------------------------------- 基础工具
def d(s):
    return date.fromisoformat(s)


def iso(x):
    return x.isoformat()


def origin_timestamp(origin_type, target):
    if origin_type not in ORIGIN_TIMES:
        raise ValueError("未知起点: %s" % origin_type)
    off, hhmm = ORIGIN_TIMES[origin_type]
    day = target + timedelta(days=off)
    h, m = [int(v) for v in hhmm.split(":")]
    return datetime(day.year, day.month, day.day, h, m)


def at(day, hhmm):
    h, m = [int(v) for v in hhmm.split(":")]
    return datetime(day.year, day.month, day.day, h, m)


def load_manifest(path=MANIFEST):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def feature_map(manifest):
    return {f["feature_id"]: f for f in manifest["features"]}


def q(x, nd=4):
    return Decimal(str(x)).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP)


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return None
    mid = n // 2
    if n % 2:
        return xs[mid]
    return (xs[mid - 1] + xs[mid]) / 2.0


def percentile(xs, p):
    """线性插值分位数，p 取 0..100。"""
    xs = sorted(xs)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * (p / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return xs[int(k)]
    return xs[lo] * (hi - k) + xs[hi] * (k - lo)


# ---------------------------------------------------------- 台账与可得性
REQUIRED_COLUMNS = [
    "feature_id", "family", "definition", "source_dataset", "source_system",
    "availability", "shift_days", "allowed_origins", "required",
]


def validate_manifest(m):
    errors = []
    cols = m.get("columns", [])
    if cols != REQUIRED_COLUMNS:
        errors.append("columns 与九列约定不一致: %r" % (cols,))
    seen = set()
    for f in m.get("features", []):
        fid = f.get("feature_id")
        for c in REQUIRED_COLUMNS:
            if c not in f:
                errors.append("%s 缺列 %s" % (fid, c))
        if fid in seen:
            errors.append("feature_id 重复: %s" % fid)
        seen.add(fid)
        av = f.get("availability", {})
        kind = av.get("kind")
        if kind not in ALLOWED_KINDS:
            errors.append("%s availability.kind 非法: %r" % (fid, kind))
        origins = f.get("allowed_origins", [])
        if kind == "NEVER":
            if origins:
                errors.append("%s 为 NEVER 却声明可用起点" % fid)
            if not av.get("reason"):
                errors.append("%s 为 NEVER 但未写 reason" % fid)
        else:
            if not origins:
                errors.append("%s 无 allowed_origins" % fid)
            for o in origins:
                if o not in ORIGIN_TIMES:
                    errors.append("%s 起点非法: %s" % (fid, o))
        for ds in str(f.get("source_dataset", "")).split(","):
            ds = ds.strip()
            rule = m.get("shift_rules", {}).get(ds)
            if rule and kind != "NEVER" and int(f.get("shift_days", 0)) < int(rule["min_shift_days"]):
                errors.append(
                    "%s 对 %s 的 shift_days=%s 小于下限 %s"
                    % (fid, ds, f.get("shift_days"), rule["min_shift_days"])
                )
    return errors


def feature_available_at(feature, as_of_date):
    """返回该特征在 as_of_date 这批数据上最早可得的时刻；NEVER 返回 None。"""
    av = feature["availability"]
    kind = av["kind"]
    if kind == "NEVER":
        return None
    if kind == "STATIC":
        return at(as_of_date, "00:00")
    if kind == "OBS_LAG":
        return at(as_of_date + timedelta(days=int(av.get("lag_days", 0))), av.get("cutoff", "00:00"))
    if kind == "EVENT":
        if "offset_days" in av:
            return at(as_of_date + timedelta(days=int(av["offset_days"])), av["time"])
        return at(as_of_date, "00:00")
    raise ValueError("未知 availability.kind: %s" % kind)


def latest_as_of(feature, origin_type, target):
    """沿时间往回找到该起点下最晚可得的业务日。"""
    if feature["availability"]["kind"] == "NEVER":
        raise FeatureNotAllowed("%s 永不可入模" % feature["feature_id"])
    origin = origin_timestamp(origin_type, target)
    day = target - timedelta(days=1)
    for _ in range(400):
        ts = feature_available_at(feature, day)
        if ts is not None and ts <= origin:
            return day
        day -= timedelta(days=1)
    raise CannotForecast("找不到可得日: %s" % feature["feature_id"])


def assert_origin_allowed(feature, origin_type):
    if origin_type not in feature.get("allowed_origins", []):
        raise FeatureNotAllowed(
            "%s 不允许在 %s 起点使用（允许: %s）"
            % (feature["feature_id"], origin_type, feature.get("allowed_origins"))
        )


def assert_not_leaked(feature, as_of_date, origin_type, target):
    assert_origin_allowed(feature, origin_type)
    ts = feature_available_at(feature, as_of_date)
    origin = origin_timestamp(origin_type, target)
    if ts is not None and ts > origin:
        raise FeatureLeakageError(
            "%s 在 %s 尚不可得（available_at=%s > origin=%s）"
            % (feature["feature_id"], iso(as_of_date), ts.isoformat(), origin.isoformat())
        )


# ---------------------------------------------------------------- 组合复算
def portfolio_users(contracts, target):
    """目标日有效的代理用户集合（合同 term_start <= D <= term_end）。"""
    return sorted({c["user_id"] for c in contracts
                   if c["term_start"] <= target <= c["term_end"]})


def portfolio_load(history, users, target):
    """按 slot 求组合负荷；缺用户不补零，coverage<0.99 记 PARTIAL。"""
    per_slot = {s: {} for s in SLOTS}
    for u in users:
        day = history.get(u, {}).get(target, {})
        for s in SLOTS:
            v = day.get(s)
            if v is not None:
                per_slot[s][u] = v
    out = []
    for s in SLOTS:
        observed = len(per_slot[s])
        expected = len(users)
        coverage = (observed / expected) if expected else 0.0
        total = sum(per_slot[s].values()) if observed else None
        out.append({
            "slot_no": s,
            "value": total,
            "observed_users": observed,
            "expected_users": expected,
            "coverage": round(coverage, 6),
            "status": "OK" if expected and coverage >= 0.99 else "PARTIAL",
            "missing_users": sorted(set(users) - set(per_slot[s])),
        })
    return out


# ---------------------------------------------------------------- 基线
def available_dates(history_user, as_of, window):
    days = []
    day = as_of
    for _ in range(window * 3):
        if day in history_user:
            vals = history_user[day]
            if any(v is not None for v in vals.values()):
                days.append(day)
        if len(days) >= window:
            break
        day -= timedelta(days=1)
    return sorted(days, reverse=True)


def naive_baselines(history_user, as_of):
    n1 = {s: history_user.get(as_of, {}).get(s) for s in SLOTS}
    days7 = available_dates(history_user, as_of, 7)
    n2 = {}
    for s in SLOTS:
        vals = [history_user[dd][s] for dd in days7 if history_user[dd].get(s) is not None]
        n2[s] = median(vals) if vals else None
    n3 = {}
    same_weekday = [as_of - timedelta(days=7 * k) for k in range(8)]
    same_weekday = [dd for dd in same_weekday if dd in history_user]
    for s in SLOTS:
        vals = [history_user[dd][s] for dd in same_weekday if history_user[dd].get(s) is not None]
        n3[s] = median(vals) if vals else None
    return {"N1": n1, "N2": n2, "N3": n3}


# ---------------------------------------------------------------- 指标
def mae(y, p):
    pairs = [(a, b) for a, b in zip(y, p) if a is not None and b is not None]
    return sum(abs(a - b) for a, b in pairs) / len(pairs) if pairs else None


def rmse(y, p):
    pairs = [(a, b) for a, b in zip(y, p) if a is not None and b is not None]
    return math.sqrt(sum((a - b) ** 2 for a, b in pairs) / len(pairs)) if pairs else None


def metrics(y_true, y_pred, eps):
    pairs = [(a, b) for a, b in zip(y_true, y_pred) if a is not None and b is not None]
    if not pairs:
        return {"n": 0, "mae": None, "rmse": None, "nmae": None, "mape": None,
                "mape_applicable": False, "mape_excluded": 0}
    abs_mean = sum(abs(a) for a, _ in pairs) / len(pairs)
    m = mae([a for a, _ in pairs], [b for _, b in pairs])
    r = rmse([a for a, _ in pairs], [b for _, b in pairs])
    small = [(a, b) for a, b in pairs if abs(a) < eps]
    big = [(a, b) for a, b in pairs if abs(a) >= eps]
    mape_val = None
    if big:
        mape_val = sum(abs(a - b) / abs(a) for a, b in big) / len(big)
    return {
        "n": len(pairs),
        "mae": round(m, 6) if m is not None else None,
        "rmse": round(r, 6) if r is not None else None,
        "nmae": round(m / abs_mean, 6) if abs_mean else None,
        "mape": round(mape_val, 6) if mape_val is not None else None,
        "mape_applicable": bool(big) and (len(small) / len(pairs)) <= 0.10,
        "mape_excluded": len(small),
        "mape_excluded_ratio": round(len(small) / len(pairs), 6),
    }


def picp(y_true, p10, p90):
    pairs = [(a, lo, hi) for a, lo, hi in zip(y_true, p10, p90) if None not in (a, lo, hi)]
    if not pairs:
        return {"picp": None, "avg_width": None, "n": 0}
    inside = sum(1 for a, lo, hi in pairs if lo <= a <= hi)
    width = sum(hi - lo for _, lo, hi in pairs) / len(pairs)
    return {"picp": round(inside / len(pairs), 6), "avg_width": round(width, 6), "n": len(pairs)}


# ---------------------------------------------------------------- 峰平谷
def tariff_segment(month, slot_no):
    """鄂发改价管〔2024〕77 号；返回时段枚举。"""
    if not 1 <= slot_no <= 24:
        raise ValueError("slot_no 越界")
    summer = month in (7, 8)
    if summer:
        if 21 <= slot_no <= 22:
            return "SHARP_PEAK"
        if 17 <= slot_no <= 20 or 23 <= slot_no <= 24:
            return "PEAK"
    else:
        if 19 <= slot_no <= 20:
            return "SHARP_PEAK"
        if 17 <= slot_no <= 18 or 21 <= slot_no <= 24:
            return "PEAK"
    if 7 <= slot_no <= 12 or 15 <= slot_no <= 16:
        return "FLAT"
    if 1 <= slot_no <= 6 or 13 <= slot_no <= 14:
        return "VALLEY"
    raise ValueError("slot %s 未覆盖" % slot_no)


def tariff_ratio(month, segment):
    seasonal = month in (7, 8, 12, 1)
    if segment == "SHARP_PEAK":
        return 2.0 if seasonal else 1.8
    if segment == "PEAK":
        return 1.49
    if segment == "FLAT":
        return 1.0
    if segment == "VALLEY":
        return 0.45 if seasonal else 0.48
    raise ValueError("湖北现行无该时段: %s" % segment)


# ---------------------------------------------------------------- 归因
def check_attribution(text, evidence):
    issues = []
    for w in FORBIDDEN_WORDS:
        if w in text:
            issues.append("FORBIDDEN_WORD:%s" % w)
    if CAUSAL_RE.search(text):
        issues.append("FORBIDDEN_WORD:因")
    if FIXED_SENTENCE not in text:
        issues.append("MISSING_FIXED_SENTENCE")
    for k in ("source", "verified", "window", "confidence"):
        if not evidence.get(k):
            issues.append("MISSING_EVIDENCE:%s" % k)
    if evidence.get("verified") not in ("已核验", "未核验", "缺失"):
        issues.append("BAD_VERIFIED_STATE")
    return {"ok": not issues, "issues": issues}


def unable_to_attribute(target_missing, missing_ratio, out_of_distribution, unshifted_runtime, threshold=0.30):
    reasons = []
    if target_missing:
        reasons.append("TARGET_SOURCE_UNAVAILABLE")
    if missing_ratio is not None and missing_ratio > threshold:
        reasons.append("MISSING_FEATURE_RATIO")
    if out_of_distribution:
        reasons.append("OUT_OF_DISTRIBUTION")
    if unshifted_runtime:
        reasons.append("UNSHIFTED_RUNTIME_FEATURE")
    return {"unable": bool(reasons), "reasons": reasons}


# ---------------------------------------------------------------- 特征装配
def build_feature_matrix(manifest, target, origin_type, history, published=None):
    """装配目标日特征；返回 values/缺失/降级。published: feature_id -> 发布时间。"""
    published = published or {}
    values, missing, degraded = {}, [], []
    for f in manifest["features"]:
        fid = f["feature_id"]
        kind = f["availability"]["kind"]
        if kind == "NEVER":
            continue
        if origin_type not in f.get("allowed_origins", []):
            # 该起点不允许使用：不是缺失，是口径外的特征，直接不入模
            continue
        if f["availability"].get("as_of") == "LATEST_LOAD_DAY":
            as_of = latest_as_of(f, origin_type, target)
            assert_not_leaked(f, as_of, origin_type, target)
            values[fid] = {"as_of": iso(as_of)}
            continue
        ts = published.get(fid)
        if ts is None:
            reason = "FORECAST_NOT_ARCHIVED" if "weather" in fid else "NOT_YET_PUBLISHED"
            if fid in ("f_production_correction",):
                reason = "NOT_PROVIDED"
            missing.append({"feature_id": fid, "missing_reason": reason})
            if f.get("required"):
                raise CannotForecast("必需特征缺失: %s (%s)" % (fid, reason))
            degraded.append({"feature_id": fid, "action": "DROP_FEATURE", "missing_reason": reason})
            continue
        if ts > origin_timestamp(origin_type, target):
            raise FeatureLeakageError("%s 发布于 %s，晚于起点" % (fid, ts.isoformat()))
        values[fid] = {"published_at": ts.isoformat()}
    return {"values": values, "missing": missing, "degraded": degraded}


def feature_set_hash(manifest, feature_ids):
    ids = ",".join(sorted(feature_ids))
    return hashlib.sha256(ids.encode("utf-8")).hexdigest()[:16]


def downsample_96(points):
    if len(points) != 96:
        raise ValueError("需要 96 个点")
    hourly = [sum(points[i * 4:(i + 1) * 4]) / 4.0 for i in range(24)]
    return {"values": hourly, "source_resolution": "15min", "derived_to": "hourly"}


# ---------------------------------------------------------------- 样例
def generate_samples(seed=20261105):
    import random
    rng = random.Random(seed)
    start = date(2026, 9, 1)
    users = ["U000001", "U000002", "U000003"]
    os.makedirs(SAMPLES, exist_ok=True)
    rows = []
    for ui, u in enumerate(users):
        base = 4.0 + ui * 1.5
        for k in range(56):
            day = start + timedelta(days=k)
            weekday = day.weekday()
            for s in SLOTS:
                shape = 0.7 + 0.5 * math.sin((s - 8) / 24.0 * 2 * math.pi)
                if weekday >= 5:
                    shape *= 0.85
                val = base * shape + rng.uniform(-0.2, 0.2)
                rows.append([u, iso(day), s, "%.3f" % max(val, 0.0)])
    with open(HISTORY_CSV, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["user_id", "business_date", "slot_no", "energy_mwh"])
        w.writerows(rows)
    return len(rows)


def load_history_csv(path=HISTORY_CSV):
    hist = {}
    with open(path, "r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            u = row["user_id"]
            day = d(row["business_date"])
            hist.setdefault(u, {}).setdefault(day, {})[int(row["slot_no"])] = float(row["energy_mwh"])
    return hist


# ---------------------------------------------------------------- 自检
def run_checks():
    results = []
    m = load_manifest()
    fm = feature_map(m)

    # E1 台账九列与 shift 规则
    errs = validate_manifest(m)
    never = [f["feature_id"] for f in m["features"] if f["availability"]["kind"] == "NEVER"]
    results.append(("E1", not errs and len(m["features"]) == 22 and set(never) == {"f_rt_price", "f_monthly_settlement"},
                    {"features": len(m["features"]), "never": never, "errors": errs}))

    # E2 穿越拦截
    bad = json.loads(json.dumps(fm["f_load_same_hour_lag1"]))
    bad["availability"]["lag_days"] = 0
    target = date(2026, 10, 20)
    e2 = {"leak": None, "allowed": None}
    try:
        assert_not_leaked(bad, target - timedelta(days=1), "P_CURVE", target)
    except FeatureLeakageError as exc:
        e2["leak"] = "FeatureLeakageError"
    try:
        assert_origin_allowed(fm["f_system_load_forecast"], "P_CURVE")
    except FeatureNotAllowed:
        e2["allowed"] = "FeatureNotAllowed"
    results.append(("E2", e2["leak"] == "FeatureLeakageError" and e2["allowed"] == "FeatureNotAllowed", e2))

    # E3 三起点与最近可得日
    days = {o: iso(latest_as_of(fm["f_load_same_hour_lag1"], o, target)) for o in ORIGIN_TIMES}
    d09 = {o: iso(latest_as_of(fm["f_settlement_energy"], o, target)) for o in ORIGIN_TIMES}
    origins = {o: origin_timestamp(o, target).isoformat() for o in ORIGIN_TIMES}
    ok3 = (days == {"P_CURVE": "2026-10-17", "P_BID": "2026-10-18", "P_REVIEW": "2026-10-18"}
           and d09["P_CURVE"] == "2026-10-16")
    results.append(("E3", ok3, {"load_as_of": days, "settle_as_of": d09, "origins": origins}))

    # E4 组合复算：缺一个用户不补零
    users = ["U1", "U2", "U3"]
    contracts = [{"user_id": u, "term_start": date(2026, 10, 1), "term_end": date(2026, 10, 31)} for u in users]
    hist = {u: {target: {s: 10.0 for s in SLOTS}} for u in users}
    hist["U3"][target][5] = None
    active = portfolio_users(contracts, target)
    port = portfolio_load(hist, active, target)
    s5 = [r for r in port if r["slot_no"] == 5][0]
    s4 = [r for r in port if r["slot_no"] == 4][0]
    ok4 = (active == users and s5["value"] == 20.0 and s5["coverage"] == round(2 / 3, 6)
           and s5["status"] == "PARTIAL" and s5["missing_users"] == ["U3"]
           and s4["value"] == 30.0 and s4["status"] == "OK")
    results.append(("E4", ok4, {"slot5": s5, "slot4": s4}))

    # E5 三基线口径
    as_of = date(2026, 10, 17)
    h = {}
    for k in range(56):
        day = as_of - timedelta(days=k)
        h[day] = {s: float(100 + (k % 7)) for s in SLOTS}
    h[as_of] = {s: 50.0 for s in SLOTS}
    bl = naive_baselines(h, as_of)
    ok5 = (bl["N1"][1] == 50.0 and bl["N2"][1] == median([50.0] + [float(100 + (k % 7)) for k in range(1, 7)])
           and bl["N3"][1] == median([50.0] + [float(100 + (k % 7)) for k in (7, 14, 21, 28, 35, 42, 49)]))
    results.append(("E5", ok5, {"N1": bl["N1"][1], "N2": bl["N2"][1], "N3": bl["N3"][1]}))

    # E6 缺失与降级
    e6 = {"degraded": None, "required": None}
    pub = {fid: None for fid in fm}
    pub["f_agency_user_set"] = datetime(2026, 10, 1, 9, 0)
    pub["f_contract_energy"] = datetime(2026, 10, 1, 9, 0)
    pub["f_calendar_day_type"] = datetime(2026, 1, 1, 0, 0)
    pub["f_agency_change"] = datetime(2026, 10, 1, 9, 0)
    mat = build_feature_matrix(m, target, "P_CURVE", {}, pub)
    w = [x for x in mat["degraded"] if x["feature_id"] == "f_weather_forecast_temp"]
    try:
        pub2 = dict(pub)
        pub2["f_agency_user_set"] = None
        build_feature_matrix(m, target, "P_CURVE", {}, pub2)
    except CannotForecast:
        e6["required"] = "CannotForecast"
    e6["degraded"] = w[0] if w else None
    results.append(("E6", bool(w) and w[0]["missing_reason"] == "FORECAST_NOT_ARCHIVED"
                    and w[0]["action"] == "DROP_FEATURE" and e6["required"] == "CannotForecast", e6))

    # E7 G2 归因
    good = ("气温预报偏高，%s。来源：D11 预报（已核验）；时间窗：D-2 至 D-1；"
            "置信度：P10–P90 区间。" % FIXED_SENTENCE)
    bad_text = "因为高温导致负荷上升，" + FIXED_SENTENCE
    ev = {"source": "D11", "verified": "已核验", "window": "D-2..D-1", "confidence": "P10-P90"}
    r_good = check_attribution(good, ev)
    r_bad = check_attribution(bad_text, ev)
    r_miss = check_attribution(FIXED_SENTENCE, {})
    unable = unable_to_attribute(False, 0.42, False, False)
    results.append(("E7", r_good["ok"] and not r_bad["ok"] and not r_miss["ok"]
                    and unable["unable"] and "MISSING_FEATURE_RATIO" in unable["reasons"],
                    {"good": r_good, "bad": r_bad["issues"], "missing": r_miss["issues"],
                     "unable": unable}))

    # E8 指标与区间
    y = [10.0, 20.0, 30.0, 0.4, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0]
    p = [12.0, 18.0, 33.0, 1.0, 38.0, 52.0, 57.0, 72.0, 78.0, 94.0]
    met = metrics(y, p, eps=1.0)
    pic = picp(y, [8, 17, 28, 0, 35, 45, 55, 65, 75, 85], [13, 22, 34, 2, 44, 55, 65, 75, 85, 95])
    ok8 = (met["n"] == 10 and met["mae"] == round(22.6 / 10, 6)
           and met["mape_excluded"] == 1 and met["mape_applicable"] is True
           and pic["picp"] == 1.0)
    results.append(("E8", ok8, {"metrics": met, "picp": pic}))

    # E9 峰平谷
    july = [tariff_segment(7, s) for s in SLOTS]
    march = [tariff_segment(3, s) for s in SLOTS]
    count = lambda xs: {k: xs.count(k) for k in set(xs)}
    ok9 = (count(july) == {"SHARP_PEAK": 2, "PEAK": 6, "FLAT": 8, "VALLEY": 8}
           and count(march) == {"SHARP_PEAK": 2, "PEAK": 6, "FLAT": 8, "VALLEY": 8}
           and july[18] == "PEAK" and march[18] == "SHARP_PEAK"
           and tariff_ratio(7, "SHARP_PEAK") == 2.0 and tariff_ratio(3, "SHARP_PEAK") == 1.8
           and tariff_ratio(7, "VALLEY") == 0.45 and tariff_ratio(3, "VALLEY") == 0.48)
    results.append(("E9", ok9, {"july": count(july), "march": count(march),
                                "ratio_7_sharp": tariff_ratio(7, "SHARP_PEAK"),
                                "ratio_3_sharp": tariff_ratio(3, "SHARP_PEAK")}))

    # E10 96→24 降采样
    ds = downsample_96([float(i + 1) for i in range(96)])
    ok10 = (len(ds["values"]) == 24 and ds["values"][0] == 2.5 and ds["values"][23] == 94.5
            and ds["source_resolution"] == "15min" and ds["derived_to"] == "hourly")
    results.append(("E10", ok10, {"first": ds["values"][0], "last": ds["values"][23],
                                  "tags": [ds["source_resolution"], ds["derived_to"]]}))

    # 样例文件体检（若存在）
    sample_note = {}
    if os.path.exists(HISTORY_CSV):
        hist_csv = load_history_csv(HISTORY_CSV)
        rows = sum(len(v) for u in hist_csv for v in hist_csv[u].values())
        sample_note = {"users": len(hist_csv), "rows": rows}

    return results, sample_note


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--generate-samples", action="store_true")
    args = ap.parse_args(argv)
    if args.generate_samples:
        n = generate_samples()
        print("samples written: %d rows -> %s" % (n, HISTORY_CSV))
    if args.verify:
        results, note = run_checks()
        failed = 0
        for name, ok, detail in results:
            print("[%s] %s" % ("PASS" if ok else "FAIL", name))
            print("      " + json.dumps(detail, ensure_ascii=False, default=str))
            if not ok:
                failed += 1
        print("samples: " + json.dumps(note, ensure_ascii=False))
        print("%d/%d PASS" % (len(results) - failed, len(results)))
        return 1 if failed else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
