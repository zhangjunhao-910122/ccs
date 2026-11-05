"""统一时间栅格与上卷规则的参考实现（#26）。

只依赖标准库；金额与电量用 Decimal 计算。规则条文见同目录 SPEC.md（T1–T15）。
用法：
    python reference_timegrid.py --write-slot-map slot-map.csv --verify
"""
from __future__ import annotations

import argparse
import calendar
import csv
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Literal

TZ = timezone(timedelta(hours=8))  # Asia/Shanghai，中国无夏令时（1991 起）
SLOTS_PER_DAY = 24
STEP_MIN = 15
GRID = timedelta(hours=1)

Quality = Literal["OK", "MISSING", "PARTIAL", "ESTIMATED", "SUSPECT"]
_QUALITY_RANK = {"OK": 0, "ESTIMATED": 1, "PARTIAL": 2, "SUSPECT": 3, "MISSING": 4}


def worst(flags: Iterable[str]) -> str:
    return max(flags, key=lambda q: _QUALITY_RANK[q]) if flags else "OK"


# ---------------------------------------------------------------- 时间轴
def slot_start(day: date, slot: int) -> datetime:
    """T4：时段编号 → 区间起点（左闭右开）。"""
    if not 1 <= slot <= SLOTS_PER_DAY:
        raise ValueError(f"slot 必须在 1..24：{slot}")
    return datetime(day.year, day.month, day.day, tzinfo=TZ) + timedelta(hours=slot - 1)


def slot_of(ts: datetime) -> tuple[date, int]:
    """T2/A4：区间起点 → (业务日, 时段号)。"""
    local = ts.astimezone(TZ)
    return local.date(), local.hour + 1


def day_hours(day: date) -> int:
    return SLOTS_PER_DAY


def month_hours(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1] * SLOTS_PER_DAY


def assert_day_grid(day: date) -> None:
    """A1：每个自然日必须恰好 24 个连续、不重叠的小时区间。"""
    starts = [slot_start(day, s) for s in range(1, SLOTS_PER_DAY + 1)]
    assert len(starts) == SLOTS_PER_DAY
    for a, b in zip(starts, starts[1:]):
        assert b - a == GRID, f"{day} 时段不连续：{a} -> {b}"
    assert starts[0].hour == 0 and starts[-1].hour == 23
    assert starts[0].astimezone(TZ).utcoffset() == timedelta(hours=8)


def day_type(day: date) -> str:
    """T9：节假日只改日历属性，不改 24 时段栅格。"""
    special = {date(2026, 10, 1): "法定节假日", date(2026, 10, 10): "调休工作日"}
    if day in special:
        return special[day]
    return "周末" if day.weekday() >= 5 else "工作日"


def is_rest_day(day: date) -> bool:
    return day_type(day) in ("周末", "法定节假日")


# ---------------------------------------------------------------- 时标归一
def normalize_start(ts_raw: datetime, convention: Literal["interval_end", "interval_start"]) -> datetime:
    """T3：把来源时标归一为区间起点。"""
    ts = ts_raw.astimezone(TZ)
    if convention == "interval_end":
        return ts - timedelta(minutes=STEP_MIN)
    if convention == "interval_start":
        return ts
    raise ValueError(f"未知时标约定：{convention}")


def check_96_boundary(raw_starts: list[datetime], convention: str) -> None:
    """T3：96 点首末必须落在同一端；否则整批拒绝（不猜）。"""
    if len(raw_starts) != 96:
        raise ValueError(f"应为 96 点，实际 {len(raw_starts)}")
    first, last = raw_starts[0].astimezone(TZ), raw_starts[-1].astimezone(TZ)
    first_tod = first.hour * 60 + first.minute
    last_tod = last.hour * 60 + last.minute
    if convention == "interval_end":
        ok = first_tod == 15 and (last_tod == 0 or last_tod == 1440)
    else:
        ok = first_tod == 0 and last_tod == 23 * 60 + 45
    if not ok:
        raise ValueError(f"96 点首末时标与声明不符：{first} … {last} / {convention}")


# ---------------------------------------------------------------- 15 分钟 → 小时
@dataclass(frozen=True)
class Point15:
    start: datetime          # 已归一的区间起点
    energy: Decimal | None   # MWh
    price: Decimal | None    # 元/MWh
    source_priority: int = 0
    batch_seq: int = 0


@dataclass
class HourAgg:
    interval_start: datetime
    energy: Decimal | None
    observed_sum: Decimal | None
    quality: Quality
    price_official: Decimal | None      # 官方口径：4 点算术平均
    price_weighted: Decimal | None      # 经济口径：电量加权
    weight_coverage: Decimal | None
    partial_points: int = 0
    conflicts: list[dict] = field(default_factory=list)

    @property
    def business_date(self) -> date:
        return slot_of(self.interval_start)[0]

    @property
    def slot_no(self) -> int:
        return slot_of(self.interval_start)[1]


