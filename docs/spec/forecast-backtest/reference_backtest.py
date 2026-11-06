"""朴素基线与滚动回测框架（#31）。

从第一性原理出发：
1. 预测 = 用「起点时刻已经可得」的历史外推目标日。可得性由数据发布时延决定，
   不由日历经决定。同一目标日，不同起点（P_CURVE/P_BID/P_REVIEW）的可得日不同。
2. 回测 = 滚动样本外。每个切点只看切点时刻可得的数据，逐期算误差，
   最后对「所有期 × 所有时刻」等权汇总（不是各期指标再平均）。
3. 指标口径只有一份，基线与后续模型共用；缺测点不插值、不补 0，直接不计入误差统计。
4. 一切可调项（切点、窗口、评估期长度、门槛）写在配置里，可复现。
5. 日历是分层维度，不是跳过开关：电负荷每天都有、每天都要预测，
   但工作日 / 休息日 / 法定节假日 / 调休上班日的误差必须分开看。
"""
import argparse
import csv
import hashlib
import json
import math
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
SAMPLES = ROOT / "samples"
OUT = ROOT / "out"
CONFIG_PATH = ROOT / "backtest.config.json"
HISTORY_CSV = SAMPLES / "load_history_backtest.csv"
CALENDAR_CSV = SAMPLES / "business_calendar_2026.csv"
VERIFICATION_PATH = ROOT / "verification.txt"

SLOTS = list(range(1, 25))
ORIGIN_OFFSETS = {"P_CURVE": (-2, "12:00"), "P_BID": (-1, "08:00"), "P_REVIEW": (-1, "20:00")}
BASELINES = ["N1", "N2", "N3", "N3a"]
BUSINESS_DAY_TYPES = ["普通工作日", "调休上班日"]


class BacktestError(RuntimeError):
    """回测框架自检用异常。"""


class CannotForecast(RuntimeError):
    """找不到任何可得日，无法出基线。"""


class LeakageError(RuntimeError):
    """使用了起点时刻不可得的数据。"""


# ---------------------------------------------------------------- 基础工具
def d(s):
    return date.fromisoformat(s)


def iso(x):
    return x.isoformat()


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


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_store_module():
    """引用仓内存储层参考实现（#25）。"""
    p = str(REPO / "docs" / "spec" / "data-storage")
    if p not in sys.path:
        sys.path.insert(0, p)
    import reference_store
    return reference_store


def load_forecast_module():
    """引用仓内预测域参考实现（#45），仅用于口径交叉校验。"""
    p = str(REPO / "docs" / "spec" / "load-forecast")
    if p not in sys.path:
        sys.path.insert(0, p)
    import reference_forecast
    return reference_forecast


# ---------------------------------------------------------------- 可得性
def origin_timestamp(origin_type, target):
    if origin_type not in ORIGIN_OFFSETS:
        raise ValueError("未知起点: %s" % origin_type)
    off, hhmm = ORIGIN_OFFSETS[origin_type]
    day = target + timedelta(days=off)
    h, m = [int(v) for v in hhmm.split(":")]
    return datetime(day.year, day.month, day.day, h, m)


def available_at(day, shift_days, cutoff):
    """某业务日的实测数据在存储层可被读到的时刻。"""
    h, m = [int(v) for v in cutoff.split(":")]
    dd = day + timedelta(days=int(shift_days))
    return datetime(dd.year, dd.month, dd.day, h, m)


def latest_available_date(target, origin_type, shift_days=1, cutoff="00:00"):
    """目标日 target 在该起点下，理论上最晚可用的业务日（不考虑是否有数据）。"""
    origin = origin_timestamp(origin_type, target)
    day = target - timedelta(days=1)
    for _ in range(400):
        if available_at(day, shift_days, cutoff) <= origin:
            return day
        day -= timedelta(days=1)
    raise CannotForecast("找不到可得日: target=%s origin=%s" % (iso(target), origin_type))


def assert_no_leak(days_used, target, origin_type, shift_days, cutoff):
    """断言用到的历史日都在起点时刻可得。"""
    origin = origin_timestamp(origin_type, target)
    for day in days_used:
        if available_at(day, shift_days, cutoff) > origin:
            raise LeakageError("历史日 %s 在 %s 起点不可得" % (iso(day), origin_type))


# ---------------------------------------------------------------- 历史序列
def days_with_data(series):
    """有实测数据的业务日：当天至少有一个非空时点（与 #45 available_dates 同口径）。"""
    return sorted(dd for dd, vals in series.items() if any(v is not None for v in vals.values()))


