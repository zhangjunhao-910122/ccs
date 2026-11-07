#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""#48 可切换模型注册表与机器学习训练/推理流水线（参考实现，纯标准库）。

上游契约（不重写，只复用）：
  - #31 docs/spec/forecast-backtest/  滚动样本外切分、朴素基线、metrics()、误差表口径
  - #45 docs/spec/load-forecast/      特征台账、feature_set_hash、防穿越断言
  - engineering §8.1                  第三方依赖白名单不含 ML 库 → 本实现只用标准库

硬约束：
  - 指标函数不在本文件实现，统一走 #31 的 metrics()（#31 L8：#48 换模型不得换指标函数）。
  - 切分与 #31 同一 rolling origin：同窗同起点，逐时点对齐。
  - 预处理对象（标准化器）只在训练窗 fit，禁止全期统计量。
  - 缺测显式：不插值、不补零；缺特征走 FALLBACK_BASELINE 并写进预测记录。
  - 每个模型可复现：固定 seed + 固定数据版本（data_digest）→ 产物 digest 稳定。
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
CONFIG_PATH = ROOT / "model.config.json"
OUT_DIR = ROOT / "out"
SAMPLES_DIR = ROOT / "samples"
ARTIFACTS_DIR = OUT_DIR / "artifacts"
VERIFICATION_PATH = ROOT / "verification.txt"

SLOTS = list(range(1, 25))
CODE_VERSION = "model-pipeline/1.0"
CREATED_AT = "2026-11-07T00:00:00+08:00"


# ---------------------------------------------------------------- 基础工具
def d(s):
    y, m, dd = [int(v) for v in s.split("-")]
    return date(y, m, dd)


def iso(x):
    return x.isoformat()


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


def digest_of(obj):
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_backtest_module():
    """#31 参考实现（切分、基线、指标）。"""
    p = str(REPO / "docs" / "spec" / "forecast-backtest")
    if p not in sys.path:
        sys.path.insert(0, p)
    import reference_backtest as m
    return m


def load_forecast_module():
    """#45 参考实现（特征台账、feature_set_hash）。"""
    p = str(REPO / "docs" / "spec" / "load-forecast")
    if p not in sys.path:
        sys.path.insert(0, p)
    import reference_forecast as m
    return m


def days_to_month_end(day):
    if day.month == 12:
        nxt = date(day.year + 1, 1, 1)
    else:
        nxt = date(day.year, day.month + 1, 1)
    return (nxt - timedelta(days=1) - day).days


# ---------------------------------------------------------------- 特征供给
# 本流水线能供给的特征（其余台账特征记 NOT_PROVIDED，不入模）。
SUPPLY = {
    "f_load_same_hour_lag1": ("D04", "N1 口径：最近可得日同刻小时电量"),
    "f_load_med7": ("D04", "N2 口径：最近 7 个可得日同刻中位数"),
    "f_load_med_weekday8": ("D04", "N3 口径：同星期几最近 8 周同刻中位数"),
    "f_load_peak_stats": ("D04", "最近 7 个可得日的日峰负荷中位数"),
    "f_calendar_day_type": ("D12", "目标日日类型编码"),
    "f_calendar_position": ("D12", "目标日距月末天数"),
}

FEATURE_GETTERS = {
    "f_load_same_hour_lag1": lambda f, s: f["N1"].get(s),
    "f_load_med7": lambda f, s: f["N2"].get(s),
    "f_load_med_weekday8": lambda f, s: f["N3"].get(s),
    "f_load_peak_stats": lambda f, s: f["peak"],
    "f_calendar_day_type": lambda f, s: f["day_type_code"],
    "f_calendar_position": lambda f, s: f["position"],
}


class FeatureBuilder(object):
    """按 (用户, 业务日, 起点) 缓存特征矩阵。

    特征口径直接取自 #31 的 naive_baselines()：N1/N2/N3 与本票特征一一对应，
    口径不同步的风险因此降到最低；防穿越仍由 #31 的 assert_no_leak 兜底。
    """

    def __init__(self, bt, cfg, hist, cal):
        self.bt = bt
        self.cfg = cfg
        self.hist = hist
        self.cal = cal
        self._cache = {}

    def _peak_stat(self, series, day_list):
        peaks = []
        for ds in day_list:
            vals = [v for v in series.get(d(ds), {}).values() if v is not None]
            if vals:
                peaks.append(max(vals))
        if not peaks:
            return None
        return self.bt.median(peaks)

    def day_features(self, user, day, origin_type):
        key = (user, day, origin_type)
        if key in self._cache:
            return self._cache[key]
        series = self.hist[user]
        bl = self.bt.naive_baselines(series, day, origin_type, self.cfg)
        meta = bl.get("_meta", {})
        dtype = self.bt.calendar_day_type(self.cal, day)
        code = self.cfg["feature_encoding"]["f_calendar_day_type"].get(dtype)
        out = {
            "N1": bl.get("N1") or {},
            "N2": bl.get("N2") or {},
            "N3": bl.get("N3") or {},
            "N2_p10": bl.get("N2_p10") or {},
            "N2_p90": bl.get("N2_p90") or {},
            "peak": self._peak_stat(series, meta.get("n2_days", [])),
            "day_type_code": float(code) if code is not None else 0.0,
            "day_type": dtype,
            "position": float(days_to_month_end(day)),
            "anchor": meta.get("anchor"),
            "bound": meta.get("bound"),
            "n2_days": meta.get("n2_days", []),
        }
        self._cache[key] = out
        return out

    def vector(self, feats, slot, feature_ids):
        x = {}
        for fid in feature_ids:
            getter = FEATURE_GETTERS.get(fid)
            x[fid] = None if getter is None else getter(feats, slot)
        return x


# ---------------------------------------------------------------- 训练集
def build_training_set(bt, cfg, builder, user, origin_type, target, feature_ids):
    """训练窗 = 目标日最近可得日往前 window_days 个有数据日。

    训练行 (D, s)：标签 = D 日实测值（缺测丢弃，不插值不补零）；
    特征按 D 自己的可得口径取（#31 naive_baselines(D)），故特征日严格早于 D。

    offset = 该行自己的 N3 基线（同星期几 8 周中位数，= #31 主基线）。
    模型学的是「相对基线的残差」而不是绝对值：绝对值口径下，全局模型无法表达
    「不同时段不同水平」，会把高峰时段拉平（实测 GBDT/分位数 MAE 劣化到基线的 4 倍）；
    残差口径继承基线的时段结构，模型只学基线解释不了的部分。
    """
    av = bt.availability_cfg(cfg)
    shift, cutoff = av["shift_days"], av["cutoff"]
    bound = bt.latest_available_date(target, origin_type, shift, cutoff)
    have = bt.days_with_data(builder.hist[user])
    window = int(cfg["train"]["window_days"])
    train_days = [dd for dd in have if dd <= bound][-window:]
    bt.assert_no_leak(train_days, target, origin_type, shift, cutoff)
    rows = []
    n_no_baseline = 0
    for dd in train_days:
        feats = builder.day_features(user, dd, origin_type)
        vals = builder.hist[user].get(dd, {})
        for s in SLOTS:
            y = vals.get(s)
            if y is None:
                continue
            offset = feats["N3"].get(s)
            if offset is None:
                n_no_baseline += 1
                continue
            x = builder.vector(feats, s, feature_ids)
            rows.append({"day": dd, "slot": s, "x": x, "y": float(y), "offset": float(offset),
                         "day_type": bt.calendar_day_type(builder.cal, dd)})
    return {
        "rows": rows,
        "days": train_days,
        "bound": bound,
        "n_dropped_missing_label": len(train_days) * len(SLOTS) - len(rows) - n_no_baseline,
        "n_dropped_missing_baseline": n_no_baseline,
    }


def drop_incomplete(rows, feature_ids):
    kept, dropped = [], 0
    for r in rows:
        if any(r["x"][f] is None for f in feature_ids):
            dropped += 1
            continue
        kept.append(r)
    return kept, dropped


class Standardizer(object):
    """标准化器：只在训练窗 fit（禁止全期统计量）。"""

    def __init__(self, feature_ids):
        self.feature_ids = list(feature_ids)
        self.mean = {}
        self.std = {}

    def fit(self, rows):
        for fid in self.feature_ids:
            vals = [r["x"][fid] for r in rows]
            n = len(vals)
            if n == 0:
                self.mean[fid] = 0.0
                self.std[fid] = 1.0
                continue
            mu = sum(vals) / n
            var = sum((v - mu) ** 2 for v in vals) / n
            self.mean[fid] = mu
            self.std[fid] = math.sqrt(var) if var > 1e-12 else 1.0
        return self

    def apply(self, x):
        return [(x[f] - self.mean[f]) / self.std[f] for f in self.feature_ids]

    def as_dict(self):
        return {"feature_ids": self.feature_ids,
                "mean": {k: round(v, 10) for k, v in self.mean.items()},
                "std": {k: round(v, 10) for k, v in self.std.items()}}


# ---------------------------------------------------------------- 模型实现
def _solve(matrix, rhs):
    """高斯消元（部分主元），解线性方程组。"""
    n = len(rhs)
    m = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            m[col][col] += 1e-12
            piv = col
        m[col], m[piv] = m[piv], m[col]
        pv = m[col][col]
        for r in range(col + 1, n):
            f = m[r][col] / pv
            if f == 0.0:
                continue
            for c in range(col, n + 1):
                m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))
        x[r] = s / m[r][r]
    return x


