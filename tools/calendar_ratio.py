"""日历与比例基础计算。

本模块只做纯计算，不读参数表、不做业务判断：

* 日历：月份/年份加减（月末收敛）、间隔天数、整月差、ISO 日期解析。
  优先使用 dateutil.relativedelta；若环境缺少 dateutil 则使用本模块内置的
  等价实现，use_dateutil=False 可强制走回退路径（便于测试两条路径等价）。
* 比例：全部用 fractions.Fraction 做精确比较，避免浮点误差把
  「恰好 80%」「恰好 1/3」这类临界值判错。

日期一律按「日历月」处理，不按 30 天折算。
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from fractions import Fraction
from typing import Optional, Union

try:  # 可选依赖
    from dateutil.relativedelta import relativedelta  # type: ignore

    HAS_DATEUTIL = True
except Exception:  # pragma: no cover - 环境缺 dateutil 时走回退路径
    relativedelta = None  # type: ignore
    HAS_DATEUTIL = False

DateLike = Union[str, date, datetime]

__all__ = [
    "HAS_DATEUTIL",
    "add_months",
    "add_years",
    "add_days",
    "days_between",
    "whole_months_between",
    "parse_date",
    "to_fraction",
    "ratio",
    "at_least",
    "ceil_fraction",
    "round_to_step",
    "num",
]


# ---------------------------------------------------------------------------
# 日期解析与基础运算
# ---------------------------------------------------------------------------
def parse_date(value: DateLike) -> date:
    """把 ISO 字符串 / datetime / date 统一成 date。

    >>> parse_date("2026-06-01")
    datetime.date(2026, 6, 1)
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("日期为空")
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    raise ValueError("无法解析日期：%r（应为 YYYY-MM-DD）" % (value,))


def _clamp_day(year: int, month: int, day: int) -> int:
    """把日期收敛到目标月的合法日期（1 月 31 日 -> 2 月 28 日）。"""
    return min(day, calendar.monthrange(year, month)[1])


def add_months(start: date, months: int, *, use_dateutil: Optional[bool] = None) -> date:
    """日期加月份，按日历月处理；目标月没有对应日时收敛到该月最后一天。

    >>> add_months(date(2026, 1, 31), 1)
    datetime.date(2026, 2, 28)
    """
    if use_dateutil is None:
        use_dateutil = HAS_DATEUTIL
    if use_dateutil and HAS_DATEUTIL:
        return start + relativedelta(months=months)
    total = start.year * 12 + (start.month - 1) + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    return date(year, month, _clamp_day(year, month, start.day))


def add_years(start: date, years: int, *, use_dateutil: Optional[bool] = None) -> date:
    """日期加年份；2 月 29 日加到非闰年收敛到 2 月 28 日。

    >>> add_years(date(2024, 2, 29), 1)
    datetime.date(2025, 2, 28)
    """
    if use_dateutil is None:
        use_dateutil = HAS_DATEUTIL
    if use_dateutil and HAS_DATEUTIL:
        return start + relativedelta(years=years)
    year = start.year + years
    return date(year, start.month, _clamp_day(year, start.month, start.day))


def add_days(start: date, days: int) -> date:
    """日期加减天数。"""
    return start + timedelta(days=days)


def days_between(start: date, end: date) -> int:
    """end - start 的天数（可为负）。"""
    return (end - start).days


def whole_months_between(start: date, end: date) -> int:
    """start 到 end 之间走满的整月数（不足整月不计）。

    >>> whole_months_between(date(2026, 1, 31), date(2026, 2, 28))
    0
    >>> whole_months_between(date(2026, 1, 1), date(2026, 7, 1))
    6
    """
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return months


# ---------------------------------------------------------------------------
# 比例（精确有理数）
# ---------------------------------------------------------------------------
def to_fraction(value: Union[int, float, str, Fraction]) -> Fraction:
    """转成精确分数；float 先转 str 避免二进制误差（0.8 -> 4/5）。"""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        return Fraction(str(value))
    if isinstance(value, str):
        return Fraction(value)
    raise TypeError("无法转换为分数：%r" % (value,))


def ratio(numerator: int, denominator: int) -> Fraction:
    """构造分数比例（1, 3 -> 1/3）。"""
    if denominator == 0:
        raise ZeroDivisionError("分母不能为 0")
    return Fraction(numerator, denominator)


def at_least(actual: Union[int, float, Fraction], minimum: Union[int, float, Fraction]) -> bool:
    """actual >= minimum 的精确比较（>= 视为满足，即临界值合规）。"""
    return to_fraction(actual) >= to_fraction(minimum)


def ceil_fraction(value: Union[int, float, Fraction]) -> int:
    """向上取整（抽样数量 Y 的取整规则）。"""
    f = to_fraction(value)
    return -((-f.numerator) // f.denominator)


def round_to_step(value: Union[int, float, Fraction], step: Union[int, float, Fraction] = 0.5):
    """取最接近 step 的整数倍；正好居中时向上取（ROUND_HALF_UP）。

    >>> round_to_step(5.3)
    Fraction(11, 2)
    >>> round_to_step(5.2)
    Fraction(5, 1)
    >>> round_to_step(5.25)
    Fraction(11, 2)
    """
    f = to_fraction(value)
    s = to_fraction(step)
    if s == 0:
        raise ZeroDivisionError("步长不能为 0")
    q = f / s
    n, d = q.numerator, q.denominator
    floored = n // d
    if (n - floored * d) * 2 >= d:  # 恰好一半时向上
        floored += 1
    return floored * s


def num(value: Union[int, float, Fraction]):
    """分数转成可 JSON 序列化的数字（整数则返回 int）。"""
    if isinstance(value, Fraction):
        return int(value) if value.denominator == 1 else float(value)
    return value