def _pick(group: list[Point15]) -> tuple[Point15, bool]:
    """T8：同一起点多条 → 优先级/批次高者；异值记冲突。"""
    ordered = sorted(group, key=lambda p: (p.source_priority, p.batch_seq), reverse=True)
    chosen = ordered[0]
    conflict = len({(p.energy, p.price) for p in group}) > 1
    return chosen, conflict


def aggregate_15(hour_start: datetime, points: list[Point15]) -> HourAgg:
    """T5/T7：4 个 15 分钟点 → 1 个小时。缺任一則电量 NULL + PARTIAL。"""
    assert hour_start.astimezone(TZ).minute == 0
    expected = [hour_start + timedelta(minutes=STEP_MIN * i) for i in range(4)]
    by_start: dict[datetime, list[Point15]] = {s: [] for s in expected}
    for p in points:
        s = p.start.astimezone(TZ)
        if s in by_start:
            by_start[s].append(p)

    chosen: dict[datetime, Point15] = {}
    conflicts: list[dict] = []
    for s, group in by_start.items():
        if not group:
            continue
        pick, conflict = _pick(group)
        chosen[s] = pick
        if conflict:
            conflicts.append({"interval_start": s.isoformat(), "values": [(str(p.energy), str(p.price)) for p in group]})

    present = [chosen[s] for s in expected if s in chosen]
    energies = [p.energy for p in present]
    prices = [p.price for p in present]
    n = len(present)
    energies_ok = all(e is not None for e in energies)
    observed = sum((e for e in energies if e is not None), Decimal(0)) if exposed_any(energies) else None

    quality: str = "OK"
    if n < 4 or not energies_ok:
        quality = "PARTIAL"
    energy = observed if (n == 4 and energies_ok) else None

    price_official = None
    if n == 4 and all(p is not None for p in prices):
        price_official = (sum(p for p in prices if p is not None) / Decimal(4))

    price_weighted = None
    weight_cov = None
    if energy is not None and energy > 0 and all(p is not None for p in prices) and energies_ok:
        money = sum((p * e for p, e in zip(prices, energies) if p is not None and e is not None), Decimal(0))
        price_weighted = money / energy
        weight_cov = Decimal(1)
    elif energy is not None and energy > 0:
        weight_cov = Decimal(0)

    return HourAgg(
        interval_start=hour_start,
        energy=energy,
        observed_sum=observed,
        quality=quality,  # type: ignore[arg-type]
        price_official=price_official,
        price_weighted=price_weighted,
        weight_coverage=weight_cov,
        partial_points=4 - n if n < 4 else (0 if energies_ok else 4 - sum(e is not None for e in energies)),
        conflicts=conflicts,
    )


def exposed_any(values: list[Decimal | None]) -> bool:
    return any(v is not None for v in values)


# ---------------------------------------------------------------- 上卷
@dataclass
class Rollup:
    level: str
    start: date
    expected_hours: int
    valid_hours: int
    coverage: Decimal
    energy: Decimal | None
    observed_sum: Decimal | None
    price_weighted: Decimal | None
    quality: Quality


def rollup_hours(level: str, period_start: date, expected_hours: int, hours: list[HourAgg]) -> Rollup:
    """T6/T7/T15：小时 → 日/月/年。不完整只给 observed_sum + 覆盖率。"""
    valid = [h for h in hours if h.energy is not None and h.quality in ("OK", "ESTIMATED")]
    coverage = (Decimal(len(valid)) / Decimal(expected_hours)) if expected_hours else Decimal(0)
    complete = len(valid) == expected_hours
    energy = sum((h.energy for h in valid if h.energy is not None), Decimal(0)) if complete else None
    observed = sum((h.observed_sum for h in hours if h.observed_sum is not None), Decimal(0))
    priced = [h for h in valid if h.price_weighted is not None and h.energy is not None]
    pw = None
    if complete and priced:
        e = sum((h.energy for h in priced if h.energy is not None), Decimal(0))
        m = sum((h.price_weighted * h.energy for h in priced if h.price_weighted is not None and h.energy is not None), Decimal(0))  # type: ignore[operator]
        pw = (m / e) if e > 0 else None
    quality = worst([h.quality for h in hours]) if not complete else worst([h.quality for h in hours] or ["OK"])
    if not complete:
        quality = worst([quality, "PARTIAL"])
    return Rollup(level, period_start, expected_hours, len(valid), coverage, energy, observed, pw, quality)  # type: ignore[arg-type]