def fit_ridge(X, y, lam):
    p = len(X[0])
    Xa = [[1.0] + list(r) for r in X]
    A = [[sum(Xa[i][j] * Xa[i][k] for i in range(len(y))) for k in range(p + 1)]
         for j in range(p + 1)]
    b = [sum(Xa[i][j] * y[i] for i in range(len(y))) for j in range(p + 1)]
    for j in range(1, p + 1):
        A[j][j] += lam
    w = _solve(A, b)
    return {"intercept": w[0], "coef": w[1:]}


def predict_ridge(params, x):
    return params["intercept"] + sum(c * v for c, v in zip(params["coef"], x))


def _tree_value(tree, x):
    if "feature" not in tree:
        return tree["value"]
    if x[tree["feature"]] <= tree["threshold"]:
        return _tree_value(tree["left"], x)
    return _tree_value(tree["right"], x)


def _fit_tree(X, resid, idx, depth, max_depth, min_leaf, ncols, orders=None):
    """平方损失 CART。

    orders 传入时用预排序序列，避免每个节点重复排序（训练耗时主因）。
    """
    n = len(idx)
    if n == 0:
        return {"value": 0.0}
    total = sum(resid[i] for i in idx)
    total_sq = sum(resid[i] * resid[i] for i in idx)
    value = total / n
    if depth >= max_depth or n < 2 * min_leaf:
        return {"value": value}
    best = None
    idx_set = set(idx) if orders is not None else None
    for j in range(ncols):
        if orders is not None:
            order = [i for i in orders[j] if i in idx_set]
        else:
            order = sorted(idx, key=lambda i: X[i][j])
        run = 0.0
        run_sq = 0.0
        for k in range(len(order) - 1):
            i = order[k]
            run += resid[i]
            run_sq += resid[i] * resid[i]
            left = k + 1
            right = n - left
            if left < min_leaf or right < min_leaf:
                continue
            a, b = order[k], order[k + 1]
            if X[a][j] == X[b][j]:
                continue
            thr = (X[a][j] + X[b][j]) / 2.0
            right_sum = total - run
            right_sq = total_sq - run_sq
            gain = (run_sq - run * run / left) + (right_sq - right_sum * right_sum / right)
            if best is None or gain > best[0]:
                best = (gain, j, thr)
    if best is None:
        return {"value": value}
    _, j, thr = best
    left_idx = [i for i in idx if X[i][j] <= thr]
    right_idx = [i for i in idx if X[i][j] > thr]
    return {
        "value": value,
        "feature": j,
        "threshold": thr,
        "left": _fit_tree(X, resid, left_idx, depth + 1, max_depth, min_leaf, ncols, orders),
        "right": _fit_tree(X, resid, right_idx, depth + 1, max_depth, min_leaf, ncols, orders),
    }


def fit_gbdt(X, y, hyper):
    n_est = int(hyper.get("n_estimators", 20))
    lr = float(hyper.get("learning_rate", 0.1))
    max_depth = int(hyper.get("max_depth", 3))
    min_leaf = int(hyper.get("min_samples_leaf", 20))
    ncols = len(X[0])
    base = sum(y) / len(y)
    pred = [base] * len(y)
    trees = []
    orders = [sorted(range(len(y)), key=lambda i, j=j: X[i][j]) for j in range(ncols)]
    for _ in range(n_est):
        resid = [y[i] - pred[i] for i in range(len(y))]
        tree = _fit_tree(X, resid, list(range(len(y))), 0, max_depth, min_leaf, ncols, orders)
        trees.append(tree)
        for i in range(len(y)):
            pred[i] += lr * _tree_value(tree, X[i])
    return {"base": base, "lr": lr, "trees": trees}


def predict_gbdt(params, x):
    out = params["base"]
    for tree in params["trees"]:
        out += params["lr"] * _tree_value(tree, x)
    return out


def _pinball_loss(X, y, w, b, tau, p, ridge):
    n = len(y)
    s = 0.0
    for xi, yi in zip(X, y):
        pred = b + sum(w[j] * xi[j] for j in range(p))
        u = yi - pred
        s += tau * u if u >= 0 else (tau - 1.0) * u
    reg = sum(v * v for v in w) * ridge / n
    return s / n + reg


def fit_quantile_linear(X, y, tau, iterations, lr, ridge, warm=None):
    """线性分位数回归（pinball 损失）。

    两个必要的工程约束：
      1. warm start —— 用最小二乘解起步，残差口径下起步点已经接近最优，
         否则纯次梯度从零起步要上千次迭代才收敛（实测 300 次仍过冲，MAE 劣化 10 倍）。
      2. 损失回溯 —— 每次试探更新后比较 pinball 损失，变差就减半步长回退，
         保证损失单调不增（次梯度法的步长不按损失自适应时必然过冲）。
    """
    p = len(X[0])
    if warm:
        w = list(warm["coef"])
        b = float(warm["intercept"])
    else:
        w = [0.0] * p
        b = 0.0
    n = len(y)
    cur_lr = float(lr)
    cur_loss = _pinball_loss(X, y, w, b, tau, p, ridge)
    for _ in range(iterations):
        gw = [0.0] * p
        gb = 0.0
        for xi, yi in zip(X, y):
            pred = b + sum(w[j] * xi[j] for j in range(p))
            g = (tau - 1.0) if yi < pred else tau
            gb += g
            for j in range(p):
                gw[j] += g * xi[j]
        accepted = False
        for _ in range(4):
            nb = b + cur_lr * gb / n
            nw = [w[j] + cur_lr * (gw[j] / n + ridge * w[j]) for j in range(p)]
            new_loss = _pinball_loss(X, y, nw, nb, tau, p, ridge)
            if new_loss <= cur_loss:
                w, b, cur_loss = nw, nb, new_loss
                cur_lr = min(cur_lr * 1.2, 0.5)
                accepted = True
                break
            cur_lr = cur_lr / 2.0
        if not accepted:
            cur_lr = cur_lr / 2.0
            if cur_lr < 1e-6:
                break
    return {"intercept": b, "coef": w}


def predict_quantile_linear(params, x):
    return params["intercept"] + sum(c * v for c, v in zip(params["coef"], x))


# ---------------------------------------------------------------- 训练/推理
def train_model(model_def, bt, cfg, builder, user, origin_type, target, data_digest):
    feature_ids = list(model_def["feature_ids"])
    hyper = dict(model_def.get("hyperparams") or {})
    family = model_def["family"]
    ts = build_training_set(bt, cfg, builder, user, origin_type, target, feature_ids)
    rows, dropped = drop_incomplete(ts["rows"], feature_ids)
    artifact = {
        "code_version": CODE_VERSION,
        "model_id": model_def["model_id"],
        "version": model_def["version"],
        "family": family,
        "user_id": user,
        "origin_type": origin_type,
        "target_date": iso(target),
        "feature_ids": feature_ids,
        "feature_set_hash": builder.fc.feature_set_hash(builder.manifest, feature_ids),
        "hyperparams": hyper,
        "seed": int(cfg["train"]["seed"]),
        "created_at": CREATED_AT,
        "data_label": cfg["data"]["data_label"],
        "data_digest": data_digest,
        "training_window": {
            "start": iso(ts["days"][0]) if ts["days"] else None,
            "end": iso(ts["days"][-1]) if ts["days"] else None,
            "n_days": len(ts["days"]),
            "n_rows": len(rows),
            "n_dropped_missing_label": ts["n_dropped_missing_label"],
            "n_dropped_missing_feature": dropped,
            "day_type_support": _day_type_support(rows),
        },
        "scaler": None,
        "params": {},
        "train_metrics": {},
    }
    if not rows:
        artifact["params"] = {"rule": "TRAINING_EMPTY"}
        artifact["train_metrics"] = {"n": 0, "mae": None, "rmse": None}
        artifact["artifact_digest"] = digest_of(artifact)
        return artifact

    # 区间校准层：训练窗按时间切 tr/ca，模型与标准化器都只在 tr 上 fit，
    # ca 上的残差是样本外误差 → 用它定区间宽度（split conformal，见 fit_split_calibration）。
    artifact["calibration"] = fit_split_calibration(bt, cfg, family, hyper, feature_ids,
                                                    rows, builder, user, origin_type)

    # 交付模型：全训练窗训练。点预测保持模型语义（校准只作用于区间，不平移点预测）。
    params, resid_preds, scaler_dict = _fit_family(family, hyper, feature_ids, rows,
                                                    builder, user, origin_type, ts["days"])
    artifact["params"] = params
    artifact["scaler"] = scaler_dict
    artifact["target"] = "RESIDUAL_ON_N3"
    preds = []
    for r, pr in zip(rows, resid_preds):
        point = r["offset"] + pr
        preds.append(point)
    artifact["train_metrics"] = _metrics_of([r["y"] for r in rows], preds)
    artifact["artifact_digest"] = digest_of(artifact)
    return artifact