def anchor_date(series, target, origin_type, shift_days=1, cutoff="00:00"):
    """最近可得日 = 理论可得日边界内、且当天确有数据的最大业务日。"""
    bound = latest_available_date(target, origin_type, shift_days, cutoff)
    cands = [dd for dd in days_with_data(series) if dd <= bound]
    if not cands:
        return None, bound
    return max(cands), bound


def naive_baselines(series, target, origin_type, cfg):
    """三个朴素基线 + 一个锚点对照变体。series: {date: {slot: value}}。"""
    b = cfg["backtest"]
    av = availability_cfg(cfg)
    shift, cutoff = av["shift_days"], av["cutoff"]
    have = days_with_data(series)
    have_set = set(have)
    anchor, bound = anchor_date(series, target, origin_type, shift, cutoff)
    if anchor is None:
        return {"N1": {}, "N2": {}, "N3": {}, "N3a": {}, "N2_p10": {}, "N2_p90": {},
                "_meta": {"anchor": None, "bound": iso(bound)}}

    # N1 最近可得日同刻
    n1 = {s: series[anchor].get(s) for s in SLOTS}

    # N2 最近 N 个可得日同刻中位数（并给出该窗口的经验 P10/P90 区间）
    window = int(b.get("n2_window_days", 7))
    min_pts = int(b.get("interval_min_points", 3))
    avail = [dd for dd in have if dd <= anchor][-window:]
    n2 = {}
    n2_p10 = {}
    n2_p90 = {}
    for s in SLOTS:
        vals = [series[dd].get(s) for dd in avail if series[dd].get(s) is not None]
        n2[s] = median(vals) if vals else None
        if len(vals) >= min_pts:
            n2_p10[s] = percentile(vals, 10)
            n2_p90[s] = percentile(vals, 90)
        else:
            n2_p10[s] = None
            n2_p90[s] = None

    # N3 同星期几最近 N 周中位数（锚在目标日的星期几上）
    weeks = int(b.get("n3_same_weekday_count", 8))
    same_wd = []
    for k in range(1, weeks + 1):
        dd = target - timedelta(days=7 * k)
        if dd in have_set and dd <= bound:
            same_wd.append(dd)
    n3 = {}
    for s in SLOTS:
        vals = [series[dd].get(s) for dd in same_wd if series[dd].get(s) is not None]
        n3[s] = median(vals) if vals else None

    # N3a 对照变体：锚在最近可得日的星期几上（#45 参考实现的锚点口径）
    same_wd_a = []
    for k in range(1, weeks + 1):
        dd = anchor - timedelta(days=7 * k)
        if dd in have_set and dd <= bound:
            same_wd_a.append(dd)
    n3a = {}
    for s in SLOTS:
        vals = [series[dd].get(s) for dd in same_wd_a if series[dd].get(s) is not None]
        n3a[s] = median(vals) if vals else None

    assert_no_leak(list(avail) + list(same_wd) + list(same_wd_a) + [anchor], target, origin_type, shift, cutoff)
    return {"N1": n1, "N2": n2, "N3": n3, "N3a": n3a,
            "N2_p10": n2_p10, "N2_p90": n2_p90,
            "_meta": {"anchor": iso(anchor), "bound": iso(bound),
                      "n2_days": [iso(x) for x in avail],
                      "n3_days": [iso(x) for x in same_wd],
                      "n3a_days": [iso(x) for x in same_wd_a]}}