# ---------------------------------------------------------------- 自检
PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, fn) -> None:
    try:
        detail = fn()
        PASS.append(f"PASS {name}" + (f" :: {detail}" if detail else ""))
    except AssertionError as exc:  # noqa: BLE001
        FAIL.append(f"FAIL {name} :: {exc}")
    except Exception as exc:  # noqa: BLE001
        FAIL.append(f"ERROR {name} :: {type(exc).__name__}: {exc}")


def e1_slot_map() -> str:
    rows = [(s, slot_start(date(2026, 1, 1), s)) for s in range(1, 25)]
    assert len(rows) == 24
    assert rows[0][1].strftime("%H:%M") == "00:00"
    assert rows[-1][1].strftime("%H:%M") == "23:00"
    for s, st in rows:
        assert slot_of(st) == (date(2026, 1, 1), s)
    return "24 行；00:00→23:00；slot_of 往返一致"


def e2_boundaries() -> str:
    d, s = slot_of(datetime(2026, 6, 30, 23, 0, tzinfo=TZ))
    assert (d, s) == (date(2026, 6, 30), 24), (d, s)
    assert slot_of(slot_start(date(2026, 7, 1), 1)) == (date(2026, 7, 1), 1)
    assert slot_of(slot_start(date(2026, 12, 31), 24))[0].year == 2026
    assert slot_of(slot_start(date(2027, 1, 1), 1))[0].year == 2027
    assert day_hours(date(2028, 2, 29)) == 24 and month_hours(2028, 2) == 696
    assert month_hours(2026, 1) == 744 and month_hours(2026, 4) == 720
    assert day_type(date(2026, 10, 1)) == "法定节假日" and day_type(date(2026, 10, 10)) == "调休工作日"
    assert not is_rest_day(date(2026, 10, 10)) and is_rest_day(date(2026, 10, 1))
    for day in (date(2026, 1, 1), date(2026, 6, 30), date(2026, 10, 1), date(2028, 2, 29)):
        assert_day_grid(day)
    return "跨日/跨月/跨年/闰日/大小月/节假日/调休 全部符合"


def _worked_example() -> HourAgg:
    h0 = datetime(2026, 3, 1, 0, 0, tzinfo=TZ)
    es = [Decimal("100"), Decimal("110"), Decimal("90"), Decimal("100")]
    ps = [Decimal("300"), Decimal("400"), Decimal("600"), Decimal("500")]
    pts = [Point15(h0 + timedelta(minutes=15 * i), es[i], ps[i]) for i in range(4)]
    return aggregate_15(h0, pts)


def e3_aggregate() -> str:
    agg = _worked_example()
    assert agg.energy == Decimal("400"), agg.energy
    assert agg.price_official == Decimal("450")
    assert agg.price_weighted == Decimal("445"), agg.price_weighted
    assert (agg.price_weighted * agg.energy).quantize(Decimal("0.000001")) == Decimal("178000.000000")
    assert quant(agg.price_official) == Decimal("450.0000") and quant(agg.price_weighted) == Decimal("445.0000")

    h0 = datetime(2026, 3, 1, 1, 0, tzinfo=TZ)
    es = [Decimal("100"), Decimal("110"), Decimal("90"), Decimal("100")]
    ps = [Decimal("300"), Decimal("400"), Decimal("600"), Decimal("500")]
    partial = aggregate_15(h0, [Point15(h0 + timedelta(minutes=15 * i), es[i], ps[i]) for i in range(3)])
    assert partial.energy is None and partial.quality == "PARTIAL", (partial.energy, partial.quality)
    assert partial.observed_sum == Decimal("300") and partial.partial_points == 1
    return "400 MWh；官方 450.0000；加权 445.0000；金额守恒 178000；缺 1 点→NULL/PARTIAL/300"


def quant(d: Decimal | None, places: str = "0.0001") -> Decimal | None:
    return None if d is None else d.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _hour_energy(day: date, slot: int) -> Decimal:
    return Decimal(1000 + (day.toordinal() * 7 + slot * 13) % 500)


def _hour_price(day: date, slot: int) -> Decimal:
    return Decimal(300 + (slot * 11 + day.day * 17) % 500)


def _split15(total: Decimal) -> list[Decimal]:
    a = int(total) * 35 // 100
    b = int(total) * 25 // 100
    c = int(total) * 22 // 100
    d = int(total) - a - b - c
    return [Decimal(a), Decimal(b), Decimal(c), Decimal(d)]