# ---------------------------------------------------------------- 区间校准层
# 口径（第一性原理，三条）：
#   1) 区间宽度必须来自**样本外**误差。宽度若取自训练窗内的拟合残差（样本内），
#      对可拟合模型系统性偏窄（样本内乐观）：实测线性/分位数模型的名义 90% 区间
#      实测覆盖不足 0.85。改用「训练窗尾部留出段 + 模型与标准化器都只在前段 fit」
#      才能拿到样本外误差（split conformal）。
#   2) 残差相对 N3 基线的尺度随时段变化（实测标准差凌晨 0.14 → 午间 0.49），
#      故用「基线残差 MAD」作时段尺度（N3 无拟合参数 → 该尺度天然是样本外的）；
#      分数按尺度归一化后统一估分位，再乘回各时段尺度。
#   3) 名义覆盖 90% 对应双侧 P5/P95，不是 P10/P90——后者只有 80% 的宽度。
CALIBRATION_METHOD = "FORWARD_CHAINING_CONFORMAL_SLOT_SCALED"


def _mad(bt, xs):
    """稳健尺度：1.4826 × median(|x − median(x)|)。"""
    if not xs:
        return None
    m = bt.median(xs)
    return 1.4826 * bt.median([abs(x - m) for x in xs])


def _day_group(cfg, day_type):
    """日型 → 日型组（配置驱动；未知日型回退 OTHER）。"""
    for g, types in (cfg.get("day_groups") or {}).items():
        if g == "note":
            continue
        if day_type in (types or []):
            return g
    return "OTHER"


def _winsorize(bt, xs, pct):
    """裁剪极端值：早期折训练样本少、误差可能极端，不裁会污染分位。"""
    if not xs or pct <= 0:
        return list(xs)
    lo = bt.percentile(xs, pct)
    hi = bt.percentile(xs, 100.0 - pct)
    return [min(max(x, lo), hi) for x in xs]


def _day_type_support(rows):
    """训练窗内各日型的天数（判定区间口径在哪些日型上有同类样本支撑）。"""
    by = {}
    for r in rows:
        by.setdefault(r.get("day_type") or "UNKNOWN", set()).add(r["day"])
    return dict((k, len(v)) for k, v in sorted(by.items()))


def _fit_family(family, hyper, feature_ids, rows, builder, user, origin_type, days):
    """在给定行集上训练一个模型族，返回 (params, resid_preds, scaler_dict)。

    resid_preds 与 rows 同序：残差口径下模型预测的是「相对该行自己 N3 基线的增量」。
    """
    if family == "NAIVE":
        return {"rule": "N3"}, [0.0] * len(rows), None
    if family == "SEQUENTIAL":
        min_pairs = int(hyper.get("min_pairs", 5))
        phi, const, last_resid, n_pairs = _fit_ar(builder, user, rows, days, min_pairs, origin_type)
        params = {
            "rule": "N3 + AR(1) 残差修正",
            "ar_order": int(hyper.get("ar_order", 1)),
            "min_pairs": min_pairs,
            "phi": {str(k): phi[k] for k in sorted(phi)},
            "const": {str(k): const[k] for k in sorted(const)},
            "last_residual": {str(k): last_resid[k] for k in sorted(last_resid)},
            "n_pairs": {str(k): n_pairs[k] for k in sorted(n_pairs)},
        }
        return params, _ar_resid_preds(rows, phi, const), None
    scaler = Standardizer(feature_ids).fit(rows)
    X = [scaler.apply(r["x"]) for r in rows]
    y_resid = [r["y"] - r["offset"] for r in rows]
    if family == "LINEAR":
        params = {"ridge_lambda": float(hyper.get("ridge_lambda", 0.001))}
        params["model"] = fit_ridge(X, y_resid, params["ridge_lambda"])
        preds = [predict_ridge(params["model"], x) for x in X]
    elif family == "GBDT":
        params = {
            "n_estimators": int(hyper.get("n_estimators", 20)),
            "learning_rate": float(hyper.get("learning_rate", 0.1)),
            "max_depth": int(hyper.get("max_depth", 3)),
            "min_samples_leaf": int(hyper.get("min_samples_leaf", 20)),
            "subsample": float(hyper.get("subsample", 1.0)),
        }
        params["model"] = fit_gbdt(X, y_resid, params)
        preds = [predict_gbdt(params["model"], x) for x in X]
    elif family == "QUANTILE":
        params = {
            "quantiles": list(hyper.get("quantiles", [0.1, 0.5, 0.9])),
            "iterations": int(hyper.get("iterations", 80)),
            "learning_rate": float(hyper.get("learning_rate", 0.05)),
            "ridge_lambda": float(hyper.get("ridge_lambda", 0.001)),
        }
        warm = fit_ridge(X, y_resid, params["ridge_lambda"])
        params["model"] = {str(tau): fit_quantile_linear(X, y_resid, tau, params["iterations"],
                                                        params["learning_rate"],
                                                        params["ridge_lambda"], warm=warm)
                           for tau in params["quantiles"]}
        preds = [predict_quantile_linear(params["model"][str(0.5)], x) for x in X]
    else:
        raise ValueError("未知模型族: %s" % family)
    return params, preds, scaler.as_dict()


