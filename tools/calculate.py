"""CNCA 三体系 人日 / 抽样比例 / 期限 计算器。

设计约定
--------
1. 规则数值一律不写在代码里，全部从 tools/params.yaml 读取；
   参数表里没有的数值视为「参数待核」，调用时报错，不猜值。
2. 每个计算都返回结构化结果，字段固定为：
   inputs / units / basis / formula / rounding / result / recomputed /
   source_ref / verified。
   - inputs      原始输入的只读副本（不覆盖调用方的对象）
   - units       各输入的计量单位
   - basis       口径说明（这个数字到底按什么口径算）
   - formula     公式（可代入复算）
   - rounding    舍入规则
   - result      计算结果 + 结论
   - recomputed  独立路径的复算值（应与 result 一致）
   - source_ref  CNCA 条款号
   - verified    参与计算的参数是否全部已核验
3. 期限按日历月处理，不按 30 天折算；起算当日不计入，届满当日算届满
   （该口径写在 params.yaml 的 conventions 段，是工具约定，不是规则数值）。

CLI
---
    python tools/calculate.py man-days --profile qms --headcount 120 --risk medium --sites 3
    python tools/calculate.py ratio --check onsite-ratio --total 40 --onsite 30
    python tools/calculate.py deadline --kind nc-verification --audit-type initial --start 2026-06-01
    python tools/calculate.py deadline --kind certificate-expiry --issue 2026-06-01
    python tools/calculate.py params --list

退出码：0 完成；1 输入错误；2 参数待核；3 参数表/依赖错误；4 --strict 且判定不合规。
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:  # 同目录导入（脚本直接运行）
    from calendar_ratio import (
        HAS_DATEUTIL,
        add_days,
        add_months,
        add_years,
        at_least,
        ceil_fraction,
        days_between,
        num,
        parse_date,
        ratio,
        round_to_step,
        to_fraction,
        whole_months_between,
    )
except ImportError:  # pragma: no cover - 以包方式导入时
    from .calendar_ratio import (  # type: ignore
        HAS_DATEUTIL,
        add_days,
        add_months,
        add_years,
        at_least,
        ceil_fraction,
        days_between,
        num,
        parse_date,
        ratio,
        round_to_step,
        to_fraction,
        whole_months_between,
    )

DEFAULT_PARAMS_PATH = Path(__file__).resolve().with_name("params.yaml")

PROFILE_LABELS = {"qms": "QMS", "ems": "EMS", "ohsms": "OHSMS"}
RISK_ALIASES = {
    "low": "low",
    "medium": "medium",
    "mid": "medium",
    "middle": "medium",
    "high": "high",
    "limited": "limited",
    "special": "special",
}
AUDIT_TYPES = ("initial", "supervision", "recert")
AUDIT_TYPE_LABELS = {
    "initial": "初次认证审核",
    "supervision": "监督审核",
    "recert": "再认证审核",
}
SITE_TYPES = ("similar", "non-similar")
SITE_TYPE_LABELS = {"similar": "相似场所", "non-similar": "非相似场所"}


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class ParamError(Exception):
    """参数缺失或参数待核（verified=false）。"""


class InputError(Exception):
    """调用方输入不合法。"""


class CalcError(Exception):
    """结果自检失败（如复算值与结果不一致）。"""


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------
@dataclass
class CalcResult:
    """固定 9 个字段的计算结果。"""

    inputs: Dict[str, Any]
    units: Dict[str, str]
    basis: str
    formula: str
    rounding: str
    result: Dict[str, Any]
    recomputed: Dict[str, Any]
    source_ref: List[str]
    verified: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "inputs": self.inputs,
            "units": self.units,
            "basis": self.basis,
            "formula": self.formula,
            "rounding": self.rounding,
            "result": self.result,
            "recomputed": self.recomputed,
            "source_ref": self.source_ref,
            "verified": self.verified,
        }


# ---------------------------------------------------------------------------
# 参数表读取
# ---------------------------------------------------------------------------
class ParamStore:
    """tools/params.yaml 的只读访问器。"""

    def __init__(self, data: Dict[str, Any], path: Optional[Path] = None):
        self.data = data
        self.path = path

    # -- 加载 ---------------------------------------------------------------
    @classmethod
    def load(cls, path: Optional[str] = None) -> "ParamStore":
        target = Path(path) if path else DEFAULT_PARAMS_PATH
        if not target.exists():
            raise ParamError("参数表不存在：%s" % target)
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover
            raise ParamError("缺少 PyYAML 依赖，无法读取参数表：%s" % target) from exc
        try:
            with target.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        except Exception as exc:
            raise ParamError("参数表解析失败：%s（%s）" % (target, exc)) from exc
        if not isinstance(data, dict):
            raise ParamError("参数表结构异常：%s" % target)
        return cls(data, target)

    # -- 访问 ---------------------------------------------------------------
    def get(self, dotted: str) -> Any:
        node: Any = self.data
        for key in dotted.split("."):
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                raise ParamError("参数缺失：%s" % dotted)
        return node

    def source_ref(self, dotted: str) -> List[str]:
        try:
            node = self.get(dotted)
        except ParamError:
            return []
        if not isinstance(node, dict):
            return []
        ref = node.get("source_ref")
        if ref is None:
            return []
        if isinstance(ref, str):
            return [ref]
        return [str(item) for item in ref]

    def md_ref(self, dotted: str) -> Optional[str]:
        try:
            node = self.get(dotted)
        except ParamError:
            return None
        if isinstance(node, dict):
            return node.get("md_ref")
        return None

    def _allow_primary_only(self) -> bool:
        try:
            node = self.get("meta.verification_policy.allow_primary_source_only")
        except ParamError:
            return True
        return bool(node)

    def is_verified(self, dotted: str) -> bool:
        node = self.get(dotted)
        if not isinstance(node, dict):
            return True
        if node.get("verified", True):
            return True
        # 明确声明「一手规则原文也没有数值」：任何情况下都不放行
        if node.get("primary_source_verified") is False:
            return False
        # 工具约定不是规则数值，不适用「一手原文已核验」政策
        if node.get("class") == "tool_convention":
            return False
        # md 未载明、但一手规则原文已核验：按参数表策略放行
        if node.get("md_ref") is None and self._allow_primary_only():
            return True
        return False

    def md_row_verified(self, md_ref: Optional[str]) -> bool:
        """附录 B 行级核验：md 载有的行直接可用；md 未载的行按参数表策略放行。"""
        if md_ref:
            return True
        return self._allow_primary_only()

    def is_blocking(self, dotted: str) -> bool:
        node = self.get(dotted)
        if isinstance(node, dict):
            return bool(node.get("blocking", True))
        return True

    def require(self, dotted: str) -> Tuple[Any, bool]:
        """取参数节点。

        返回 (节点, 是否已核验)。未核验且 blocking=true 时抛 ParamError
        （消息为「参数待核」），blocking=false 时不阻断，仅把已核验标记为 False。
        """
        node = self.get(dotted)
        verified = self.is_verified(dotted)
        if not verified and self.is_blocking(dotted):
            refs = "; ".join(self.source_ref(dotted)) or "（无来源记录）"
            reason = ""
            if isinstance(node, dict):
                reason = node.get("reason") or node.get("note") or ""
            raise ParamError(
                "参数待核：%s（来源：%s）%s" % (dotted, refs, ("；" + reason) if reason else "")
            )
        return node, verified

    def value(self, dotted: str) -> Tuple[Any, bool]:
        node, verified = self.require(dotted)
        if not isinstance(node, dict) or "value" not in node:
            raise ParamError("参数不是标量条目：%s" % dotted)
        return node["value"], verified

    def fraction(self, dotted: str) -> Tuple[Fraction, bool]:
        raw, verified = self.value(dotted)
        return to_fraction(raw), verified

    def ratio_fraction(self, dotted: str) -> Tuple[Fraction, bool]:
        node, verified = self.require(dotted)
        return Fraction(int(node["numerator"]), int(node["denominator"])), verified

    def fraction_pair(self, numerator: int, denominator: int) -> Fraction:
        return ratio(numerator, denominator)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _norm_profile(profile: str) -> str:
    key = str(profile).strip().lower()
    if key not in PROFILE_LABELS:
        raise InputError(
            "未知体系：%s（可选 qms / ems / ohsms）" % profile
        )
    return key


def _norm_risk(risk: str) -> str:
    key = str(risk).strip().lower()
    if key not in RISK_ALIASES:
        raise InputError(
            "未知风险类型：%s（可选 low / medium / high；EMS 另有 limited / special）" % risk
        )
    return RISK_ALIASES[key]


def _norm_audit_type(audit_type: str) -> str:
    key = str(audit_type).strip().lower()
    if key not in AUDIT_TYPES:
        raise InputError("未知审核类型：%s（可选 initial / supervision / recert）" % audit_type)
    return key


def _norm_site_type(site_type: str) -> str:
    key = str(site_type).strip().lower()
    if key not in SITE_TYPES:
        raise InputError("未知场所类型：%s（可选 similar / non-similar）" % site_type)
    return key


def _frac_str(value: Fraction) -> str:
    """把分数写成 3/4 或 4 这样的形式，便于人工复算。"""
    if value.denominator == 1:
        return str(value.numerator)
    return "%d/%d" % (value.numerator, value.denominator)


def _f(value: Fraction):
    return num(value)


# ---------------------------------------------------------------------------
# 1. 人日计算
# ---------------------------------------------------------------------------
def round_days_via_hours(value, step=0.5, hours_per_day=8):
    """在小时域复算人日并折回人日。

    先把人日换算成小时，在小时域按「步长 × 每日小时数」的步长取整，再折回人日。
    与在人日域直接取整等价（1 人日 = 8 小时、半人日步长 0.5 → 小时步长 4），
    但走的是另一条算术路径，可暴露换算或步长写错的缺陷。

    注意：折回后不能再用 round_to_step(..., Fraction(1)) 收尾 —— 步长 1 遇到恰好一半
    （如 6.5 天）会按「居中向上」变成 7 天，凭空造出复算差异。
    """
    f_val = to_fraction(value)
    s_val = to_fraction(step)
    h = int(hours_per_day)
    return round_to_step(round_to_step(f_val * h, s_val * h) / h, s_val)


def appendix_b_days(profile: str, headcount: int, risk: str = "medium",
                    params: Optional[ParamStore] = None) -> CalcResult:
    """按附录 B 查有效人数区间对应的人日（含 QMS 风险调整）。"""
    store = params or ParamStore.load()
    profile = _norm_profile(profile)
    risk = _norm_risk(risk)
    headcount = int(headcount)
    if headcount < 1:
        raise InputError("有效人数必须 >= 1：%s" % headcount)

    table = store.get("man_day.%s.table" % profile)
    mode = store.get("man_day.%s.mode" % profile)
    row = None
    for item in table:
        if int(item["min"]) <= headcount <= int(item["max"]):
            row = item
            break
    if row is None:
        # 超出附录 B 表格范围：>10700 行没有具体数值，按「参数待核」处理
        store.require("man_day.%s.above_max" % profile)
        raise InputError("有效人数 %d 不在附录 B 表格范围内" % headcount)

    if not store.md_row_verified(row.get("md_ref")):
        raise ParamError(
            "参数待核：man_day.%s.table 中有效人数 %d 所在行未在一手规则原文核验"
            % (profile, headcount)
        )

    verified = True
    base_raw: Fraction
    band = "%s-%s" % (row["min"], row["max"])
    risk_note = ""
    factor: Optional[Fraction] = None

    if mode == "base_plus_risk_factor":
        if risk not in ("low", "medium", "high"):
            raise InputError(
                "%s 附录 B 只有低/中/高三个风险档，没有「%s」档"
                % (PROFILE_LABELS[profile], risk)
            )
        base_raw = to_fraction(row["days"])
        node, ok = store.require("man_day.%s.risk_adjust.%s" % (profile, risk))
        verified = verified and ok
        factor = to_fraction(node["factor"])
        risk_note = node.get("direction", "")
        adjusted = base_raw * factor
    elif mode == "risk_column":
        columns = row["days"]
        if risk in ("limited", "special") and profile != "ems":
            raise InputError(
                "%s 附录 B 没有「%s」档（该档仅 EMS 附录 B 涉及）"
                % (PROFILE_LABELS[profile], risk)
            )
        if risk == "limited":
            node, ok = store.require("man_day.ems.limited_column")
            verified = verified and ok
            risk_note = node.get("reason", "")
        elif risk == "special":
            node, ok = store.require("man_day.ems.special_complexity")
            verified = verified and ok
            risk = node.get("floor", "high")
            risk_note = node.get("reason", "")
        if risk not in columns:
            raise InputError("附录 B 表没有风险类型「%s」的列（可选 %s）" % (risk, ", ".join(columns)))
        base_raw = to_fraction(columns[risk])
        adjusted = base_raw
    else:
        raise InputError("未知的附录 B 取值模式：%s" % mode)

    step_node, step_ok = store.require("conventions.half_man_day_rounding")
    verified = verified and step_ok
    step = to_fraction(step_node["step"])
    hours_per_day, hours_ok = store.value("units.man_day_hours")
    verified = verified and hours_ok

    result_days = round_to_step(adjusted, step)
    hours = result_days * int(hours_per_day)

    # 独立路径复算：走小时域（详见 round_days_via_hours 的说明）
    recomputed_days = round_days_via_hours(adjusted, step, int(hours_per_day))
    if recomputed_days != result_days:
        raise CalcError(
            "复算不一致：result=%s recomputed=%s" % (_frac_str(result_days), _frac_str(recomputed_days))
        )

    basis = (
        "有效人数口径：认证范围内涉及的所有人员（含每个班次人员），"
        "含承包商等非固定人员与兼职人员；附录 B 数值口径为「第1阶段＋第2阶段」审核时间；"
        "1 人日 = 8 小时（5.4.2.1）。"
        + ("风险调整口径：" + risk_note if risk_note else "")
    )
    formula = "人日 = 附录B 区间[%s] 的 %s%s" % (
        band,
        _frac_str(base_raw),
        (" × 风险系数 %s" % _frac_str(factor)) if factor is not None else "",
    )
    rounding = "按 5.4.2.3「结果有小数宜调整为最接近的半人日数」取整（步长 0.5，正好居中时向上取）"

    result = {
        "profile": PROFILE_LABELS[profile],
        "headcount_band": band,
        "risk": risk,
        "base_days": _f(base_raw),
        "risk_factor": _f(factor) if factor is not None else None,
        "adjusted_days": _f(adjusted),
        "adjusted_days_exact": _frac_str(adjusted),
        "total_man_days": _f(result_days),
        "total_hours": _f(hours),
    }
    recomputed = {
        "total_man_days": _f(recomputed_days),
        "total_hours": _f(recomputed_days * int(hours_per_day)),
        "path": "人日 × 8 小时 → 小时取整 → ÷ 8 → 取最接近半人日",
    }
    source_ref = store.source_ref("man_day.%s.table" % profile) + [
        ref
        for ref in store.source_ref("man_day.%s.risk_adjust.%s" % (profile, risk))
    ] + store.source_ref("units.man_day_hours")

    return CalcResult(
        inputs=copy.deepcopy(
            {"profile": profile, "headcount": headcount, "risk": risk}
        ),
        units={"headcount": "人", "days": "人日", "hours": "小时"},
        basis=basis,
        formula=formula,
        rounding=rounding,
        result=result,
        recomputed=recomputed,
        source_ref=sorted(set(source_ref)),
        verified=verified,
    )


def man_days(profile: str, headcount: int, risk: str = "medium", sites: int = 1,
             audit_type: str = "initial", site_type: str = "similar",
             params: Optional[ParamStore] = None) -> CalcResult:
    """人日计算：附录 B 基础值 + 风险调整 + 审核类型系数 + 多场所抽样与分配。"""
    store = params or ParamStore.load()
    profile = _norm_profile(profile)
    risk = _norm_risk(risk)
    audit_type = _norm_audit_type(audit_type)
    site_type = _norm_site_type(site_type)
    sites = int(sites)
    if sites < 1:
        raise InputError("场所数必须 >= 1：%s" % sites)

    base = appendix_b_days(profile, headcount, risk, params=store)
    adjusted = to_fraction(base.result["adjusted_days_exact"])

    # 审核类型系数：监督 >= 初次 1/3；再认证 >= 初次 2/3
    type_factor = Fraction(1)
    type_refs: List[str] = []
    ok = True
    if audit_type == "supervision":
        type_factor, ok = store.ratio_fraction("ratio.surveillance_min")
        type_refs = store.source_ref("ratio.surveillance_min")
    elif audit_type == "recert":
        type_factor, ok = store.ratio_fraction("ratio.recert_min")
        type_refs = store.source_ref("ratio.recert_min")
    verified = base.verified and ok

    total_raw = adjusted * type_factor

    step_node, step_ok = store.require("conventions.half_man_day_rounding")
    verified = verified and step_ok
    step = to_fraction(step_node["step"])
    hours_per_day, hours_ok = store.value("units.man_day_hours")
    verified = verified and hours_ok
    total_days = round_to_step(total_raw, step)

    # 抽样
    sampling = sampling_count(sites, audit_type, site_type, params=store)
    sampled = int(sampling.result["sampled_sites"])
    verified = verified and sampling.verified

    # 分场所人日（工具约定：总人日 ÷ 抽样场所数，再取最接近半人日）
    per_site_raw = total_days / sampled
    per_site_days = round_to_step(per_site_raw, step)
    allocated = per_site_days * sampled

    onsite_min, onsite_ok = store.fraction("ratio.onsite_min")
    verified = verified and onsite_ok
    min_onsite = total_days * onsite_min

    per_site_min, per_site_ok = store.fraction("ratio.per_site_onsite_min")
    verified = verified and per_site_ok
    min_onsite_per_site = per_site_days * per_site_min

    reduce_cap, cap_ok = store.fraction("ratio.max_reduction_vs_appendix_b")
    verified = verified and cap_ok
    # 5.4.2.3 的 30% 减时上限以附录 B 所规定的审核时间为基数
    min_allowed = adjusted * (Fraction(1) - reduce_cap)

    basis = (
        base.basis
        + " 监督/再认证审核时间以附录 B 确定的初次认证审核时间为基数（5.7.4 / 5.8.4，比例是下限）。"
        + " 现场审核时间下限：不少于所确定审核时间的 80%（5.4.2.3）；分场所现场审核时间下限："
        + "不少于依据附录 B 所确定现场审核时间的 50%（5.4.3.5）。"
    )
    formula = (
        "总人日 = 附录B 区间值 %s × 风险系数 %s × 审核类型系数 %s；"
        "抽样场所数 Y = %s；分场所人日 = 总人日 ÷ Y"
        % (
            base.result["adjusted_days_exact"],
            _frac_str(to_fraction(base.result["risk_factor"])) if base.result["risk_factor"] is not None else "1",
            _frac_str(type_factor),
            sampling.result["sampling_formula"],
        )
    )
    rounding = (
        "总人日按最接近的半人日数取整（步长 0.5，正好居中向上取）；"
        "分场所人日同样取最接近的半人日，分配合计与总人日的差额单列"
    )

    result = {
        "profile": PROFILE_LABELS[profile],
        "audit_type": AUDIT_TYPE_LABELS[audit_type],
        "risk": risk,
        "headcount": int(headcount),
        "headcount_band": base.result["headcount_band"],
        "base_days": base.result["base_days"],
        "risk_factor": base.result["risk_factor"],
        "adjusted_days": base.result["adjusted_days"],
        "audit_type_factor": _frac_str(type_factor),
        "total_man_days": _f(total_days),
        "total_man_days_exact": _frac_str(total_days),
        "total_hours": _f(total_days * int(hours_per_day)),
        "min_onsite_man_days": _f(min_onsite),
        "min_allowed_after_reduction": _f(round_to_step(min_allowed, step)),
        "per_site": {
            "sites_total": sites,
            "site_type": SITE_TYPE_LABELS[site_type],
            "sampled_sites": sampled,
            "per_site_man_days": _f(per_site_days),
            "allocated_total": _f(allocated),
            "allocation_difference": _f(allocated - total_days),
            "min_onsite_per_site_man_days": _f(min_onsite_per_site),
        },
        "sampling": sampling.result,
    }
    recomputed_days = round_days_via_hours(total_raw, step, int(hours_per_day))
    recomputed_per_site = round_days_via_hours(total_days / sampled, step, int(hours_per_day))
    recomputed = {
        "total_man_days": _f(recomputed_days),
        "total_hours": _f(recomputed_days * int(hours_per_day)),
        "per_site_man_days": _f(recomputed_per_site),
        "path": "先算精确人日 → × 8 小时 → 小时取整 → ÷ 8 → 取最接近半人日",
    }
    if recomputed_days != total_days:
        raise CalcError("复算不一致：总人日 %s vs %s" % (_frac_str(total_days), _frac_str(recomputed_days)))

    source_ref = list(base.source_ref) + list(type_refs)
    source_ref += store.source_ref("ratio.onsite_min")
    source_ref += store.source_ref("ratio.per_site_onsite_min")
    source_ref += store.source_ref("ratio.max_reduction_vs_appendix_b")
    source_ref += sampling.source_ref

    return CalcResult(
        inputs=copy.deepcopy(
            {
                "profile": profile,
                "headcount": int(headcount),
                "risk": risk,
                "sites": sites,
                "audit_type": audit_type,
                "site_type": site_type,
            }
        ),
        units={"headcount": "人", "days": "人日", "hours": "小时", "sites": "个场所"},
        basis=basis,
        formula=formula,
        rounding=rounding,
        result=result,
        recomputed=recomputed,
        source_ref=sorted(set(source_ref)),
        verified=verified,
    )


# ---------------------------------------------------------------------------
# 2. 比例计算
# ---------------------------------------------------------------------------
def sampling_count(sites: int, audit_type: str = "initial", site_type: str = "similar",
                   params: Optional[ParamStore] = None) -> CalcResult:
    """多场所抽样数量 Y。"""
    store = params or ParamStore.load()
    audit_type = _norm_audit_type(audit_type)
    site_type = _norm_site_type(site_type)
    sites = int(sites)
    if sites < 1:
        raise InputError("场所数必须 >= 1：%s" % sites)

    verified = True
    if site_type == "similar":
        factor, ok = store.fraction("ratio.sampling.%s.factor" % audit_type)
        verified = verified and ok
        y = ceil_fraction(factor * sites)
        formula = "Y = %s × X，向上取整" % _frac_str(factor)
        basis = "相似场所按 5.4.3.3 抽样（X = 相似场所总体数量，Y 向上取整）。"
        refs = store.source_ref("ratio.sampling.%s.factor" % audit_type)
        refs += store.source_ref("ratio.sampling_rounding")
        conclusion = "抽样 %d / 场所 %d" % (y, sites)
    elif audit_type in ("initial", "recert"):
        node, ok = store.require("ratio.non_similar_no_sampling")
        verified = verified and ok
        y = sites
        factor = Fraction(1)
        formula = "非相似场所不抽样：逐一在各场所审核，Y = X"
        basis = "非相似场所不应抽样，初审和再认证审核应逐一在各场所进行审核（5.4.3.4）。"
        refs = store.source_ref("ratio.non_similar_no_sampling")
        conclusion = "不抽样，逐一审核 %d 个场所" % sites
    else:
        factor, ok = store.fraction("ratio.non_similar_supervision_min_sites")
        verified = verified and ok
        y = ceil_fraction(factor * sites)
        formula = "Y = ceil(%s × X)，且每次审核均应包括中心职能部门" % _frac_str(factor)
        basis = "非相似场所监督审核应抽取不少于 30% 的场所，且每次审核均应包括中心职能部门（5.4.3.4）。"
        refs = store.source_ref("ratio.non_similar_supervision_min_sites")
        conclusion = "抽样 %d / 场所 %d（不少于 30%%）" % (y, sites)

    result = {
        "sites_total": sites,
        "site_type": SITE_TYPE_LABELS[site_type],
        "audit_type": AUDIT_TYPE_LABELS[audit_type],
        "sampled_sites": y,
        "sampling_formula": formula,
        "conclusion": conclusion,
        "includes_central_function": site_type == "non-similar" and audit_type == "supervision",
    }
    recomputed = {
        "sampled_sites": y,
        "path": "ceil(系数 × 场所数)",
    }
    return CalcResult(
        inputs=copy.deepcopy({"sites": sites, "audit_type": audit_type, "site_type": site_type}),
        units={"sites": "个场所"},
        basis=basis,
        formula=formula,
        rounding="向上取整（ceil）",
        result=result,
        recomputed=recomputed,
        source_ref=sorted(set(refs)),
        verified=verified,
    )


def _ratio_result(kind: str, inputs: Dict[str, Any], units: Dict[str, str], basis: str,
                  formula: str, rounding: str, result: Dict[str, Any],
                  recomputed: Dict[str, Any], refs: List[str], verified: bool) -> CalcResult:
    return CalcResult(
        inputs=copy.deepcopy(inputs),
        units=units,
        basis=basis,
        formula=formula,
        rounding=rounding,
        result=result,
        recomputed=recomputed,
        source_ref=sorted(set(refs)),
        verified=verified,
    )


def ratio_onsite(total_days: float, onsite_days: float,
                 params: Optional[ParamStore] = None) -> CalcResult:
    """现场/非现场时间分配校验：现场审核时间 >= 总审核时间 80%。"""
    store = params or ParamStore.load()
    total = to_fraction(total_days)
    onsite = to_fraction(onsite_days)
    if total <= 0:
        raise InputError("总审核时间必须 > 0")
    if onsite < 0:
        raise InputError("现场审核时间不能为负")
    min_ratio, ok = store.fraction("ratio.onsite_min")
    required_onsite = total * min_ratio
    required_total = onsite / min_ratio
    ok_onsite = at_least(onsite, required_onsite)
    ok_total = at_least(total, required_total)
    compliant = ok_onsite and ok_total
    actual_ratio = onsite / total if total else Fraction(0)

    basis = (
        "总审核时间 = 在委托人现场的审核时间 + 现场以外策划、文件审核、编写审核报告的时间；"
        "现场审核时间以首次会议到末次会议为界（5.4.2.1、12.1.6）。"
        "阈值按精确分数比较，恰好 80% 视为满足（>= 判定）。"
    )
    formula = "现场审核时间 >= 总审核时间 × 80%；等价校验：总审核时间 >= 现场审核时间 ÷ 80%"
    result = {
        "total_man_days": _f(total),
        "onsite_man_days": _f(onsite),
        "offsite_man_days": _f(total - onsite),
        "onsite_ratio": float(actual_ratio),
        "onsite_ratio_exact": _frac_str(actual_ratio),
        "min_onsite_ratio": float(min_ratio),
        "required_onsite_man_days": _f(required_onsite),
        "required_total_man_days_from_onsite": _f(required_total),
        "onsite_check": "合规" if ok_onsite else "不合规",
        "total_check": "合规" if ok_total else "不合规",
        "conclusion": "合规" if compliant else "不合规",
        "critical": "恰好 80% 视为满足" if compliant else "低于 80%",
    }
    recomputed = {
        "onsite_ratio": float(actual_ratio),
        "required_onsite_man_days": _f(total * min_ratio),
        "path": "现场 ÷ 总（精确分数比较）",
    }
    refs = store.source_ref("ratio.onsite_min")
    return _ratio_result(
        "onsite-ratio", {"total": _f(total), "onsite": _f(onsite)},
        {"total": "人日", "onsite": "人日", "ratio": "比例"},
        basis, formula, "不取整，按精确分数比较", result, recomputed, refs, ok,
    )


def ratio_audit_time(kind: str, initial_days: float, other_days: float,
                     params: Optional[ParamStore] = None) -> CalcResult:
    """监督 >= 初次 1/3、再认证 >= 初次 2/3 的校验。"""
    store = params or ParamStore.load()
    if kind not in ("supervision", "recert"):
        raise InputError("未知比例类型：%s（可选 supervision / recert）" % kind)
    initial = to_fraction(initial_days)
    other = to_fraction(other_days)
    if initial <= 0:
        raise InputError("初次认证审核时间必须 > 0")
    path = "ratio.surveillance_min" if kind == "supervision" else "ratio.recert_min"
    min_ratio, ok = store.ratio_fraction(path)
    required = initial * min_ratio
    compliant = at_least(other, required)
    actual = other / initial
    label = "监督审核时间" if kind == "supervision" else "再认证审核时间"
    clause = "5.7.4" if kind == "supervision" else "5.8.4"

    basis = (
        "%s不得少于依据附录 B 所确定的初次认证审核时间的 %s（%s）；"
        "阈值按精确分数比较，恰好等于 %s 视为满足（>= 判定）。"
        % (label, _frac_str(min_ratio), clause, _frac_str(min_ratio))
    )
    formula = "%s >= 初次认证审核时间 × %s" % (label, _frac_str(min_ratio))
    result = {
        "initial_man_days": _f(initial),
        "other_man_days": _f(other),
        "other_label": label,
        "min_ratio": float(min_ratio),
        "min_ratio_exact": _frac_str(min_ratio),
        "required_man_days": _f(required),
        "actual_ratio": float(actual),
        "actual_ratio_exact": _frac_str(actual),
        "conclusion": "合规" if compliant else "不合规",
        "critical": "恰好 %s 视为满足" % _frac_str(min_ratio),
    }
    recomputed = {
        "required_man_days": _f(initial * min_ratio),
        "actual_ratio": float(actual),
        "path": "其他 ÷ 初次（精确分数比较）",
    }
    refs = store.source_ref(path)
    return _ratio_result(
        kind, {"initial": _f(initial), kind: _f(other)},
        {"initial": "人日", kind: "人日", "ratio": "比例"},
        basis, formula, "不取整，按精确分数比较", result, recomputed, refs, ok,
    )


def ratio_combined(sum_days: float, combined_days: float,
                   params: Optional[ParamStore] = None) -> CalcResult:
    """结合审核：总审核时间 >= 各单独体系所需审核时间之和的 80%。"""
    store = params or ParamStore.load()
    total = to_fraction(sum_days)
    combined = to_fraction(combined_days)
    if total <= 0:
        raise InputError("单独体系审核时间之和必须 > 0")
    min_ratio, ok = store.fraction("ratio.combined_min_vs_sum")
    required = total * min_ratio
    compliant = at_least(combined, required)
    actual = combined / total
    basis = (
        "结合审核的总审核时间不得少于多个单独体系所需审核时间之和的 80%（5.4.2.4）；"
        "该 80% 基线是「各体系之和」，与 5.4.2.3 的「减时不超过 30%」不是同一个基线；"
        "阈值按精确分数比较，恰好 80% 视为满足。"
    )
    formula = "结合审核总时间 >= 各单独体系审核时间之和 × 80%"
    result = {
        "sum_man_days": _f(total),
        "combined_man_days": _f(combined),
        "min_ratio": float(min_ratio),
        "required_man_days": _f(required),
        "actual_ratio": float(actual),
        "actual_ratio_exact": _frac_str(actual),
        "conclusion": "合规" if compliant else "不合规",
        "critical": "恰好 80% 视为满足",
    }
    recomputed = {
        "required_man_days": _f(total * min_ratio),
        "actual_ratio": float(actual),
        "path": "结合 ÷ 之和（精确分数比较）",
    }
    refs = store.source_ref("ratio.combined_min_vs_sum")
    return _ratio_result(
        "combined-ratio", {"sum": _f(total), "combined": _f(combined)},
        {"sum": "人日", "combined": "人日", "ratio": "比例"},
        basis, formula, "不取整，按精确分数比较", result, recomputed, refs, ok,
    )


def ratio_supervision_sites(sites: int, sampled: int,
                             params: Optional[ParamStore] = None) -> CalcResult:
    """非相似场所监督审核：抽样场所数不少于 30%。"""
    store = params or ParamStore.load()
    sites = int(sites)
    sampled = int(sampled)
    if sites < 1:
        raise InputError("场所数必须 >= 1")
    if sampled < 0:
        raise InputError("抽样场所数不能为负")
    min_ratio, ok = store.fraction("ratio.non_similar_supervision_min_sites")
    required = ceil_fraction(min_ratio * sites)
    compliant = sampled >= required
    actual = Fraction(sampled, sites)
    basis = (
        "非相似场所监督审核应抽取不少于 30% 的场所，且每次审核均应包括中心职能部门（5.4.3.4）；"
        "恰好 30% 视为满足（>= 判定）。"
    )
    formula = "抽样场所数 >= ceil(30% × 场所数)"
    result = {
        "sites_total": sites,
        "sampled_sites": sampled,
        "min_sampled_sites": required,
        "actual_ratio": float(actual),
        "actual_ratio_exact": _frac_str(actual),
        "conclusion": "合规" if compliant else "不合规",
        "critical": "恰好 30% 视为满足",
    }
    recomputed = {
        "min_sampled_sites": required,
        "actual_ratio": float(actual),
        "path": "ceil(30% × 场所数)",
    }
    refs = store.source_ref("ratio.non_similar_supervision_min_sites")
    return _ratio_result(
        "supervision-sites", {"sites": sites, "sampled": sampled},
        {"sites": "个场所", "ratio": "比例"},
        basis, formula, "场所数向上取整", result, recomputed, refs, ok,
    )


# ---------------------------------------------------------------------------
# 3. 期限计算
# ---------------------------------------------------------------------------
def _deadline_common(store: ParamStore, inputs: Dict[str, Any], basis: str, formula: str,
                      result: Dict[str, Any], recomputed: Dict[str, Any],
                      refs: List[str], verified: bool, units: Optional[Dict[str, str]] = None) -> CalcResult:
    return CalcResult(
        inputs=copy.deepcopy(inputs),
        units=units or {"date": "日期", "months": "日历月"},
        basis=basis,
        formula=formula,
        rounding="按日历月处理，不按 30 天折算；月份加减时目标月无对应日则收敛到该月最后一天",
        result=result,
        recomputed=recomputed,
        source_ref=sorted(set(refs)),
        verified=verified,
    )


def deadline_nc_verification(audit_type: str, start: str, cert_expiry: Optional[str] = None,
                             profile: str = "qms",
                             params: Optional[ParamStore] = None) -> CalcResult:
    """严重不符合验证时限：初次 6 个月 / 监督 3 个月 / 再认证 原证到期前。"""
    store = params or ParamStore.load()
    audit_type = _norm_audit_type(audit_type)
    profile = _norm_profile(profile)
    path = "deadline.nc_verification.%s" % audit_type
    node, ok = store.require(path)
    event = node.get("event", "")
    refs = store.source_ref(path)
    clause_map = store.get("meta.rule_clause_map.%s" % profile) or {}
    clause = clause_map.get("nc_verification", "")
    if clause:
        refs = ["%s %s" % (PROFILE_LABELS[profile], clause)] + list(refs)

    start_date = parse_date(start)
    if audit_type == "recert":
        if not cert_expiry:
            raise InputError("再认证的严重不符合验证以原认证证书到期为限，必须提供 --cert-expiry")
        expiry = parse_date(cert_expiry)
        deadline = expiry
        formula = "验证期限截止日 = 原认证证书到期日"
    else:
        months = int(node["months"])
        deadline = add_months(start_date, months)
        formula = "验证期限截止日 = %s + %d 个日历月" % (event, months)

    inclusive, inc_ok = store.value("conventions.deadline_day_included")
    start_counted, sc_ok = store.value("conventions.start_day_counted")
    verified = ok and inc_ok and sc_ok

    basis = (
        "起算事件：%s。期间按日历月计算（不按 30 天折算）；起算当日%s计入，届满当日%s届满，"
        "即在截止日当日 24:00 前完成视为在期限内。未在时限内完成不符合所采取措施的，"
        "不应作出授予、保持或更新认证的决定（%s）。"
        % (
            event,
            "不" if not start_counted else "",
            "算" if inclusive else "不算",
            clause_map.get("no_decision", "5.10.5"),
        )
    )
    result = {
        "profile": PROFILE_LABELS[profile],
        "audit_type": AUDIT_TYPE_LABELS[audit_type],
        "start_event": event,
        "start_date": start_date.isoformat(),
        "deadline": deadline.isoformat(),
        "deadline_day_included": bool(inclusive),
        "start_day_counted": bool(start_counted),
        "months": node.get("months"),
        "conclusion": "截止日为 %s（含当日）" % deadline.isoformat(),
    }
    recomputed = {
        "deadline": deadline.isoformat(),
        "path": "起算日 + N 个日历月（月末收敛）",
    }
    return _deadline_common(
        store,
        {"audit_type": audit_type, "start": start_date.isoformat(),
         "cert_expiry": parse_date(cert_expiry).isoformat() if cert_expiry else None,
         "profile": profile},
        basis, formula, result, recomputed, refs, verified,
    )


def deadline_certificate_expiry(issue: str, years: Optional[int] = None,
                                params: Optional[ParamStore] = None) -> CalcResult:
    """认证证书有效期：自签发日起算，最长 3 年。"""
    store = params or ParamStore.load()
    issue_date = parse_date(issue)
    max_years, ok = store.value("deadline.certificate_validity_max_years")
    valid_years = int(years) if years is not None else int(max_years)
    if valid_years > int(max_years):
        raise InputError("证书有效期最长 %d 年，给定 %d 年超出规则上限" % (max_years, valid_years))
    expiry = add_years(issue_date, valid_years)
    start_node, start_ok = store.require("deadline.certificate_validity_start")
    verified = ok and start_ok

    basis = (
        "认证证书有效期最长为 3 年（6.2.1）；有效期起算日期为证书签发日期，"
        "签发日期不应早于做出认证决定的日期（6.2.2）。"
    )
    formula = "证书终止日 = 签发日 + %d 年（2 月 29 日加年收敛到 2 月 28 日）" % valid_years
    result = {
        "issue_date": issue_date.isoformat(),
        "validity_years": valid_years,
        "expiry_date": expiry.isoformat(),
        "max_validity_years": int(max_years),
        "conclusion": "证书有效期 %d 年，终止日 %s" % (valid_years, expiry.isoformat()),
    }
    recomputed = {
        "expiry_date": expiry.isoformat(),
        "path": "签发日 + N 年（闰日收敛）",
    }
    refs = store.source_ref("deadline.certificate_validity_max_years") + store.source_ref(
        "deadline.certificate_validity_start"
    )
    return _deadline_common(
        store, {"issue": issue_date.isoformat(), "years": valid_years},
        basis, formula, result, recomputed, refs, verified,
    )


def deadline_certificate_cycle(previous_expiry: str, issue: str, years: Optional[int] = None,
                               params: Optional[ParamStore] = None) -> CalcResult:
    """再认证周期：新证终止日不晚于上一周期终止日 + 3 年。"""
    store = params or ParamStore.load()
    previous = parse_date(previous_expiry)
    issue_date = parse_date(issue)
    max_years, ok = store.value("deadline.certificate_validity_max_years")
    valid_years = int(years) if years is not None else int(max_years)
    if valid_years > int(max_years):
        raise InputError("证书有效期最长 %d 年，给定 %d 年超出规则上限" % (max_years, valid_years))
    new_expiry = add_years(issue_date, valid_years)
    limit = add_years(previous, int(max_years))
    compliant = new_expiry <= limit
    lapse_ok, lapse_verified = store.require("deadline.certificate_auto_lapse")

    basis = (
        "证书有效期最长 3 年（6.2.1）；新证终止日不得晚于上一周期终止日 + 3 年；"
        "未能在原认证证书到期前完成再认证决定的，证书到期后自动失效，且不应延长原证书有效期（6.2.3）。"
    )
    formula = "新证终止日 = 签发日 + %d 年；上限 = 上一周期终止日 + %d 年" % (valid_years, max_years)
    result = {
        "previous_cycle_expiry": previous.isoformat(),
        "issue_date": issue_date.isoformat(),
        "new_expiry_date": new_expiry.isoformat(),
        "max_new_expiry_date": limit.isoformat(),
        "conclusion": "合规" if compliant else "不合规",
        "detail": "新证终止日 %s%s上一周期终止日 + 3 年（%s）" % (
            new_expiry.isoformat(),
            "不晚于" if compliant else "晚于",
            limit.isoformat(),
        ),
    }
    recomputed = {
        "new_expiry_date": new_expiry.isoformat(),
        "max_new_expiry_date": limit.isoformat(),
        "path": "两条加年路径分别计算后比较",
    }
    refs = store.source_ref("deadline.certificate_validity_max_years")
    refs += store.source_ref("deadline.certificate_auto_lapse")
    return _deadline_common(
        store, {"previous_expiry": previous.isoformat(), "issue": issue_date.isoformat(),
                "years": valid_years},
        basis, formula, result, recomputed, refs, ok and lapse_verified,
    )


def deadline_initial_decision(start: str, params: Optional[ParamStore] = None) -> CalcResult:
    """初次认证决定：现场审核后 6 个月内完成。"""
    store = params or ParamStore.load()
    node, ok = store.require("deadline.initial_decision")
    months = int(node["months"])
    start_date = parse_date(start)
    deadline = add_months(start_date, months)
    inclusive, inc_ok = store.value("conventions.deadline_day_included")
    verified = ok and inc_ok
    basis = (
        "初次认证审核的认证决定应在现场审核后 6 个月内完成，否则应在推荐认证注册前再实施一次"
        "第二阶段审核（5.12.3）。起算事件：现场审核结束之日。起算当日不计入，届满当日算届满。"
    )
    formula = "决定截止日 = 现场审核结束日 + %d 个日历月" % months
    result = {
        "start_event": node.get("event", "现场审核结束之日"),
        "start_date": start_date.isoformat(),
        "deadline": deadline.isoformat(),
        "deadline_day_included": bool(inclusive),
        "conclusion": "认证决定须在 %s（含当日）前完成" % deadline.isoformat(),
    }
    recomputed = {"deadline": deadline.isoformat(), "path": "起算日 + N 个日历月（月末收敛）"}
    return _deadline_common(
        store, {"start": start_date.isoformat()}, basis, formula, result, recomputed,
        store.source_ref("deadline.initial_decision"), verified,
    )


def deadline_recert_decision(cert_expiry: str, params: Optional[ParamStore] = None) -> CalcResult:
    """再认证审核与决定：原证到期前完成（最迟到期之日起 6 个月内）。"""
    store = params or ParamStore.load()
    node, ok = store.require("deadline.recert_decision")
    expiry = parse_date(cert_expiry)
    max_months = int(node["max_months_after_expiry"])
    latest = add_months(expiry, max_months)
    audit_ok, audit_verified = store.require("deadline.recert_audit_completion")
    inclusive, inc_ok = store.value("conventions.deadline_day_included")
    verified = ok and audit_ok and inc_ok
    basis = (
        "再认证审核应在获证组织现场进行，并应在认证证书到期前完成（5.8.2）；"
        "再认证审核的认证决定宜在上一认证周期认证证书到期前完成，最迟应在证书到期之日起 6 个月内"
        "完成（5.12.4）；若在当前认证证书终止日期前未完成再认证审核或严重不符合验证，"
        "则不应予以再认证，也不应延长原证书有效期。"
    )
    formula = "应完成日 = 原证到期日（不晚于该日）；最迟日 = 原证到期日 + %d 个日历月" % max_months
    result = {
        "certificate_expiry": expiry.isoformat(),
        "should_complete_by": expiry.isoformat(),
        "latest_date": latest.isoformat(),
        "deadline_day_included": bool(inclusive),
        "conclusion": "再认证审核与决定应在 %s 前完成；最迟不超过 %s" % (
            expiry.isoformat(), latest.isoformat()),
    }
    recomputed = {
        "should_complete_by": expiry.isoformat(),
        "latest_date": latest.isoformat(),
        "path": "到期日 + N 个日历月",
    }
    refs = store.source_ref("deadline.recert_decision")
    refs += store.source_ref("deadline.recert_audit_completion")
    return _deadline_common(
        store, {"cert_expiry": expiry.isoformat()}, basis, formula, result, recomputed,
        refs, verified,
    )


def deadline_two_stage_gap(stage1_end: str, stage2_start: str,
                            params: Optional[ParamStore] = None) -> CalcResult:
    """两阶段间隔：最短 5 日、最长 6 个月。"""
    store = params or ParamStore.load()
    node, ok = store.require("deadline.two_stage_gap")
    min_days = int(node["min_days"])
    max_months = int(node["max_months"])
    first = parse_date(stage1_end)
    second = parse_date(stage2_start)
    gap_days = days_between(first, second)
    earliest = add_days(first, min_days)
    latest = add_months(first, max_months)
    ok_min = gap_days >= min_days
    ok_max = second <= latest
    compliant = ok_min and ok_max
    inclusive, inc_ok = store.value("conventions.deadline_day_included")
    start_counted, sc_ok = store.value("conventions.start_day_counted")
    verified = ok and inc_ok and sc_ok

    basis = (
        "两个阶段审核时间间隔最短不应少于 5 日，最长不应超过 6 个月；如需要更长的时间间隔，"
        "应重新实施第一阶段审核（5.6.1）。间隔天数 = 第二阶段开始日 − 第一阶段结束日"
        "（第一阶段结束当日不计入）；恰好 5 日、恰好 6 个月均视为满足（>= / <= 判定）。"
    )
    formula = "5 日 <= 第二阶段开始日 − 第一阶段结束日 <= 第一阶段结束日 + 6 个日历月"
    result = {
        "stage1_end": first.isoformat(),
        "stage2_start": second.isoformat(),
        "gap_days": gap_days,
        "min_gap_days": min_days,
        "max_gap_months": max_months,
        "earliest_stage2_start": earliest.isoformat(),
        "latest_stage2_start": latest.isoformat(),
        "min_check": "合规" if ok_min else "不合规",
        "max_check": "合规" if ok_max else "不合规",
        "conclusion": "合规" if compliant else "不合规",
        "critical": "恰好 5 日、恰好 6 个月视为满足",
    }
    recomputed = {
        "gap_days": gap_days,
        "earliest_stage2_start": earliest.isoformat(),
        "latest_stage2_start": latest.isoformat(),
        "path": "差值 + 加日/加月两条路径",
    }
    return _deadline_common(
        store, {"stage1_end": first.isoformat(), "stage2_start": second.isoformat()},
        basis, formula, result, recomputed, store.source_ref("deadline.two_stage_gap"), verified,
    )


# ---------------------------------------------------------------------------
# 参数表展示
# ---------------------------------------------------------------------------
def list_params(store: ParamStore, only_unverified: bool = False,
                section: Optional[str] = None) -> List[Dict[str, Any]]:
    """列出参数表条目（含 value / source_ref / verified / md_ref）。

    表格行（附录 B 的每一行）也会单独列出，并继承所属表的 source_ref；
    md 未列、仅由一手规则原文核验的行按参数表策略标记为已核验。
    """
    rows: List[Dict[str, Any]] = []
    skip_keys = {
        "source_ref", "verified", "md_ref", "blocking", "reason", "note",
        "class", "primary_source_verified", "mode", "direction", "floor",
        "rule", "tie", "event", "status", "step", "schema_version",
    }
    value_keys = (
        "value", "days", "factor", "months", "min_days", "max_months",
        "max_months_after_expiry", "numerator", "denominator", "floor", "event",
    )

    def pick_value(node: Dict[str, Any]) -> Any:
        for key in value_keys:
            if key in node:
                return node[key]
        return None

    def walk(node: Any, path: str, inherited_ref: Any,
             inherited_verified: Optional[bool]) -> None:
        if isinstance(node, dict):
            own_ref = node.get("source_ref")
            own_verified: Optional[bool] = None
            if own_ref is not None and path:
                own_verified = store.is_verified(path)
                inherited_ref = own_ref
                inherited_verified = own_verified
            has_children = any(isinstance(v, (dict, list)) for v in node.values())
            is_entry = (
                "value" in node
                or "md_ref" in node
                or (own_ref is not None and not has_children)
            )
            if is_entry and path:
                if own_ref is not None:
                    verified = bool(own_verified)
                    ref = own_ref
                elif "md_ref" in node:
                    verified = store.md_row_verified(node.get("md_ref"))
                    ref = inherited_ref
                else:
                    verified = bool(inherited_verified) if inherited_verified is not None else True
                    ref = inherited_ref
                rows.append(
                    {
                        "path": path,
                        "value": pick_value(node),
                        "source_ref": ref,
                        "md_ref": node.get("md_ref"),
                        "verified": verified,
                        "blocking": node.get("blocking", True),
                        "note": node.get("note") or node.get("reason"),
                    }
                )
                return
            for key, value in node.items():
                if key in skip_keys:
                    continue
                walk(value, "%s.%s" % (path, key) if path else str(key),
                     inherited_ref, inherited_verified)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, "%s[%d]" % (path, index), inherited_ref, inherited_verified)

    walk(store.data, "", None, None)

    def keep(row: Dict[str, Any]) -> bool:
        if section and not row["path"].startswith(section):
            return False
        if only_unverified and row["verified"]:
            return False
        return True

    return [row for row in rows if keep(row)]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _format_text(result: CalcResult) -> str:
    lines = []
    lines.append("inputs     : %s" % json.dumps(result.inputs, ensure_ascii=False, sort_keys=True))
    lines.append("units      : %s" % json.dumps(result.units, ensure_ascii=False, sort_keys=True))
    lines.append("basis      : %s" % result.basis)
    lines.append("formula    : %s" % result.formula)
    lines.append("rounding   : %s" % result.rounding)
    lines.append("result     : %s" % json.dumps(result.result, ensure_ascii=False, sort_keys=True, indent=2))
    lines.append("recomputed : %s" % json.dumps(result.recomputed, ensure_ascii=False, sort_keys=True, indent=2))
    lines.append("source_ref : %s" % json.dumps(result.source_ref, ensure_ascii=False))
    lines.append("verified   : %s" % result.verified)
    return "\n".join(lines)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--params", default=None, help="参数表路径（默认 tools/params.yaml）")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")
    parser.add_argument("--strict", action="store_true", help="判定不合规时以非 0 退出码结束")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="calculate.py",
        description="CNCA 三体系 人日 / 抽样比例 / 期限 计算器（数值全部来自 tools/params.yaml）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_mandays = sub.add_parser("man-days", help="人日计算")
    p_mandays.add_argument("--profile", required=True, help="体系：qms / ems / ohsms")
    p_mandays.add_argument("--headcount", required=True, type=int, help="有效人数")
    p_mandays.add_argument("--risk", default="medium", help="风险类型：low / medium / high（EMS 另有 limited / special）")
    p_mandays.add_argument("--sites", type=int, default=1, help="场所数")
    p_mandays.add_argument("--audit-type", default="initial", help="审核类型：initial / supervision / recert")
    p_mandays.add_argument("--site-type", default="similar", help="场所类型：similar / non-similar")
    _add_common(p_mandays)

    p_ratio = sub.add_parser("ratio", help="比例计算")
    p_ratio.add_argument("--check", required=True,
                          help="onsite-ratio / supervision-ratio / recert-ratio / combined-ratio / sampling / supervision-sites")
    p_ratio.add_argument("--total", type=float, help="总审核时间（人日）")
    p_ratio.add_argument("--onsite", type=float, help="现场审核时间（人日）")
    p_ratio.add_argument("--initial", type=float, help="初次认证审核时间（人日）")
    p_ratio.add_argument("--supervision", type=float, help="监督审核时间（人日）")
    p_ratio.add_argument("--recert", type=float, help="再认证审核时间（人日）")
    p_ratio.add_argument("--sum", type=float, help="各单独体系审核时间之和（人日）")
    p_ratio.add_argument("--combined", type=float, help="结合审核总时间（人日）")
    p_ratio.add_argument("--sites", type=int, help="场所数")
    p_ratio.add_argument("--sampled", type=int, help="抽样场所数")
    p_ratio.add_argument("--audit-type", default="initial", help="审核类型：initial / supervision / recert")
    p_ratio.add_argument("--site-type", default="similar", help="场所类型：similar / non-similar")
    _add_common(p_ratio)

    p_deadline = sub.add_parser("deadline", help="期限计算")
    p_deadline.add_argument("--kind", required=True,
                            help="nc-verification / certificate-expiry / certificate-cycle / initial-decision / recert-decision / two-stage-gap")
    p_deadline.add_argument("--profile", default="qms", help="体系（用于条款号）：qms / ems / ohsms")
    p_deadline.add_argument("--audit-type", default="initial", help="审核类型：initial / supervision / recert")
    p_deadline.add_argument("--start", help="起算日期（YYYY-MM-DD）")
    p_deadline.add_argument("--issue", help="证书签发日期（YYYY-MM-DD）")
    p_deadline.add_argument("--cert-expiry", help="原认证证书到期日（YYYY-MM-DD）")
    p_deadline.add_argument("--stage1-end", help="第一阶段结束日（YYYY-MM-DD）")
    p_deadline.add_argument("--stage2-start", help="第二阶段开始日（YYYY-MM-DD）")
    p_deadline.add_argument("--years", type=int, help="证书有效期年数（默认取规则上限 3 年）")
    _add_common(p_deadline)

    p_params = sub.add_parser("params", help="参数表查看")
    p_params.add_argument("--list", action="store_true", help="列出参数")
    p_params.add_argument("--unverified", action="store_true", help="只列未核验参数")
    p_params.add_argument("--section", help="只列某个前缀的路径")
    p_params.add_argument("--fail-on-unverified", action="store_true", help="存在未核验参数时以退出码 3 结束")
    p_params.add_argument("--params", default=None, help="参数表路径")
    p_params.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")

    return parser


def _cmd_man_days(args: argparse.Namespace, store: ParamStore) -> int:
    result = man_days(
        profile=args.profile,
        headcount=args.headcount,
        risk=args.risk,
        sites=args.sites,
        audit_type=args.audit_type,
        site_type=args.site_type,
        params=store,
    )
    _emit(result, args.format)
    return 0


def _cmd_ratio(args: argparse.Namespace, store: ParamStore) -> int:
    check = args.check
    if check == "onsite-ratio":
        if args.total is None or args.onsite is None:
            raise InputError("onsite-ratio 需要 --total 与 --onsite")
        result = ratio_onsite(args.total, args.onsite, params=store)
    elif check == "supervision-ratio":
        if args.initial is None or args.supervision is None:
            raise InputError("supervision-ratio 需要 --initial 与 --supervision")
        result = ratio_audit_time("supervision", args.initial, args.supervision, params=store)
    elif check == "recert-ratio":
        if args.initial is None or args.recert is None:
            raise InputError("recert-ratio 需要 --initial 与 --recert")
        result = ratio_audit_time("recert", args.initial, args.recert, params=store)
    elif check == "combined-ratio":
        if args.sum is None or args.combined is None:
            raise InputError("combined-ratio 需要 --sum 与 --combined")
        result = ratio_combined(args.sum, args.combined, params=store)
    elif check == "sampling":
        if args.sites is None:
            raise InputError("sampling 需要 --sites")
        result = sampling_count(args.sites, args.audit_type, args.site_type, params=store)
    elif check == "supervision-sites":
        if args.sites is None or args.sampled is None:
            raise InputError("supervision-sites 需要 --sites 与 --sampled")
        result = ratio_supervision_sites(args.sites, args.sampled, params=store)
    else:
        raise InputError("未知校验项：%s" % check)
    _emit(result, args.format)
    if args.strict and result.result.get("conclusion") not in (None, "合规"):
        return 4
    return 0


def _cmd_deadline(args: argparse.Namespace, store: ParamStore) -> int:
    kind = args.kind
    if kind == "nc-verification":
        if not args.start:
            raise InputError("nc-verification 需要 --start")
        result = deadline_nc_verification(args.audit_type, args.start, args.cert_expiry,
                                          profile=args.profile, params=store)
    elif kind == "certificate-expiry":
        if not args.issue:
            raise InputError("certificate-expiry 需要 --issue")
        result = deadline_certificate_expiry(args.issue, args.years, params=store)
    elif kind == "certificate-cycle":
        if not args.issue or not args.cert_expiry:
            raise InputError("certificate-cycle 需要 --issue 与 --cert-expiry（上一周期终止日）")
        result = deadline_certificate_cycle(args.cert_expiry, args.issue, args.years, params=store)
    elif kind == "initial-decision":
        if not args.start:
            raise InputError("initial-decision 需要 --start（现场审核结束日）")
        result = deadline_initial_decision(args.start, params=store)
    elif kind == "recert-decision":
        if not args.cert_expiry:
            raise InputError("recert-decision 需要 --cert-expiry")
        result = deadline_recert_decision(args.cert_expiry, params=store)
    elif kind == "two-stage-gap":
        if not args.stage1_end or not args.stage2_start:
            raise InputError("two-stage-gap 需要 --stage1-end 与 --stage2-start")
        result = deadline_two_stage_gap(args.stage1_end, args.stage2_start, params=store)
    else:
        raise InputError("未知期限类型：%s" % kind)
    _emit(result, args.format)
    if args.strict and result.result.get("conclusion") not in (None, "合规"):
        return 4
    return 0


def _cmd_params(args: argparse.Namespace, store: ParamStore) -> int:
    rows = list_params(store, only_unverified=args.unverified, section=args.section)
    unverified = [row for row in list_params(store, only_unverified=True)]
    if args.format == "json":
        payload = {
            "params_file": str(store.path),
            "total": len(list_params(store)),
            "unverified_count": len(unverified),
            "params": rows,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("参数表：%s" % store.path)
        print("条目总数：%d，未核验：%d" % (len(list_params(store)), len(unverified)))
        for row in rows:
            mark = "OK " if row["verified"] else "待核"
            print("[%s] %s = %s" % (mark, row["path"], json.dumps(row["value"], ensure_ascii=False)))
            if row["source_ref"]:
                print("      来源: %s" % "; ".join(row["source_ref"]))
            if row["md_ref"]:
                print("      md 出处: %s" % row["md_ref"])
            if row["note"]:
                print("      说明: %s" % row["note"])
    if args.fail_on_unverified and unverified:
        return 3
    return 0


def _emit(result: CalcResult, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=False))
    else:
        print(_format_text(result))


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        store = ParamStore.load(getattr(args, "params", None))
        if args.command == "man-days":
            return _cmd_man_days(args, store)
        if args.command == "ratio":
            return _cmd_ratio(args, store)
        if args.command == "deadline":
            return _cmd_deadline(args, store)
        if args.command == "params":
            return _cmd_params(args, store)
        raise InputError("未知命令：%s" % args.command)
    except ParamError as exc:
        sys.stderr.write("参数待核/参数错误：%s\n" % exc)
        return 2 if "参数待核" in str(exc) else 3
    except InputError as exc:
        sys.stderr.write("输入错误：%s\n" % exc)
        return 1
    except CalcError as exc:
        sys.stderr.write("计算自检失败：%s\n" % exc)
        return 3


if __name__ == "__main__":
    sys.exit(main())