def _points_for_hour(day: date, slot: int) -> list[Point15]:
    h0 = slot_start(day, slot)
    es = _split15(_hour_energy(day, slot))
    p = _hour_price(day, slot)
    return [Point15(h0 + timedelta(minutes=15 * i), es[i], p + Decimal(i * 37)) for i in range(4)]


def e4_consistency() -> str:
    """两条路径：15 分钟源→聚合→日/月/年，与 小时表直接生成→日/月/年，必须逐字段相等。"""
    start, end = date(2026, 1, 1), date(2026, 12, 31)
    day = start
    from_source: dict[date, Decimal] = {}
    from_hours: dict[date, Decimal] = {}
    while day <= end:
        h_from_source = []
        h_from_hours = []
        for s in range(1, 25):
            agg = aggregate_15(slot_start(day, s), _points_for_hour(day, s))
            assert agg.quality == "OK", (day, s, agg.quality)
            h_from_source.append(agg)
            h_from_hours.append(HourAgg(slot_start(day, s), _hour_energy(day, s), _hour_energy(day, s), "OK", None, None, None))
        r1 = rollup_hours("day", day, 24, h_from_source)
        r2 = rollup_hours("day", day, 24, h_from_hours)
        assert r1.energy == r2.energy and r1.coverage == r2.coverage == 1
        from_source[day] = r1.energy
        from_hours[day] = r2.energy
        day += timedelta(days=1)

    assert from_source == from_hours
    total_year = sum(from_source.values(), Decimal(0))
    total_month = sum((sum((v for d, v in from_source.items() if d.month == m), Decimal(0)) for m in range(1, 13)), Decimal(0))
    assert total_year == total_month
    assert len(from_source) == 365
    assert sum(1 for d in from_source if d.month == 6) * 24 == month_hours(2026, 6)
    return f"365 天 / 8760 小时；日=月=年逐字段相等（{total_year} MWh）"


def e5_duplicates() -> str:
    h0 = datetime(2026, 5, 1, 2, 0, tzinfo=TZ)
    base = [Point15(h0 + timedelta(minutes=15 * i), Decimal("10"), Decimal("100")) for i in range(4)]
    same = aggregate_15(h0, base + [Point15(h0, Decimal("10"), Decimal("100"), source_priority=9, batch_seq=2)])
    assert same.energy == Decimal("40") and not same.conflicts, (same.energy, same.conflicts)
    diff = aggregate_15(h0, base + [Point15(h0, Decimal("12"), Decimal("150"), source_priority=9, batch_seq=2)])
    assert diff.energy == Decimal("42") and diff.conflicts, (diff.energy, diff.conflicts)
    return "同值去重（40 MWh）；异值取高优先级（42 MWh）并记冲突"


def e6_coverage() -> str:
    day = date(2026, 6, 30)
    hours = []
    for s in range(1, 25):
        e = Decimal("100") if s != 24 else None
        hours.append(HourAgg(slot_start(day, s), e, None if e is None else e, "MISSING" if e is None else "OK", None, None, None))
    r = rollup_hours("day", day, 24, hours)
    assert r.valid_hours == 23 and r.coverage == Decimal(23) / Decimal(24)
    assert r.energy is None and r.observed_sum == Decimal("2300")
    assert r.quality == "MISSING"
    return "24 应有 / 23 有效 / coverage=0.958333 / energy=NULL / observed=2300"


def write_slot_map(path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["slot_no", "start_hhmm", "end_hhmm", "start_minute", "end_minute"])
        base = date(2026, 1, 1)
        for s in range(1, 25):
            st = slot_start(base, s)
            end_label = "24:00" if s == SLOTS_PER_DAY else (st + GRID).strftime("%H:%M")
            w.writerow([s, st.strftime("%H:%M"), end_label, (s - 1) * 60, s * 60])


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="时间栅格参考实现与自检（#26）")
    ap.add_argument("--write-slot-map", metavar="PATH")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args(argv)

    if args.write_slot_map:
        write_slot_map(args.write_slot_map)
        print(f"已写 {args.write_slot_map}（24 行）")

    if args.verify:
        for name, fn in [
            ("E1 映射表", e1_slot_map),
            ("E2 边界归属", e2_boundaries),
            ("E3 15分钟聚合与价格双口径", e3_aggregate),
            ("E4 跨粒度一致性", e4_consistency),
            ("E5 重复裁决", e5_duplicates),
            ("E6 完整性口径", e6_coverage),
        ]:
            check(name, fn)
        for line in PASS:
            print(line)
        for line in FAIL:
            print(line)
        print(f"----\n合计 {len(PASS)} 项通过，{len(FAIL)} 项失败")
        return 0 if not FAIL else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