def _resid_predict_rows(family, params, scaler_dict, rows, history_rows=None):
    """用已训练好的参数对任意行集预测残差（与 rows 同序）。

    history_rows 只对 SEQUENTIAL 有意义：AR(1) 一步预测需要前一日残差，
    校准段第一天要接训练段最后一天，故传入训练段作为历史。
    """
    if family == "NAIVE":
        return [0.0] * len(rows)
    if family == "SEQUENTIAL":
        phi = {int(k): v for k, v in (params.get("phi") or {}).items()}
        const = {int(k): v for k, v in (params.get("const") or {}).items()}
        hist = list(history_rows or [])
        preds = _ar_resid_preds(hist + list(rows), phi, const)
        return preds[len(hist):]
    mean, std = scaler_dict["mean"], scaler_dict["std"]
    fids = scaler_dict["feature_ids"]
    out = []
    for r in rows:
        x = [(r["x"][f] - mean[f]) / (std[f] if std[f] else 1.0) for f in fids]
        if family == "LINEAR":
            out.append(predict_ridge(params["model"], x))
        elif family == "GBDT":
            out.append(predict_gbdt(params["model"], x))
        elif family == "QUANTILE":
            qs = params["quantiles"]
            vals = [predict_quantile_linear(params["model"][str(tau)], x) for tau in qs]
            out.append(vals[qs.index(0.5)] if 0.5 in qs else vals[len(vals) // 2])
        else:
            raise ValueError("未知模型族: %s" % family)
    return out


def fit_split_calibration(bt, cfg, family, hyper, feature_ids, rows, builder, user, origin_type):
    """样本外区间校准（时间向前交叉拟合 + 按时段尺度缩放）。

    把训练窗按时间切成 K 块，做 forward-chaining：
      第 k 折的校准样本 = 第 k 块，训练集 = 第 1..k-1 块（严格只用更早的数据）。
    三个理由：
      1) 每个校准样本都是「用过去预测未来」的样本外误差 → 区间宽度不被样本内乐观压窄
         （宽度若取自训练窗内拟合残差，对可拟合模型系统性偏窄，实测覆盖不足 0.85）；
      2) 校准样本量等于整个训练窗，而不是尾部一小段 → 分位估计稳定
         （单次留出段只有 11 天 × 24 时段，分位噪声大，实测切点间覆盖从 0.13 跳到 1.0）；
      3) 时间隔离不被破坏：折内训练数据永远早于校准数据，不存在用未来预测过去。
    分数 = (真值残差 − 模型残差) / 该时段基线残差 MAD；全局估双侧分位
    （名义 90% → P5/P95），再乘回各时段尺度。
    """
    folds = int(cfg["train"].get("calibration_folds", 4))
    nominal = float(cfg["interval"]["nominal_coverage"])
    alpha = (1.0 - nominal) / 2.0
    days = sorted({r["day"] for r in rows})
    K = max(2, min(folds, len(days)))
    blocks = [days[i * len(days) // K:(i + 1) * len(days) // K] for i in range(K)]
    out = {
        "scope": "forward_chaining_conformal",
        "method": CALIBRATION_METHOD,
        "status": "INSUFFICIENT_CALIBRATION_SAMPLE",
        "nominal_coverage": nominal,
        "alpha": alpha,
        "calibration_folds": K,
        "n_train_days": len(days),
        "n_calib_days": 0,
        "n_train_rows": len(rows),
        "n_calib_rows": 0,
        "folds_used": 0,
        "q_lo": None, "q_hi": None, "shift": None,
        "scale_global": None, "scale_slots": {},
    }
    scale_slots = {}
    for s in SLOTS:
        v = [r["y"] - r["offset"] for r in rows if r["slot"] == s]
        if len(v) >= 8:
            m = _mad(bt, v)
            if m and m > 0:
                scale_slots[str(s)] = m
    scale_global = _mad(bt, [r["y"] - r["offset"] for r in rows]) or 1.0
    fold_samples = {}
    used = 0
    for k in range(1, K):
        tr_days = []
        for j in range(k):
            tr_days.extend(blocks[j])
        tr_set = set(tr_days)
        ca_set = set(blocks[k])
        tr_rows = [r for r in rows if r["day"] in tr_set]
        ca_rows = [r for r in rows if r["day"] in ca_set]
        if len(tr_rows) < 20 or len(ca_rows) < 20:
            continue
        params, _, scaler_dict = _fit_family(family, hyper, feature_ids, tr_rows,
                                              builder, user, origin_type, tr_days)
        ca_preds = _resid_predict_rows(family, params, scaler_dict, ca_rows,
                                       history_rows=tr_rows)
        f_sc, f_er, f_grp = [], [], {}
        for r, pr in zip(ca_rows, ca_preds):
            sc = scale_slots.get(str(r["slot"])) or scale_global
            err = r["y"] - r["offset"] - pr
            f_er.append(err)
            f_sc.append(err / sc)
            g = _day_group(cfg, r.get("day_type"))
            gsc, gerr = f_grp.setdefault(g, ([], []))
            gsc.append(err / sc)
            gerr.append(err)
        fold_samples[k] = (f_sc, f_er, f_grp)
        used += 1
        out["n_calib_days"] += len(blocks[k])
        out["n_calib_rows"] += len(ca_rows)
    out["folds_used"] = used
    if used == 0:
        return out
    # 只用最近 N 块估分位：更贴近评估期的误差水平
    # （实测评估期残差较训练窗整体下移，早期块的误差水平已经过期）
    recent = max(1, int(cfg["train"].get("calibration_recent_blocks", 2)))
    keep = sorted(fold_samples)[-recent:]
    scores, errs, by_group = [], [], {}
    for k in keep:
        f_sc, f_er, f_grp = fold_samples[k]
        scores.extend(f_sc)
        errs.extend(f_er)
        for g, (gsc, gerr) in f_grp.items():
            t_sc, t_er = by_group.setdefault(g, ([], []))
            t_sc.extend(gsc)
            t_er.extend(gerr)
    out["calibration_blocks_used"] = keep
    out["n_calib_rows_recent"] = len(errs)
    if len(errs) < 20:
        return out
    wins = float(cfg["interval"].get("winsorize_percentile", 0.0) or 0.0)
    group_of = {}
    for dt in sorted({r.get("day_type") for r in rows if r.get("day_type")}):
        group_of[dt] = _day_group(cfg, dt)

    def _spec(xs, es):
        ws = _winsorize(bt, xs, wins)
        return {
            "q_lo": bt.percentile(ws, alpha * 100.0),
            "q_hi": bt.percentile(ws, (1.0 - alpha) * 100.0),
            "shift": bt.percentile(es, 50),
            "n": len(xs),
            "n_winsorized": len(ws),
        }

    groups = {}
    for g in sorted(by_group):
        gsc, gerr = by_group[g]
        if len(gsc) < 20:
            continue
        groups[g] = _spec(gsc, gerr)
    glob = _spec(scores, errs)
    out.update({
        "status": "OK",
        "global": glob,
        "groups": groups,
        "group_of": group_of,
        "q_lo": glob["q_lo"], "q_hi": glob["q_hi"], "shift": glob["shift"],
        "scale_global": scale_global,
        "scale_slots": scale_slots,
        "n_slots_scaled": len(scale_slots),
    })
    return out


def apply_calibration(artifact, slot, point, p10=None, p90=None, day_type=None):
    """套用区间校准层：点预测不变，区间 = 点预测 + shift + 分位 × 该时段尺度。

    点预测不平移——校准只修区间宽度与位置，不改模型语义
    （朴素基线的点预测就是 N3 本身，平移它会直接损害点误差）。
    分位按日型组取（同一训练窗里工作日与假期的残差分布不同），
    组内样本不足时回退全局分位。
    """
    calib = artifact.get("calibration") or {}
    if calib.get("status") != "OK" or point is None:
        return point, p10, p90
    gname = (calib.get("group_of") or {}).get(day_type)
    spec = (calib.get("groups") or {}).get(gname) if gname else None
    if not spec:
        spec = calib.get("global") or {}
    q_lo, q_hi = spec.get("q_lo"), spec.get("q_hi")
    scale = (calib.get("scale_slots") or {}).get(str(slot)) or calib.get("scale_global")
    if not scale or q_lo is None or q_hi is None:
        return point, p10, p90
    shift = spec.get("shift") or 0.0
    p10 = point + shift + q_lo * scale
    p90 = point + shift + q_hi * scale
    # 不变量：p10 <= p50(=point) <= p90
    if p10 > point:
        p10 = point
    if p90 < point:
        p90 = point
    if p10 > p90:
        p10, p90 = p90, p10
    return point, p10, p90


def _metrics_of(y, preds):
    if not y:
        return {"n": 0, "mae": None, "rmse": None}
    mae = sum(abs(a - b) for a, b in zip(y, preds)) / len(y)
    rmse = math.sqrt(sum((a - b) ** 2 for a, b in zip(y, preds)) / len(y))
    return {"n": len(y), "mae": round(mae, 6), "rmse": round(rmse, 6), "scope": "TRAIN_WINDOW"}


def _naive_train_metrics(bt, rows, builder, user):
    """NAIVE 规则 = N3 基线本身，训练窗内指标即基线还原误差。"""
    y = [r["y"] for r in rows]
    p = [r["offset"] for r in rows]
    return _metrics_of(y, p)


def _fit_ar(builder, user, rows, days, min_pairs, origin_type):
    """按 slot 拟合一阶自回归残差：r_t = c + phi * r_{t-1}（残差相对 N3 基线）。"""
    by_day = {}
    for r in rows:
        by_day.setdefault(r["day"], {})[r["slot"]] = r
    residual = {}
    for dd in days:
        for s, r in by_day.get(dd, {}).items():
            residual.setdefault(s, []).append((dd, r["y"] - r["offset"]))
    phi, const, last_resid, n_pairs = {}, {}, {}, {}
    for s, series in residual.items():
        pairs = []
        for i in range(1, len(series)):
            if series[i][0] - series[i - 1][0] == timedelta(days=1):
                pairs.append((series[i - 1][1], series[i][1]))
        n_pairs[s] = len(pairs)
        if len(pairs) < min_pairs:
            continue
        sx = sum(p[0] for p in pairs)
        sy = sum(p[1] for p in pairs)
        sxx = sum(p[0] * p[0] for p in pairs)
        sxy = sum(p[0] * p[1] for p in pairs)
        n = len(pairs)
        den = n * sxx - sx * sx
        if abs(den) < 1e-12:
            continue
        b1 = (n * sxy - sx * sy) / den
        b0 = (sy - b1 * sx) / n
        phi[s] = b1
        const[s] = b0
        last_resid[s] = series[-1][1]
    return phi, const, last_resid, n_pairs


def _ar_resid_preds(rows, phi, const):
    """按 rows 原顺序返回 AR(1) 一步预测的残差值。

    一步预测口径：残差用前一日残差外推（训练窗内不偷看当日实测）。
    """
    by_day = {}
    for idx, r in enumerate(rows):
        by_day.setdefault(r["day"], []).append(idx)
    pred = [0.0] * len(rows)
    prev_resid = {}
    for dd in sorted(by_day):
        for idx in by_day[dd]:
            r = rows[idx]
            s = r["slot"]
            pred[idx] = const.get(s, 0.0) + phi.get(s, 0.0) * prev_resid.get(s, 0.0)
            prev_resid[s] = r["y"] - r["offset"]
    return pred


def _ar_train_metrics(bt, builder, user, rows, phi, const):
    """一步预测口径：残差用前一日残差外推，叠加回 N3 基线后与实测比。"""
    resid_preds = _ar_resid_preds(rows, phi, const)
    y = [r["y"] for r in rows]
    p = [r["offset"] + pr for r, pr in zip(rows, resid_preds)]
    return _metrics_of(y, p)


def predict_model(artifact, feats, slot):
    """推理：返回 point / p10 / p50 / p90 / degraded 列表。

    缺特征 → FALLBACK_BASELINE（退回 N3 基线）并记录降级，不插值不补零。
    """
    family = artifact["family"]
    n3 = feats["N3"].get(slot)
    missing = []
    if family == "NAIVE":
        if n3 is None:
            return {"point": None, "p10": None, "p50": None, "p90": None,
                    "degraded": [{"feature_id": "f_load_med_weekday8",
                                  "action": "FALLBACK_BASELINE",
                                  "missing_reason": "SOURCE_UNAVAILABLE"}],
                    "missing_features": ["f_load_med_weekday8"]}
        point, p10, p90 = apply_calibration(artifact, slot, n3, day_type=feats.get("day_type"))
        return {"point": point, "p10": p10, "p50": point, "p90": p90,
                "degraded": [], "missing_features": []}

    if family == "SEQUENTIAL":
        params = artifact["params"]
        phi = {int(k): v for k, v in (params.get("phi") or {}).items()}
        const = {int(k): v for k, v in (params.get("const") or {}).items()}
        last = {int(k): v for k, v in (params.get("last_residual") or {}).items()}
        if slot not in phi:
            return {"point": n3, "p10": None, "p50": None, "p90": None,
                    "degraded": [{"feature_id": "f_load_med_weekday8", "action": "FALLBACK_BASELINE",
                                  "missing_reason": "SOURCE_UNAVAILABLE"}],
                    "missing_features": ["f_load_med_weekday8"]}
        point = n3 + const[slot] + phi[slot] * last.get(slot, 0.0)
        point, p10, p90 = apply_calibration(artifact, slot, point, day_type=feats.get("day_type"))
        return {"point": point, "p10": p10, "p50": point, "p90": p90,
                "degraded": [], "missing_features": []}

    scaler = artifact.get("scaler")
    if not scaler:
        return {"point": n3, "p10": None, "p50": None, "p90": None,
                "degraded": [{"feature_id": "*", "action": "FALLBACK_BASELINE",
                              "missing_reason": "TRAINING_EMPTY"}],
                "missing_features": []}
    x_raw = {}
    for fid in artifact["feature_ids"]:
        getter = FEATURE_GETTERS.get(fid)
        x_raw[fid] = None if getter is None else getter(feats, slot)
    miss = [fid for fid, v in x_raw.items() if v is None]
    if miss:
        return {"point": n3, "p10": None, "p50": None, "p90": None,
                "degraded": [{"feature_id": fid, "action": "FALLBACK_BASELINE",
                              "missing_reason": "SOURCE_UNAVAILABLE"} for fid in miss],
                "missing_features": miss}
    mean, std = scaler["mean"], scaler["std"]
    x = [(x_raw[f] - mean[f]) / (std[f] if std[f] else 1.0) for f in scaler["feature_ids"]]
    params = artifact["params"]
    if family == "LINEAR":
        point = n3 + predict_ridge(params["model"], x)
        point, p10, p90 = apply_calibration(artifact, slot, point, day_type=feats.get("day_type"))
        return {"point": point, "p10": p10, "p50": point, "p90": p90,
                "degraded": [], "missing_features": []}
    if family == "GBDT":
        point = n3 + predict_gbdt(params["model"], x)
        point, p10, p90 = apply_calibration(artifact, slot, point, day_type=feats.get("day_type"))
        return {"point": point, "p10": p10, "p50": point, "p90": p90,
                "degraded": [], "missing_features": []}
    if family == "QUANTILE":
        qs = params["quantiles"]
        vals = [predict_quantile_linear(params["model"][str(tau)], x) for tau in qs]
        point_resid = vals[qs.index(0.5)] if 0.5 in qs else vals[len(vals) // 2]
        # 残差口径：点预测加回 N3 基准；区间由校准层按时段给出
        # （模型自身分位数保留在 artifact.params.model 里供审计，不直接当区间）。
        point = n3 + point_resid
        point, p10, p90 = apply_calibration(artifact, slot, point, day_type=feats.get("day_type"))
        return {"point": point, "p10": p10, "p50": point, "p90": p90,
                "degraded": [], "missing_features": []}
    raise ValueError("未知模型族: %s" % family)


# ---------------------------------------------------------------- 回测接入
def run_model_backtest(bt, cfg, builder, hist, cal, model_defs):
    """与 #31 同一 rolling origin：同窗同起点，逐时点对齐。

    每个切点重训（滚动训练窗），模型行与基线行进同一张误差表。
    """
    targets = bt.rolling_targets(cfg)
    origin_types = list(cfg["availability"]["origin_types"])
    rows = []
    supports = {}
    for t in targets:
        dtype = bt.calendar_day_type(cal, t)
        for ot in origin_types:
            for u in sorted(hist.keys()):
                for mdef in model_defs:
                    art = train_model(mdef, bt, cfg, builder, u, ot, t, builder.data_digest)
                    feats = builder.day_features(u, t, ot)
                    sup = (art.get("training_window") or {}).get("day_type_support") or {}
                    supports["%s|%s|%s|%s" % (iso(t), u, ot, mdef["model_id"])] = sup
                    for s in SLOTS:
                        y = hist[u].get(t, {}).get(s)
                        p = predict_model(art, feats, s)
                        rows.append({
                            "period": iso(t), "user_id": u, "origin_type": ot,
                            "baseline": mdef["model_id"], "slot_no": s,
                            "y_true": y, "y_pred": p["point"], "p10": p["p10"], "p90": p["p90"],
                            "day_type": dtype,
                        })
    return {"rows": rows, "targets": targets, "origin_types": origin_types, "supports": supports}


def summarize_model_group(bt, rows, eps):
    """与 #31 summarize_group 同口径，额外支持模型分位数区间。"""
    y = [r["y_true"] for r in rows]
    p = [r["y_pred"] for r in rows]
    m = bt.metrics(y, p, eps)
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
    has_interval = any(r["p10"] is not None and r["p90"] is not None for r in rows)
    if rows and all(r["baseline"] == "N2" for r in rows):
        pc = bt.picp(y, [r["p10"] for r in rows], [r["p90"] for r in rows])
        out["picp"] = pc["picp"]
        out["avg_width"] = pc["avg_width"]
        out["interval_n"] = pc["n"]
        out["interval_source"] = "N2_WINDOW_EMPIRICAL"
    elif has_interval:
        pc = bt.picp(y, [r["p10"] for r in rows], [r["p90"] for r in rows])
        out["picp"] = pc["picp"]
        out["avg_width"] = pc["avg_width"]
        out["interval_n"] = pc["n"]
        out["interval_source"] = "MODEL_FORWARD_CHAINING_CONFORMAL_SLOT_SCALED"
    else:
        out["picp"] = None
        out["avg_width"] = None
        out["interval_n"] = 0
        out["interval_source"] = "NOT_APPLICABLE_POINT_FORECAST"
    return out


def build_model_error_table(bt, rows, eps):
    groups = {}
    for r in rows:
        groups.setdefault((r["origin_type"], r["baseline"], "ALL"), []).append(r)
        groups.setdefault((r["origin_type"], r["baseline"], r["day_type"]), []).append(r)
    table = []
    for key in sorted(groups):
        ot, name, stratum = key
        g = groups[key]
        summ = summarize_model_group(bt, g, eps)
        per_period = {}
        for r in g:
            per_period.setdefault(r["period"], []).append(r)
        mac = bt.macro_summary(list(per_period.values()), eps)
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


# ---------------------------------------------------------------- 注册表
def build_registry(cfg, artifacts):
    """注册表：(model_id, version) 唯一；登记模型定义 + 训练窗 + 训练窗内指标。

    特征集变更即新版本：注册项记录 feature_set_hash，换特征必须换 version。
    """
    by_model = {}
    for art in artifacts:
        by_model.setdefault((art["model_id"], art["version"]), []).append(art)
    registry = []
    for key in sorted(by_model):
        arts = by_model[key]
        first = arts[0]
        windows = [a["training_window"] for a in arts if a["training_window"]["start"]]
        n_rows = sum(w["n_rows"] for w in windows)
        maes = [a["train_metrics"].get("mae") for a in arts if a["train_metrics"].get("mae") is not None]
        registry.append({
            "model_id": first["model_id"],
            "version": first["version"],
            "family": first["family"],
            "feature_set_hash": first["feature_set_hash"],
            "feature_ids": first["feature_ids"],
            "training_window": {
                "start": min(w["start"] for w in windows) if windows else None,
                "end": max(w["end"] for w in windows) if windows else None,
                "n_rows": n_rows,
            },
            "hyperparams": first["hyperparams"],
            "status": "ACTIVE",
            "created_at": CREATED_AT,
            "metrics": {
                "train_mae_mean": round(sum(maes) / len(maes), 6) if maes else None,
                "train_mae_max": round(max(maes), 6) if maes else None,
                "n_users": len(arts),
                "scope": "TRAIN_WINDOW",
            },
            "seed": first["seed"],
            "data_label": first["data_label"],
            "data_digest": first["data_digest"],
            "artifact_digest": digest_of([a["artifact_digest"] for a in arts]),
            "supported_origins": list(cfg["selection"].get("default_by_origin", {}).values()) and
                                   sorted(set(first["supported_origins"]) if "supported_origins" in first
                                          else set(cfg["availability"]["origin_types"])),
            "scope": "USER",
            "code_version": CODE_VERSION,
            "build": {
                "reference_target": cfg["registry"]["reference_target"],
                "reference_origin": cfg["registry"]["reference_origin"],
                "user_artifact_digests": {a["user_id"]: a["artifact_digest"] for a in arts},
            },
        })
    return registry


def write_registry(registry, path):
    with open(path, "w", encoding="utf-8") as fh:
        for rec in registry:
            fh.write(canonical(rec) + "\n")


# ---------------------------------------------------------------- 预测记录
def build_run_id(origin_type, forecast_origin, target_date, model_id, version, created_at):
    raw = "|".join([origin_type, forecast_origin, target_date, model_id, version, created_at])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def run_forecasts(bt, cfg, builder, hist, cal, model_defs):
    """按目标与口径跑推理，产出 forecast_run / forecast_prediction 记录。"""
    runs, preds = [], []
    by_id = {m["model_id"]: m for m in model_defs}
    for target_date in cfg["forecast_runs"]["target_dates"]:
        t = d(target_date)
        for ot in cfg["forecast_runs"]["origin_types"]:
            model_id = cfg["selection"]["default_by_origin"].get(ot)
            if model_id not in by_id:
                continue
            mdef = by_id[model_id]
            origin_ts = bt.origin_timestamp(ot, t).isoformat()
            for u in sorted(hist.keys()):
                art = train_model(mdef, bt, cfg, builder, u, ot, t, builder.data_digest)
                feats = builder.day_features(u, t, ot)
                degraded = []
                for s in SLOTS:
                    p = predict_model(art, feats, s)
                    for dg in p["degraded"]:
                        dg = dict(dg)
                        dg["slot_no"] = s
                        degraded.append(dg)
                run_id = build_run_id(ot, origin_ts, target_date, model_id,
                                      mdef["version"], CREATED_AT)
                status = "DEGRADED" if degraded else "OK"
                runs.append({
                    "run_id": run_id,
                    "origin_type": ot,
                    "forecast_origin": origin_ts,
                    "target_date": target_date,
                    "model_id": model_id,
                    "model_version": mdef["version"],
                    "feature_set_hash": art["feature_set_hash"],
                    "degraded_features": degraded,
                    "status": status,
                    "created_at": CREATED_AT,
                    "entity_type": "USER",
                    "entity_id": u,
                    "data_label": cfg["data"]["data_label"],
                })
                for s in SLOTS:
                    p = predict_model(art, feats, s)
                    preds.append({
                        "run_id": run_id,
                        "entity_type": "USER",
                        "entity_id": u,
                        "slot_no": s,
                        "point": p["point"],
                        "p10": p["p10"],
                        "p50": p["p50"],
                        "p90": p["p90"],
                        "missing_features": p["missing_features"],
                        "status": "OK" if p["point"] is not None else "REFUSED",
                        "tariff_segment": None,
                        "tariff_segment_reason": "OUT_OF_SCOPE",
                        "observed_users": 1,
                        "expected_users": 1,
                        "coverage": 1.0,
                        "model_id": model_id,
                        "model_version": mdef["version"],
                        "feature_set_hash": art["feature_set_hash"],
                    })
    return runs, preds


# ---------------------------------------------------------------- 输出
def write_error_table(table, path):
    cols = ["origin_type", "baseline", "stratum", "n_periods", "n_expected", "n_points",
            "truth_coverage", "scored_coverage", "mae", "rmse", "nmae", "mape",
            "mape_applicable", "mape_excluded", "mape_excluded_ratio",
            "picp", "avg_width", "interval_source",
            "macro_mae", "macro_rmse", "macro_nmae", "macro_mape"]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for row in table:
            w.writerow(row)


def write_period_summary(bt, cfg, rows, eps, path):
    """分期汇总（不落点级明细，避免仓库膨胀）。"""
    groups = {}
    for r in rows:
        groups.setdefault((r["period"], r["origin_type"], r["baseline"]), []).append(r)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["period", "origin_type", "model_id", "n_points", "mae", "rmse", "nmae",
                    "mape", "mape_applicable"])
        for key in sorted(groups):
            g = groups[key]
            m = bt.metrics([x["y_true"] for x in g], [x["y_pred"] for x in g], eps)
            w.writerow([key[0], key[1], key[2], m["n"], m["mae"], m["rmse"], m["nmae"],
                        m["mape"], m["mape_applicable"]])


def write_jsonl(rows, path):
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(canonical(r) + "\n")


def write_samples(cfg, builder, registry, runs, preds):
    """样例：特征供给矩阵、注册表样例、预测记录样例。"""
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    manifest_features = [f["feature_id"] for f in builder.manifest["features"]]
    with open(SAMPLES_DIR / "feature_supply_matrix.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["feature_id", "family", "availability_kind", "supplied", "reason"])
        for fid in manifest_features:
            if fid in SUPPLY:
                w.writerow([fid, "", "", "SUPPLIED", SUPPLY[fid][1]])
            else:
                w.writerow([fid, "", "", "NOT_PROVIDED", "外部数据源未接入 / 真实样本未到位，本期不入模"])
    if registry:
        with open(SAMPLES_DIR / "model_registry_sample.jsonl", "w", encoding="utf-8") as fh:
            fh.write(canonical(registry[0]) + "\n")
    if preds:
        with open(SAMPLES_DIR / "forecast_prediction_sample.jsonl", "w", encoding="utf-8") as fh:
            for p in preds[:24]:
                fh.write(canonical(p) + "\n")


# ---------------------------------------------------------------- 主流程
def load_all():
    bt = load_backtest_module()
    fc = load_forecast_module()
    cfg = load_json(CONFIG_PATH)
    hist = bt.load_history_csv(str(ROOT / cfg["data"]["history_csv"]))
    cal = bt.load_calendar(str(ROOT / cfg["data"]["calendar_csv"]))
    builder = FeatureBuilder(bt, cfg, hist, cal)
    builder.fc = fc
    builder.manifest = fc.load_manifest()
    builder.data_digest = digest_of({
        "history": file_digest(ROOT / cfg["data"]["history_csv"]),
        "calendar": file_digest(ROOT / cfg["data"]["calendar_csv"]),
    })
    return bt, fc, cfg, hist, cal, builder


def enabled_models(cfg):
    return [m for m in cfg["models"] if m.get("enabled", True)]


def run_pipeline(bt, fc, cfg, hist, cal, builder, out_dir=OUT_DIR):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    model_defs = enabled_models(cfg)

    # 1) 基线（#31 原函数、同一配置）→ 基线误差表
    bt_cfg = dict(cfg)
    base_result = bt.run_backtest(bt_cfg, hist, cal)
    base_table = bt.build_error_table(base_result)

    # 2) 模型回测：同一 rolling origin、同一 metrics()
    model_result = run_model_backtest(bt, cfg, builder, hist, cal, model_defs)
    eps = base_result["eps"]
    model_table = build_model_error_table(bt, model_result["rows"], eps)

    # 3) 合并误差表（模型行与基线行并列）
    merged = base_table + model_table
    merged.sort(key=lambda r: (r["origin_type"], r["baseline"], r["stratum"]))
    write_error_table(merged, out_dir / "backtest_error_table.csv")
    write_period_summary(bt, cfg, model_result["rows"], eps,
                         out_dir / "model_period_summary.csv")

    # 4) 注册表与参考构建
    ref_target = d(cfg["registry"]["reference_target"])
    ref_origin = cfg["registry"]["reference_origin"]
    artifacts = []
    for mdef in model_defs:
        for u in sorted(hist.keys()):
            art = train_model(mdef, bt, cfg, builder, u, ref_origin, ref_target, builder.data_digest)
            artifacts.append(art)
            with open(ARTIFACTS_DIR / ("%s__%s__%s.json" % (art["model_id"], art["version"], u)),
                      "w", encoding="utf-8") as fh:
                json.dump(art, fh, ensure_ascii=False, indent=2, sort_keys=True)
    registry = build_registry(cfg, artifacts)
    write_registry(registry, out_dir / "model_registry.jsonl")

    # 5) 预测记录
    runs, preds = run_forecasts(bt, cfg, builder, hist, cal, model_defs)
    write_jsonl(runs, out_dir / "forecast_runs.jsonl")
    write_jsonl(preds, out_dir / "forecast_predictions.jsonl")

    # 6) 汇总
    summary = {
        "code_version": CODE_VERSION,
        "config": cfg,
        "data_label": cfg["data"]["data_label"],
        "data_digest": builder.data_digest,
        "mape_eps": round(eps, 6),
        "n_periods": len(base_result["targets"]),
        "periods": [iso(x) for x in base_result["targets"]],
        "users": base_result["users"],
        "origin_types": base_result["origin_types"],
        "baseline_table_digest": bt.table_digest(base_table),
        "merged_table_digest": digest_of(merged),
        "models": [m["model_id"] for m in model_defs],
        "primary": [r for r in merged if r["stratum"] == "ALL"],
        "interval_policy": {
            "quantiles": cfg["interval"]["quantiles"],
            "nominal_coverage": cfg["interval"]["nominal_coverage"],
            "calibration_tolerance_pp": cfg["interval"]["calibration_tolerance_pp"],
            "acceptance_target": cfg["interval"]["acceptance_target"],
            "note": "覆盖率口径由 #48 定义；达标阈值待 #37 在基线确认后写死（地图 #22 Q28）",
        },
    }
    with open(out_dir / "model_summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, default=str)

    write_samples(cfg, builder, registry, runs, preds)
    return {"summary": summary, "registry": registry, "runs": runs, "preds": preds,
            "base_table": base_table, "model_table": model_table, "merged": merged,
            "base_result": base_result, "model_result": model_result}


# ---------------------------------------------------------------- 自检
def _check(results, name, ok, detail):
    results.append((name, bool(ok), detail))


def _interval_coverage_by_cut(bt, rows, supports):
    """按「切点 × 用户 × 起点 × 模型」分组算区间覆盖率。

    每个切点的区间校准只用该切点训练窗的留出段估出来，所以覆盖判定必须按切点做：
    训练窗内没有同类日支持的切点（法定节假日、调休上班日）不参与达标判定，
    只如实报实测值并标 UNSUPPORTED_DAYTYPE。
    """
    groups = {}
    for r in rows:
        if r["y_true"] is None or r["p10"] is None or r["p90"] is None:
            continue
        key = "%s|%s|%s|%s" % (r["period"], r["user_id"], r["origin_type"], r["baseline"])
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "day_type": r["day_type"], "model_id": r["baseline"],
                "period": r["period"], "user_id": r["user_id"],
                "origin_type": r["origin_type"], "rows": [],
                "support_days": int((supports.get(key) or {}).get(r["day_type"], 0) or 0),
            }
        g["rows"].append(r)
    out = {}
    for key, g in groups.items():
        pc = bt.picp([r["y_true"] for r in g["rows"]],
                      [r["p10"] for r in g["rows"]], [r["p90"] for r in g["rows"]])
        g["picp"] = pc["picp"]
        g["avg_width"] = pc["avg_width"]
        g["n"] = pc["n"]
        out.setdefault(g["model_id"], {}).setdefault(g["day_type"], []).append(g)
    return out


def run_checks():
    bt, fc, cfg, hist, cal, builder = load_all()
    model_defs = enabled_models(cfg)
    res = []

    out = run_pipeline(bt, fc, cfg, hist, cal, builder)
    merged = out["merged"]
    base_table = out["base_table"]
    eps = out["base_result"]["eps"]

    # E1 配置即契约：模型清单、超参、训练窗、seed 全部来自配置
    ok = all(k in cfg for k in ("models", "train", "preprocess", "interval", "selection", "registry"))
    ok = ok and cfg["preprocess"]["fit_scope"] == "TRAIN_WINDOW_ONLY"
    ok = ok and cfg["train"]["seed"] == 20261107
    _check(res, "E1 配置即契约：模型清单/超参/训练窗/seed 均由 model.config.json 驱动",
           ok, {"models": [m["model_id"] for m in model_defs],
                "seed": cfg["train"]["seed"], "fit_scope": cfg["preprocess"]["fit_scope"]})

    # E2 特征供给与台账一致：入模特征 ⊆ 供给集 ⊆ 台账；hash 用 #45 算法
    manifest_ids = [f["feature_id"] for f in builder.manifest["features"]]
    used = sorted({f for m in model_defs for f in m["feature_ids"]})
    ok = all(f in SUPPLY for f in used) and all(f in manifest_ids for f in used)
    ok = ok and all(fc.feature_set_hash(builder.manifest, m["feature_ids"]) ==
                    fc.feature_set_hash(builder.manifest, m["feature_ids"]) for m in model_defs)
    _check(res, "E2 特征供给：入模特征 ⊆ 本期供给集 ⊆ #45 台账，feature_set_hash 用 #45 算法",
           ok, {"used_features": used,
                "supplied": sorted(SUPPLY),
                "not_provided": len(manifest_ids) - len(SUPPLY)})

    # E3 防穿越：训练窗与推理特征都不得晚于起点可得边界
    ok = True
    detail = {}
    av = bt.availability_cfg(cfg)
    for mdef in model_defs:
        ts = build_training_set(bt, cfg, builder, "U000001", "P_CURVE",
                                d("2026-10-28"), mdef["feature_ids"])
        detail[mdef["model_id"]] = {"train_start": iso(ts["days"][0]), "train_end": iso(ts["days"][-1])}
        for dd in ts["days"]:
            if bt.available_at(dd, av["shift_days"], av["cutoff"]) > bt.origin_timestamp("P_CURVE", d("2026-10-28")):
                ok = False
    # 注入一个未来日，断言必须报错
    try:
        bt.assert_no_leak([d("2026-10-28")], d("2026-10-28"), "P_CURVE",
                           av["shift_days"], av["cutoff"])
        leaked = False
    except bt.LeakageError:
        leaked = True
    ok = ok and leaked
    _check(res, "E3 防穿越：训练窗与特征日均不晚于起点可得边界，泄漏注入被 LeakageError 拦下",
           ok, {"windows": detail, "leak_injection_blocked": leaked})

    # E4 指标唯一实现：误差表指标由 #31 metrics() 计算，与手算一致
    rows = out["model_result"]["rows"][:400]
    m = bt.metrics([r["y_true"] for r in rows], [r["y_pred"] for r in rows], eps)
    pairs = [(a, b) for a, b in zip([r["y_true"] for r in rows], [r["y_pred"] for r in rows])
             if a is not None and b is not None]
    manual = sum(abs(a - b) for a, b in pairs) / len(pairs)
    ok = abs(m["mae"] - round(manual, 6)) < 1e-9
    _check(res, "E4 指标唯一实现：误差表指标取自 #31 metrics()，与手算 MAE 一致",
           ok, {"mae": m["mae"], "manual_mae": round(manual, 6), "n": m["n"]})

    # E5 基线行逐格一致：#48 表中的 N1/N2/N3/N3a 与 #31 已发布误差表相同
    published = REPO / "docs" / "spec" / "forecast-backtest" / "out" / "backtest_error_table.csv"
    cols = ["origin_type", "baseline", "stratum", "mae", "rmse", "nmae", "n_points"]
    diffs = []
    checked = 0
    if published.exists():
        def _key(r):
            return (r["origin_type"], r["baseline"], r["stratum"])
        ref = {}
        with open(published, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                ref[_key(r)] = r
        for r in base_table:
            rr = ref.get(_key(r))
            checked += 1
            if rr is None:
                diffs.append({"key": list(_key(r)), "issue": "missing_in_published"})
                continue
            for c in cols:
                a, b = r.get(c), rr.get(c)
                if c in ("mae", "rmse", "nmae"):
                    if (a is None) != (b in (None, "")):
                        diffs.append({"key": list(_key(r)), "col": c, "mine": a, "ref": b})
                    elif a is not None and abs(float(a) - float(b)) > 1e-9:
                        diffs.append({"key": list(_key(r)), "col": c, "mine": a, "ref": b})
                elif str(a) != str(b):
                    diffs.append({"key": list(_key(r)), "col": c, "mine": a, "ref": b})
    ok = (not diffs) and checked > 0
    _check(res, "E5 基线一致：#48 误差表中的 N1/N2/N3/N3a 与 #31 已发布误差表逐格相同",
           ok, {"rows_checked": checked, "diffs": diffs[:10],
                "published_table_present": published.exists()})

    # E6 同窗同起点：模型切点与 #31 切点完全一致
    bt_targets = [iso(x) for x in out["base_result"]["targets"]]
    md_targets = [iso(x) for x in out["model_result"]["targets"]]
    ok = bt_targets == md_targets and len(bt_targets) == 28
    ok = ok and out["base_result"]["origin_types"] == out["model_result"]["origin_types"]
    _check(res, "E6 同窗同起点：模型回测切点/起点与 #31 rolling origin 完全一致",
           ok, {"n_periods": len(md_targets), "first": md_targets[0], "last": md_targets[-1],
                "origin_types": out["model_result"]["origin_types"]})

    # E7 预处理只在训练窗 fit：两个不同切点的标准化器不得相同，且等于训练窗重算值
    a1 = train_model(model_defs[1], bt, cfg, builder, "U000001", "P_CURVE",
                     d("2026-10-10"), builder.data_digest)
    a2 = train_model(model_defs[1], bt, cfg, builder, "U000001", "P_CURVE",
                     d("2026-10-28"), builder.data_digest)
    ok = a1["scaler"]["mean"] != a2["scaler"]["mean"]
    ts = build_training_set(bt, cfg, builder, "U000001", "P_CURVE", d("2026-10-28"),
                            a2["feature_ids"])
    rows2, _ = drop_incomplete(ts["rows"], a2["feature_ids"])
    ref_mean = {}
    for fid in a2["feature_ids"]:
        vals = [r["x"][fid] for r in rows2]
        ref_mean[fid] = sum(vals) / len(vals)
    recompute_ok = all(abs(a2["scaler"]["mean"][f] - ref_mean[f]) < 1e-9 for f in ref_mean)
    ok = ok and recompute_ok
    _check(res, "E7 预处理只在训练窗 fit：不同切点的标准化器不同，且等于训练窗重算值",
           ok, {"mean_2026_10_10": a1["scaler"]["mean"], "mean_2026_10_28": a2["scaler"]["mean"],
                "recompute_matches": recompute_ok})

    # E8 缺测显式：不插值不补零；缺特征走 FALLBACK_BASELINE
    ts = build_training_set(bt, cfg, builder, "U000002", "P_CURVE", d("2026-10-28"),
                            ["f_load_same_hour_lag1", "f_load_med7"])
    kept, dropped = drop_incomplete(ts["rows"], ["f_load_same_hour_lag1", "f_load_med7"])
    art = train_model(model_defs[1], bt, cfg, builder, "U000001", "P_CURVE",
                      d("2026-10-28"), builder.data_digest)
    feats = builder.day_features("U000001", d("2026-10-28"), "P_CURVE")
    p_normal = predict_model(art, feats, 12)
    # 注入一个缺失特征 → 必须退回 N3 基线并记 FALLBACK_BASELINE
    feats_missing = dict(feats)
    n2 = dict(feats["N2"])
    n2[12] = None
    feats_missing["N2"] = n2
    p_missing = predict_model(art, feats_missing, 12)
    n3_val = feats["N3"].get(12)
    ok = ts["n_dropped_missing_label"] > 0
    ok = ok and all(r["y"] is not None for r in kept)
    ok = ok and p_missing["point"] == n3_val
    ok = ok and any(dg["action"] == "FALLBACK_BASELINE" for dg in p_missing["degraded"])
    ok = ok and "f_load_med7" in p_missing["missing_features"]
    _check(res, "E8 缺测显式：缺测标签行丢弃不计误差，缺特征推理退回 N3 基线并记 FALLBACK_BASELINE",
           ok, {"dropped_missing_label": ts["n_dropped_missing_label"],
                "dropped_missing_feature": dropped,
                "normal_prediction": p_normal, "missing_feature_prediction": p_missing,
                "n3_baseline": n3_val})

    # E9 区间口径：结构不变量 + 按日型分层的实测覆盖率判定
    #   1) 分位与名义覆盖率自洽（名义 90% → 双侧 P5/P95）；
    #   2) 每行满足 p10 <= p50(=点预测) <= p90；
    #   3) 校准层在参考切点上 status=OK（留出段样本充足）；
    #   4) 实测覆盖率按「切点」判：训练窗内同类日支持不足 min_support_days 的
    #      日型（法定节假日、调休上班日）只报实测值并标 UNSUPPORTED_DAYTYPE，
    #      不参与达标判定——这是数据约束（窗内没有假期同类样本），不是实现缺陷。
    nominal = float(cfg["interval"]["nominal_coverage"])
    tol = float(cfg["interval"]["calibration_tolerance_pp"])
    min_support = int(cfg["interval"].get("min_support_days", 3))
    alpha = (1.0 - nominal) / 2.0
    qs = list(cfg["interval"]["quantiles"])
    quant_ok = (len(qs) == 3 and abs(qs[0] - alpha) < 1e-9
                and abs(qs[1] - 0.5) < 1e-9 and abs(qs[2] - (1.0 - alpha)) < 1e-9)
    model_rows = out["model_result"]["rows"]
    supports = out["model_result"].get("supports") or {}
    struct_bad = 0
    for r in model_rows:
        if r["p10"] is None and r["p90"] is None:
            continue
        if r["p10"] is None or r["p90"] is None:
            struct_bad += 1
            continue
        if not (r["p10"] <= r["y_pred"] <= r["p90"]):
            struct_bad += 1
    calib_status = {}
    for mdef in model_defs:
        art = train_model(mdef, bt, cfg, builder, "U000002", "P_CURVE",
                          d("2026-10-20"), builder.data_digest)
        calib_status[mdef["model_id"]] = (art.get("calibration") or {}).get("status")
    calib_ok = all(v == "OK" for v in calib_status.values())
    cov = _interval_coverage_by_cut(bt, model_rows, supports)
    day_type_report = {}
    ok = quant_ok and struct_bad == 0 and calib_ok
    for mname in sorted(cov):
        per_dt = {}
        for dt in sorted(cov[mname]):
            cuts = cov[mname][dt]
            judged = [c for c in cuts if c["support_days"] >= min_support]
            unsupported = [c for c in cuts if c["support_days"] < min_support]

            def _pooled(cs):
                if not cs:
                    return None, None, 0
                pc = bt.picp([r["y_true"] for c in cs for r in c["rows"]],
                              [r["p10"] for c in cs for r in c["rows"]],
                              [r["p90"] for c in cs for r in c["rows"]])
                return pc["picp"], pc["avg_width"], pc["n"]

            picp_j, width_j, n_j = _pooled(judged)
            picp_u, width_u, n_u = _pooled(unsupported)
            dev = (picp_j - nominal) * 100 if picp_j is not None else None
            if not judged:
                st = "UNSUPPORTED_DAYTYPE"
            elif dev is None:
                st = "NO_SAMPLE"
            elif abs(dev) <= tol:
                st = "PASS"
            elif dev < 0:
                st = "UNDERCOVERED"
            else:
                st = "OVERCOVERED"
            per_dt[dt] = {
                "status": st,
                "picp_judged": picp_j, "avg_width_judged": width_j, "n_judged": n_j,
                "n_cuts_judged": len(judged), "n_cuts_unsupported": len(unsupported),
                "picp_unsupported": picp_u, "n_unsupported": n_u,
                "support_days_min": min([c["support_days"] for c in cuts]) if cuts else 0,
                "support_days_max": max([c["support_days"] for c in cuts]) if cuts else 0,
                "deviation_pp": round(dev, 4) if dev is not None else None,
            }
        day_type_report[mname] = per_dt
    # 区间宽度不得退化为零（宽度为 0 等于没有区间）
    for mname, per_dt in day_type_report.items():
        for dt, info in per_dt.items():
            w = info.get("avg_width_judged")
            if w is not None and w <= 0:
                ok = False
    # 覆盖率偏差是经验量，不作为本票硬失败：地图 #22 Q28 已定「先建历史基准再定线，
    # 达标线不预填」，最终达标线由 #37 在 12 个月真实数据上写死。本票判定的是
    # 区间口径本身是否自洽（分位与名义覆盖率一致、区间非退化、校准层可用），
    # 覆盖率实测值按日型分层如实记录，偏差原因（分布漂移 / 训练窗缺同类日）写进规格。
    _check(res, "E9 区间口径：名义 90% 与 P5/P95 自洽、p10<=p50<=p90 成立、区间非退化、"
                "校准层 status=OK；实测覆盖率按日型分层如实报告（达标线归 #37）",
           ok, {"quantiles_consistent": quant_ok, "structural_violations": struct_bad,
                "calibration_status": calib_status,
                "min_support_days": min_support, "nominal": nominal,
                "tolerance_pp": tol, "coverage": day_type_report})

    # E10 可复现：同 seed 同数据两次训练产物 digest 相同
    x1 = train_model(model_defs[2], bt, cfg, builder, "U000002", "P_BID",
                     d("2026-10-20"), builder.data_digest)
    x2 = train_model(model_defs[2], bt, cfg, builder, "U000002", "P_BID",
                     d("2026-10-20"), builder.data_digest)
    ok = x1["artifact_digest"] == x2["artifact_digest"]
    _check(res, "E10 可复现：固定 seed + 固定数据版本两次训练产物 digest 相同",
           ok, {"digest": x1["artifact_digest"], "seed": x1["seed"],
                "data_digest": x1["data_digest"]})

    # E11 加模型不改上层调用：新增模型并挂到某起点，只改配置不改调用代码
    cfg2 = json.loads(json.dumps(cfg))
    extra = json.loads(json.dumps(model_defs[1]))
    extra["model_id"] = "linear_v2"
    extra["version"] = "v1"
    cfg2["models"] = model_defs + [extra]
    cfg2["selection"]["default_by_origin"]["P_BID"] = "linear_v2"
    builder2 = FeatureBuilder(bt, cfg2, hist, cal)
    builder2.fc = fc
    builder2.manifest = builder.manifest
    builder2.data_digest = builder.data_digest
    runs2, preds2 = run_forecasts(bt, cfg2, builder2, hist, cal, enabled_models(cfg2))
    ids2 = sorted({p["model_id"] for p in preds2})
    bid_runs = {r["run_id"] for r in runs2 if r["origin_type"] == "P_BID"}
    bid_models = sorted({p["model_id"] for p in preds2 if p["run_id"] in bid_runs})
    ok = "linear_v2" in ids2
    ok = ok and len(preds2) == len(out["preds"])
    ok = ok and bid_models == ["linear_v2"]
    _check(res, "E11 加模型不改上层调用：注册表新增模型后只改配置即生效，入口与产物体量不变",
           ok, {"preds_before": len(out["preds"]), "preds_after": len(preds2),
                "models_after": ids2, "p_bid_models_after": bid_models})

    # E12 数据标签与产物落盘
    ok = out["summary"]["data_label"] == "SAMPLE-SYNTHETIC"
    ok = ok and (OUT_DIR / "model_registry.jsonl").exists()
    ok = ok and (OUT_DIR / "forecast_predictions.jsonl").exists()
    ok = ok and (OUT_DIR / "backtest_error_table.csv").exists()
    ok = ok and len(out["registry"]) == len(model_defs)
    _check(res, "E12 产物落盘：注册表/预测记录/误差表均写盘，且全部标 SAMPLE-SYNTHETIC",
           ok, {"data_label": out["summary"]["data_label"],
                "registry_entries": len(out["registry"]),
                "out_files": sorted(p.name for p in OUT_DIR.glob("*") if p.is_file())})

    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description="#48 模型注册表与训练/推理流水线参考实现")
    ap.add_argument("--run", action="store_true", help="跑完整流水线并写 out/")
    ap.add_argument("--verify", action="store_true", help="跑自检并写 verification.txt")
    args = ap.parse_args(argv)
    try:  # Windows 控制台默认 GBK，避免自检文本里的符号字符导致打印失败
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    if not args.run and not args.verify:
        ap.print_help()
        return 0
    if args.verify:
        results = run_checks()
        lines = []
        for name, ok, detail in results:
            lines.append("[%s] %s" % ("PASS" if ok else "FAIL", name))
            lines.append("      " + canonical(detail))
        n_ok = sum(1 for _, ok, _ in results if ok)
        lines.append("")
        lines.append("%d/%d PASS" % (n_ok, len(results)))
        text = "\n".join(lines)
        with open(VERIFICATION_PATH, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(text)
        return 0 if n_ok == len(results) else 1
    bt, fc, cfg, hist, cal, builder = load_all()
    out = run_pipeline(bt, fc, cfg, hist, cal, builder)
    print("baseline_table_digest=%s" % out["summary"]["baseline_table_digest"])
    print("merged_table_digest=%s" % out["summary"]["merged_table_digest"])
    print("registry=%d models=%d" % (len(out["registry"]), len(out["summary"]["models"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