# ---------------------------------------------------------------- 指标（L11）
def metrics(y_true, y_pred, eps):
    """与 load-forecast 参考实现 metrics() 完全同口径。"""
    pairs = [(a, b) for a, b in zip(y_true, y_pred) if a is not None and b is not None]
    if not pairs:
        return {"n": 0, "mae": None, "rmse": None, "nmae": None, "mape": None,
                "mape_applicable": False, "mape_excluded": 0}
    abs_mean = sum(abs(a) for a, _ in pairs) / len(pairs)
    mae = sum(abs(a - b) for a, b in pairs) / len(pairs)
    rmse = math.sqrt(sum((a - b) ** 2 for a, b in pairs) / len(pairs))
    small = [(a, b) for a, b in pairs if abs(a) < eps]
    big = [(a, b) for a, b in pairs if abs(a) >= eps]
    mape_val = None
    if big:
        mape_val = sum(abs(a - b) / abs(a) for a, b in big) / len(big)
    return {
        "n": len(pairs),
        "mae": round(mae, 6),
        "rmse": round(rmse, 6),
        "nmae": round(mae / abs_mean, 6) if abs_mean else None,
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


# ---------------------------------------------------------------- 配置
def load_config(path=CONFIG_PATH):
    return load_json(path)


def availability_cfg(cfg):
    """可得性参数只留一份来源：availability 优先，回退 backtest。"""
    a = cfg.get("availability", {})
    b = cfg.get("backtest", {})
    return {
        "shift_days": int(a.get("shift_days", b.get("shift_days", 1))),
        "cutoff": a.get("cutoff", b.get("availability_cutoff", "00:00")),
    }


def business_day_types(cfg):
    return list(cfg.get("calendar", {}).get("business_day_types", BUSINESS_DAY_TYPES))


def mape_eps(cfg, hist, first_target):
    """MAPE 近零剔除阈值 ε。

    默认取首个切点之前历史负荷（全体用户合并）的 P1，全程固定不变：
    既写死可复现，又不会因为用到切点之后的信息而泄漏。
    """
    m = cfg.get("metrics", {})
    fixed = m.get("mape_eps_fixed")
    if fixed is not None:
        return float(fixed)
    pct = float(m.get("mape_eps_percentile", 1.0))
    vals = []
    for series in hist.values():
        for day, slots in series.items():
            if first_target is not None and day >= first_target:
                continue
            vals.extend(v for v in slots.values() if v is not None)
    if not vals:
        return 0.0
    return percentile(vals, pct)


def per_user_p1(hist, first_target):
    out = {}
    for u, series in hist.items():
        vals = []
        for day, slots in series.items():
            if first_target is not None and day >= first_target:
                continue
            vals.extend(v for v in slots.values() if v is not None)
        out[u] = round(percentile(vals, 1.0), 6) if vals else None
    return out


# ---------------------------------------------------------------- 日历
def load_calendar(path=CALENDAR_CSV):
    cal = {}
    with open(path, "r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            day = d(row["日期"])
            cal[day] = {"weekday": row["星期"], "day_type": row["日类型"],
                        "holiday": row.get("节假日名称") or ""}
    return cal


def calendar_day_type(cal, day):
    return cal.get(day, {}).get("day_type", "UNKNOWN")


# ---------------------------------------------------------------- 数据载入
def load_history_csv(path=HISTORY_CSV):
    hist = {}
    with open(path, "r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            u = row["user_id"]
            day = d(row["business_date"])
            raw = row["energy_mwh"]
            val = float(raw) if raw not in ("", None) else None
            hist.setdefault(u, {}).setdefault(day, {})[int(row["slot_no"])] = val
    return hist


def load_history_from_store(dkey, dataset_key=None, as_of=None):
    """从仓内存储层（#25）读负荷历史，返回与 load_history_csv 同构的数据。

    真实入库数据到位后走这条通道：口径不变，回测框架不感知数据来源。
    """
    store = load_store_module()
    contract = store.load_json(REPO / "docs" / "spec" / "data-contract" / "contract.schema.json")
    policy = store.load_json(REPO / "docs" / "spec" / "data-storage" / "storage.schema.json")
    conn = store.connect(path=":memory:")
    store.build_schema(conn, contract, policy)
    key = dataset_key or dkey
    rows = store.export_rows(conn, policy, key, limit=100000)
    hist = {}
    for r in rows:
        u = r.get("user_id")
        day = d(r["business_date"])
        slot = int(r.get("slot_no") or 0)
        val = r.get("energy_mwh")
        if u is None or not slot:
            continue
        hist.setdefault(u, {}).setdefault(day, {})[slot] = float(val) if val is not None else None
    return hist


# ---------------------------------------------------------------- 回测主循环
def rolling_targets(cfg):
    """滚动样本外的切点序列（每个切点=一个目标日的 24h 预测）。"""
    b = cfg["backtest"]
    start = d(b["eval_start"])
    end = d(b["eval_end"])
    step = int(b.get("step_days", 1))
    out = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=step)
    return out


def run_backtest(cfg, hist, cal):
    """跑一轮滚动样本外回测，返回逐点明细与切点信息。"""
    b = cfg["backtest"]
    origin_types = list(cfg.get("availability", {}).get("origin_types", ["P_CURVE"]))
    targets = rolling_targets(cfg)
    if cfg.get("calendar", {}).get("skip_non_business_days"):
        bdt = business_day_types(cfg)
        targets = [t for t in targets if calendar_day_type(cal, t) in bdt]
    users = sorted(hist.keys())
    eps = mape_eps(cfg, hist, targets[0] if targets else None)
    rows = []
    for t in targets:
        dtype = calendar_day_type(cal, t)
        for u in users:
            series = hist[u]
            for ot in origin_types:
                bl = naive_baselines(series, t, ot, cfg)
                for s in SLOTS:
                    y = series.get(t, {}).get(s)
                    for name in BASELINES:
                        rows.append({
                            "period": iso(t), "user_id": u, "origin_type": ot, "baseline": name,
                            "slot_no": s, "y_true": y, "y_pred": bl[name].get(s),
                            "p10": (bl.get("N2_p10") or {}).get(s) if name == "N2" else None,
                            "p90": (bl.get("N2_p90") or {}).get(s) if name == "N2" else None,
                            "day_type": dtype,
                        })
    return {"rows": rows, "targets": targets, "users": users, "eps": eps,
            "origin_types": origin_types, "hist": hist}


# ---------------------------------------------------------------- 汇总
def _mean(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 6)


def macro_summary(period_groups, eps):
    """对照口径：各期指标再平均（macro）。"""
    per = [metrics([r["y_true"] for r in g], [r["y_pred"] for r in g], eps) for g in period_groups]
    return {"n_periods": len(per),
            "mae": _mean([m["mae"] for m in per]),
            "rmse": _mean([m["rmse"] for m in per]),
            "nmae": _mean([m["nmae"] for m in per]),
            "mape": _mean([m["mape"] for m in per]),
            "mape_applicable": all(m["mape_applicable"] for m in per) if per else False}


def summarize_group(rows, eps):
    """主口径：所有期 × 所有时刻等权汇总（一次算，不做各期平均）。"""
    y = [r["y_true"] for r in rows]
    p = [r["y_pred"] for r in rows]
    m = metrics(y, p, eps)
    expected = len(rows)
    truth_ok = sum(1 for r in rows if r["y_true"] is not None)
    out = {
        "n_expected": expected,
        "n_points": m["n"],
        "truth_coverage": round(truth_ok / expected, 6) if expected else None,
        "scored_coverage": round(m["n"] / expected, 6) if expected else None,
        "mae": m["mae"], "rmse": m["rmse"], "nmae": m["nmae"],
        "mape": m["mape"], "mape_applicable": m["mape_applicable"],
        "mape_excluded": m["mape_excluded"], "mape_excluded_ratio": m.get("mape_excluded_ratio"),
    }
    if rows and all(r["baseline"] == "N2" for r in rows):
        pc = picp(y, [r["p10"] for r in rows], [r["p90"] for r in rows])
        out["picp"] = pc["picp"]
        out["avg_width"] = pc["avg_width"]
        out["interval_n"] = pc["n"]
        out["interval_source"] = "N2_WINDOW_EMPIRICAL"
    else:
        out["picp"] = None
        out["avg_width"] = None
        out["interval_n"] = 0
        out["interval_source"] = "NOT_APPLICABLE_POINT_FORECAST"
    return out


def build_error_table(result):
    """误差表：起点 × 基线 × 分层。"""
    rows = result["rows"]
    eps = result["eps"]
    groups = {}
    for r in rows:
        groups.setdefault((r["origin_type"], r["baseline"], "ALL"), []).append(r)
        groups.setdefault((r["origin_type"], r["baseline"], r["day_type"]), []).append(r)
    table = []
    for key in sorted(groups):
        ot, name, stratum = key
        g = groups[key]
        summ = summarize_group(g, eps)
        per_period = {}
        for r in g:
            per_period.setdefault(r["period"], []).append(r)
        mac = macro_summary(list(per_period.values()), eps)
        table.append({
            "origin_type": ot, "baseline": name, "stratum": stratum,
            "n_periods": mac["n_periods"],
            "n_expected": summ["n_expected"], "n_points": summ["n_points"],
            "truth_coverage": summ["truth_coverage"], "scored_coverage": summ["scored_coverage"],
            "mae": summ["mae"], "rmse": summ["rmse"], "nmae": summ["nmae"],
            "mape": summ["mape"], "mape_applicable": summ["mape_applicable"],
            "mape_excluded": summ["mape_excluded"], "mape_excluded_ratio": summ["mape_excluded_ratio"],
            "picp": summ["picp"], "avg_width": summ["avg_width"], "interval_source": summ["interval_source"],
            "macro_mae": mac["mae"], "macro_rmse": mac["rmse"], "macro_nmae": mac["nmae"], "macro_mape": mac["mape"],
        })
    return table


def table_digest(table):
    payload = json.dumps(table, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 输出
def write_outputs(cfg, result, table, out_dir=OUT):
    out_dir = Path(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    detail_path = out_dir / "backtest_period_rows.csv"
    with open(detail_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["period", "user_id", "origin_type", "baseline", "slot_no",
                    "y_true", "y_pred", "p10", "p90", "day_type"])
        for r in result["rows"]:
            w.writerow([r["period"], r["user_id"], r["origin_type"], r["baseline"], r["slot_no"],
                        "" if r["y_true"] is None else r["y_true"],
                        "" if r["y_pred"] is None else r["y_pred"],
                        "" if r["p10"] is None else r["p10"],
                        "" if r["p90"] is None else r["p90"],
                        r["day_type"]])
    table_path = out_dir / "backtest_error_table.csv"
    cols = ["origin_type", "baseline", "stratum", "n_periods", "n_expected", "n_points",
            "truth_coverage", "scored_coverage", "mae", "rmse", "nmae", "mape",
            "mape_applicable", "mape_excluded", "mape_excluded_ratio",
            "picp", "avg_width", "interval_source",
            "macro_mae", "macro_rmse", "macro_nmae", "macro_mape"]
    with open(table_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for row in table:
            w.writerow(row)
    summary = {
        "config": cfg,
        "data_label": cfg.get("data", {}).get("data_label"),
        "mape_eps": round(result["eps"], 6),
        "mape_eps_per_user_p1": per_user_p1(
            result.get("hist", {}),
            result["targets"][0] if result["targets"] else None),
        "n_periods": len(result["targets"]),
        "periods": [iso(t) for t in result["targets"]],
        "users": result["users"],
        "origin_types": result["origin_types"],
        "table_digest": table_digest(table),
        "primary": [r for r in table if r["stratum"] == "ALL"],
    }
    summary_path = out_dir / "backtest_summary.json"
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, default=str)
    return {"detail": detail_path, "table": table_path, "summary": summary_path}


# ---------------------------------------------------------------- 样本生成
def generate_samples(seed=20261106):
    """生成回测用合成历史负荷 + 业务日历联动 + 确定性缺测点。

    合成而非真实：真实入库数据未到位（客户样本 Q02 未闭环），
    因此产出物一律标 SAMPLE-SYNTHETIC，不得当作公司真实数据引用。
    """
    import random
    rng = random.Random(seed)
    cal = load_calendar(CALENDAR_CSV)
    start = date(2026, 6, 1)
    end = date(2026, 10, 31)
    users = ["U000001", "U000002", "U000003"]
    rows = []
    missing = []
    day = start
    while day <= end:
        dtype = calendar_day_type(cal, day)
        scale = 1.0
        if dtype == "法定节假日":
            scale = 0.78
        elif dtype == "普通休息日":
            scale = 0.85
        elif dtype == "调休上班日":
            scale = 1.05
        seasonal = 1.0 + 0.08 * math.sin((day - start).days / 45.0)
        for ui, u in enumerate(users):
            base = 4.0 + ui * 1.5
            for s in SLOTS:
                if u == "U000003" and day == date(2026, 9, 15):
                    missing.append([u, iso(day), s, "SOURCE_UNAVAILABLE"])
                    continue
                if u == "U000002" and day == date(2026, 10, 5) and 12 <= s <= 19:
                    missing.append([u, iso(day), s, "SOURCE_UNAVAILABLE"])
                    continue
                if u == "U000001" and day == date(2026, 10, 20) and s in (3, 4):
                    missing.append([u, iso(day), s, "SOURCE_UNAVAILABLE"])
                    continue
                shape = 0.7 + 0.5 * math.sin((s - 8) / 24.0 * 2 * math.pi)
                val = base * shape * scale * seasonal + rng.uniform(-0.2, 0.2)
                rows.append([u, iso(day), s, "%.3f" % max(val, 0.0)])
        day += timedelta(days=1)
    os.makedirs(SAMPLES, exist_ok=True)
    with open(HISTORY_CSV, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["user_id", "business_date", "slot_no", "energy_mwh"])
        w.writerows(rows)
    with open(SAMPLES / "missing_points.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["user_id", "business_date", "slot_no", "missing_reason"])
        w.writerows(missing)
    return {"rows": len(rows), "missing": len(missing)}


# ---------------------------------------------------------------- 自检
def run_checks():
    results = []
    cfg = load_config()
    hist = load_history_csv()
    cal = load_calendar()

    # E1 可得性反推：起点不同 -> 最近可得日不同
    target = date(2026, 10, 20)
    bounds = {ot: iso(latest_available_date(target, ot, 1, "00:00")) for ot in ORIGIN_OFFSETS}
    shifted = iso(latest_available_date(target, "P_CURVE", 2, "00:00"))
    e1 = {"bounds": bounds, "shift2_curve": shifted,
          "origins": {ot: origin_timestamp(ot, target).isoformat() for ot in ORIGIN_OFFSETS}}
    ok1 = (bounds == {"P_CURVE": "2026-10-17", "P_BID": "2026-10-18", "P_REVIEW": "2026-10-18"}
           and shifted == "2026-10-16")
    results.append(("E1", ok1, e1))

    # E2 三基线手算一致（手算值写死在校验里）
    tgt = date(2026, 10, 20)          # 周二
    series = {
        date(2026, 10, 19): {1: 99.0},                                  # 晚于可得边界，不可用
        date(2026, 10, 18): {1: 20.0},                                  # 周日
        date(2026, 10, 17): {1: 30.0},                                  # 周六 = 最近可得日
        date(2026, 10, 16): {1: 40.0}, date(2026, 10, 15): {1: 50.0},
        date(2026, 10, 14): {1: 60.0}, date(2026, 10, 13): {1: 70.0},
        date(2026, 10, 12): {1: 80.0}, date(2026, 10, 11): {1: 90.0},
        date(2026, 10, 10): {1: 91.0}, date(2026, 10, 3): {1: 102.0},
        date(2026, 9, 26): {1: 113.0}, date(2026, 9, 19): {1: 124.0},
        date(2026, 9, 12): {1: 135.0}, date(2026, 9, 5): {1: 146.0},
        date(2026, 8, 29): {1: 157.0}, date(2026, 8, 22): {1: 168.0},
        date(2026, 10, 6): {1: 61.0}, date(2026, 9, 29): {1: 72.0},
        date(2026, 9, 22): {1: 83.0}, date(2026, 9, 15): {1: 94.0},
        date(2026, 9, 8): {1: 105.0}, date(2026, 9, 1): {1: 116.0},
        date(2026, 8, 25): {1: 127.0},
    }
    bl = naive_baselines(series, tgt, "P_CURVE", cfg)
    n1v = bl["N1"].get(1)
    n2v = bl["N2"].get(1)
    n3v = bl["N3"].get(1)
    n3av = bl["N3a"].get(1)
    e2 = {"N1": n1v, "N2": n2v, "N3": n3v, "N3a": n3av,
          "anchor": bl["_meta"]["anchor"], "n2_days": bl["_meta"]["n2_days"],
          "n3_days": bl["_meta"]["n3_days"], "n3a_days": bl["_meta"]["n3a_days"]}
    # 手算：N1=30；N2=median(30..90)=60；N3=median(61,70,72,83,94,105,116,127)=88.5；N3a=median(91..168)=129.5
    ok2 = (n1v == 30.0 and n2v == 60.0 and n3v == 88.5 and n3av == 129.5)
    results.append(("E2", ok2, e2))

    # E3 缺测点：不插值、不补 0、不计入误差统计
    m3 = metrics([10.0, None, 30.0], [10.0, 20.0, 30.0], 0.0)
    miss_series = {
        date(2026, 10, 17): {1: 30.0, 2: None},
        date(2026, 10, 16): {1: 40.0, 2: 5.0},
        date(2026, 10, 15): {1: 50.0, 2: None},
    }
    bl3 = naive_baselines(miss_series, tgt, "P_CURVE", cfg)
    all_none_day = {date(2026, 10, 17): {1: None, 2: None}, date(2026, 10, 16): {1: 40.0}}
    days_ok = days_with_data(all_none_day)
    e3 = {"metrics_n": m3["n"], "metrics_mae": m3["mae"], "n1_slot2_is_none": bl3["N1"].get(2) is None,
          "n2_slot2": bl3["N2"].get(2), "days_with_data": [iso(x) for x in days_ok]}
    ok3 = (m3["n"] == 2 and m3["mae"] == 0.0 and bl3["N1"].get(2) is None
           and bl3["N2"].get(2) == 5.0 and len(days_ok) == 1)
    results.append(("E3", ok3, e3))

    # E4 指标口径与 #45 参考实现逐值一致
    fm = load_forecast_module()
    cases = [
        ([1.0, 2.0, 3.0], [1.0, 2.5, 3.0], 0.5),
        ([0.0, 1.0, 2.0], [0.5, 1.0, 2.5], 0.5),
        ([None, None], [1.0, 2.0], 0.5),
        ([0.01, 0.02, 0.03, 1.0], [0.02, 0.03, 0.04, 1.0], 0.5),
        ([10.0, 20.0], [12.0, 18.0], 0.5),
    ]
    diffs = []
    for y, p, eps in cases:
        a = metrics(y, p, eps)
        b = fm.metrics(y, p, eps)
        if a != b:
            diffs.append({"y": y, "p": p, "here": a, "forecast": b})
    e4 = {"cases": len(cases), "diffs": diffs}
    results.append(("E4", not diffs, e4))

    # E5 泄漏检测：越界即报错；注入未来数据不改变结果
    leak = None
    try:
        assert_no_leak([target], target, "P_CURVE", 1, "00:00")
    except LeakageError as exc:
        leak = str(exc)
    base_series = {date(2026, 10, 17): {s: 30.0 for s in SLOTS},
                   date(2026, 10, 16): {s: 40.0 for s in SLOTS},
                   date(2026, 10, 13): {s: 50.0 for s in SLOTS}}
    future_series = dict(base_series)
    future_series[target + timedelta(days=1)] = {s: 999.0 for s in SLOTS}
    future_series[target] = {s: 888.0 for s in SLOTS}
    b_before = naive_baselines(base_series, tgt, "P_CURVE", cfg)
    b_after = naive_baselines(future_series, tgt, "P_CURVE", cfg)
    unchanged = all(b_before[n] == b_after[n] for n in ("N1", "N2", "N3", "N3a"))
    e5 = {"leak_error": leak, "future_injection_changes_nothing": unchanged,
          "anchor_before": b_before["_meta"]["anchor"], "anchor_after": b_after["_meta"]["anchor"]}
    ok5 = leak is not None and unchanged
    results.append(("E5", ok5, e5))

    # E6 滚动切点可复现
    res_a = run_backtest(cfg, hist, cal)
    tab_a = build_error_table(res_a)
    res_b = run_backtest(cfg, hist, cal)
    tab_b = build_error_table(res_b)
    expected_targets = [iso(date(2026, 10, 1) + timedelta(days=i)) for i in range(28)]
    e6 = {"n_targets": len(res_a["targets"]), "first": iso(res_a["targets"][0]),
          "last": iso(res_a["targets"][-1]),
          "targets_ok": [iso(t) for t in res_a["targets"]] == expected_targets,
          "digest_a": table_digest(tab_a), "digest_b": table_digest(tab_b)}
    ok6 = e6["targets_ok"] and e6["digest_a"] == e6["digest_b"]
    results.append(("E6", ok6, e6))

    # E7 等权汇总口径：pooled 与 macro 的差别可复算
    g1 = [{"y_true": 10.0, "y_pred": 12.0}] * 24
    g2 = [{"y_true": 10.0, "y_pred": 20.0}] * 12
    pooled = metrics([r["y_true"] for r in g1 + g2], [r["y_pred"] for r in g1 + g2], 0.0)
    macro = macro_summary([g1, g2], 0.0)
    e7 = {"pooled_mae": pooled["mae"], "pooled_n": pooled["n"], "macro_mae": macro["mae"]}
    # pooled = 全部 36 点一次算；macro = 两期指标再平均；两种口径不同，故数值必须不等
    # 容差取 1e-6：metrics() 对 mae/rmse 做了 round(...,6)
    ok7 = (pooled["n"] == 36 and abs(pooled["mae"] - (2 * 24 + 10 * 12) / 36.0) < 1e-6
           and abs(macro["mae"] - (2.0 + 10.0) / 2.0) < 1e-6 and pooled["mae"] != macro["mae"])
    results.append(("E7", ok7, e7))

    # E8 MAPE 的 ε=P1，剔除比例 >10% 标不适用
    eps_cfg = mape_eps(cfg, hist, res_a["targets"][0])
    vals = []
    for series in hist.values():
        for day, slots in series.items():
            if day >= res_a["targets"][0]:
                continue
            vals.extend(v for v in slots.values() if v is not None)
    expect_eps = percentile(vals, 1.0)
    many_small = [0.01] * 15 + [100.0] * 5
    m8 = metrics(many_small, many_small, 0.5)
    e8 = {"eps": round(eps_cfg, 6), "expect_eps": round(expect_eps, 6),
          "excluded": m8["mape_excluded"], "ratio": m8["mape_excluded_ratio"],
          "mape_applicable": m8["mape_applicable"]}
    ok8 = (abs(eps_cfg - expect_eps) < 1e-9 and m8["mape_applicable"] is False
           and m8["mape_excluded"] == 15 and m8["mape_excluded_ratio"] > 0.10)
    results.append(("E8", ok8, e8))

    # E9 日历联动：分层正确，且默认不跳过目标日
    holiday = calendar_day_type(cal, date(2026, 10, 1))
    makeup = calendar_day_type(cal, date(2026, 9, 20))
    strata = sorted({r["stratum"] for r in tab_a})
    cfg_skip = json.loads(json.dumps(cfg))
    cfg_skip["calendar"]["skip_non_business_days"] = True
    res_skip = run_backtest(cfg_skip, hist, cal)
    skipped_ok = all(calendar_day_type(cal, t) in business_day_types(cfg) for t in res_skip["targets"])
    e9 = {"holiday_1001": holiday, "makeup_0920": makeup, "strata": strata,
          "n_targets_default": len(res_a["targets"]), "n_targets_skip": len(res_skip["targets"]),
          "skip_only_business_days": skipped_ok}
    ok9 = (holiday == "法定节假日" and makeup == "调休上班日"
           and "法定节假日" in strata and "调休上班日" in strata
           and len(res_a["targets"]) > len(res_skip["targets"]) and skipped_ok)
    results.append(("E9", ok9, e9))

    # E10 配置化：改窗口/评估期结果变化，且可复现
    cfg_w = json.loads(json.dumps(cfg))
    cfg_w["backtest"]["n2_window_days"] = 3
    tab_w = build_error_table(run_backtest(cfg_w, hist, cal))
    cfg_e = json.loads(json.dumps(cfg))
    cfg_e["backtest"]["eval_end"] = "2026-10-14"
    tab_e = build_error_table(run_backtest(cfg_e, hist, cal))
    tab_w2 = build_error_table(run_backtest(cfg_w, hist, cal))
    n2_rows = {r["stratum"]: r for r in tab_a if r["origin_type"] == "P_CURVE" and r["baseline"] == "N2"}
    n2_rows_w = {r["stratum"]: r for r in tab_w if r["origin_type"] == "P_CURVE" and r["baseline"] == "N2"}
    e10 = {"n2_mae_win7": n2_rows["ALL"]["mae"], "n2_mae_win3": n2_rows_w["ALL"]["mae"],
           "periods_win7": n2_rows["ALL"]["n_periods"], "periods_short": [r for r in tab_e if r["baseline"] == "N2" and r["stratum"] == "ALL"][0]["n_periods"],
           "digest_repeat_equal": table_digest(tab_w) == table_digest(tab_w2)}
    ok10 = (n2_rows["ALL"]["mae"] != n2_rows_w["ALL"]["mae"]
            and e10["periods_short"] == 14 and e10["digest_repeat_equal"])
    results.append(("E10", ok10, e10))

    note = {"config": CONFIG_PATH.name, "history": HISTORY_CSV.name, "calendar": CALENDAR_CSV.name,
            "users": len(hist), "data_label": cfg.get("data", {}).get("data_label")}
    return results, note


# ---------------------------------------------------------------- 入口
def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--generate-samples", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args(argv)
    if args.generate_samples:
        print("samples: " + json.dumps(generate_samples(), ensure_ascii=False))
    if args.run:
        cfg = load_config()
        hist = load_history_csv()
        cal = load_calendar()
        result = run_backtest(cfg, hist, cal)
        table = build_error_table(result)
        paths = write_outputs(cfg, result, table)
        print("mape_eps=%.6f  periods=%d  digest=%s" % (
            result["eps"], len(result["targets"]), table_digest(table)[:16]))
        for row in table:
            if row["stratum"] == "ALL":
                print("  %-9s %-4s n=%-6d mae=%-9s rmse=%-9s nmae=%-8s mape=%-9s picp=%s" % (
                    row["origin_type"], row["baseline"], row["n_points"], row["mae"],
                    row["rmse"], row["nmae"], row["mape"], row["picp"]))
        print("written: " + json.dumps({k: str(v) for k, v in paths.items()}, ensure_ascii=False))
    if args.verify:
        results, note = run_checks()
        failed = 0
        lines = []
        for name, ok, detail in results:
            head = "[%s] %s" % ("PASS" if ok else "FAIL", name)
            body = "      " + json.dumps(detail, ensure_ascii=False, default=str)
            lines.append(head)
            lines.append(body)
            print(head)
            print(body)
            if not ok:
                failed += 1
        tail = "samples: " + json.dumps(note, ensure_ascii=False)
        summary = "%d/%d PASS" % (len(results) - failed, len(results))
        lines.append(tail)
        lines.append(summary)
        print(tail)
        print(summary)
        # 控制台在 Windows 上可能是 GBK 码页，文件一律以 UTF-8 显式写出
        VERIFICATION_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 1 if failed else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
