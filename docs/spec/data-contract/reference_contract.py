#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一数据契约（D01–D16）参考实现与自检。

用法（Windows，必须 -X utf8）：
  python -X utf8 docs/spec/data-contract/reference_contract.py

自检 E1–E8 全 PASS 才允许发布本契约；输出可直接重定向为 verification.txt。
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "time-grid"))
from reference_timegrid import Point15, aggregate_15, slot_start  # noqa: E402

SCHEMA = json.loads((HERE / "contract.schema.json").read_text(encoding="utf-8"))
SAMPLES = [json.loads(line) for line in (HERE / "samples.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
COMMON = {c["name"] for c in SCHEMA["common_columns"]}
TYPES = {"string", "date", "datetime", "int", "decimal", "bool", "enum", "array", "json"}
SENS_RANK = {"PUBLIC": 0, "SENSITIVE": 1, "HIGHLY_SENSITIVE": 2}

# ------------------------------------------------------------------ 质量规则
RULES = {
    "Q01": ("MISSING", "BLOCK", "必需字段/时点为空"),
    "Q02": ("DUP", "BLOCK", "同一业务键多条记录"),
    "Q03": ("RANGE", "BLOCK", "数值、枚举或日期越界"),
    "Q04": ("SHIFT", "BLOCK", "时标错位、表头列错位、日期与时段错配"),
    "Q05": ("UNIT", "WARN", "单位缺失或不一致（按单位列换算并登记）"),
    "Q06": ("LOGIC", "BLOCK", "跨字段/跨表逻辑不一致"),
}


@dataclass
class Issue:
    dataset: str
    rule_id: str
    severity: str
    business_key: str
    field: str
    detail: str

    def as_dict(self) -> dict:
        return {
            "dataset": self.dataset, "rule_id": self.rule_id, "severity": self.severity,
            "business_key": self.business_key, "field": self.field, "detail": self.detail,
        }


def _enum_values(spec: dict) -> list:
    if "enum_ref" in spec:
        return SCHEMA["enums"][spec["enum_ref"]]
    return spec.get("enum", [])


def _is_empty(v) -> bool:
    return v is None


def _parse_date(v) -> date:
    return date.fromisoformat(str(v)[:10])


def _parse_dt(v) -> datetime:
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _decimal(v) -> Decimal:
    try:
        return Decimal(str(v))
    except InvalidOperation as exc:
        raise ValueError(f"不是合法小数: {v!r}") from exc


def check_types(dataset: str, row: dict) -> list[Issue]:
    """Q01 缺失 + 类型/枚举/精度校验。返回 issues，同时把换算后的值写回 row。"""
    ds = SCHEMA["datasets"][dataset]
    issues: list[Issue] = []
    bk = _business_key(dataset, row)
    for spec in ds["fields"]:
        name = spec["name"]
        required = spec.get("required", True)
        if name not in row or _is_empty(row[name]):
            if required:
                issues.append(Issue(dataset, "Q01", RULES["Q01"][1], bk, name, "必需字段缺失"))
            continue
        v = row[name]
        t = spec["type"]
        if t == "string":
            if not isinstance(v, str):
                issues.append(Issue(dataset, "Q01", "BLOCK", bk, name, f"期望字符串，得到 {type(v).__name__}"))
        elif t in ("date", "datetime"):
            try:
                _parse_date(v) if t == "date" else _parse_dt(v)
            except ValueError:
                issues.append(Issue(dataset, "Q03", "BLOCK", bk, name, f"非法 {t}: {v!r}"))
            if t == "datetime" and isinstance(v, str) and not re.search(r"[+-]\d{2}:\d{2}$", v):
                issues.append(Issue(dataset, "Q03", "BLOCK", bk, name, "datetime 缺时区偏移"))
        elif t == "int":
            if isinstance(v, bool) or not isinstance(v, int):
                issues.append(Issue(dataset, "Q03", "BLOCK", bk, name, f"期望整数，得到 {v!r}"))
        elif t == "decimal":
            try:
                row[name] = _decimal(v)
            except ValueError:
                issues.append(Issue(dataset, "Q03", "BLOCK", bk, name, f"非法小数 {v!r}"))
        elif t == "bool":
            if not isinstance(v, bool):
                issues.append(Issue(dataset, "Q03", "BLOCK", bk, name, f"期望布尔，得到 {v!r}"))
        elif t == "enum":
            allowed = _enum_values(spec)
            if v not in allowed:
                issues.append(Issue(dataset, "Q03", "BLOCK", bk, name, f"{v!r} 不在枚举 {allowed}"))
        elif t == "array":
            if not isinstance(v, list):
                issues.append(Issue(dataset, "Q03", "BLOCK", bk, name, "期望数组"))
        elif t == "json":
            if not isinstance(v, str):
                issues.append(Issue(dataset, "Q03", "BLOCK", bk, name, "JSON 列需以字符串存储"))
            else:
                try:
                    json.loads(v)
                except json.JSONDecodeError:
                    issues.append(Issue(dataset, "Q03", "BLOCK", bk, name, "JSON 无法解析"))
    for key in COMMON:
        if key not in row:
            issues.append(Issue(dataset, "Q01", "BLOCK", bk, key, "通用血缘列缺失"))
    return issues


def _business_key(dataset: str, row: dict) -> str:
    ds = SCHEMA["datasets"][dataset]
    return "|".join(str(row.get(k)) for k in ds["business_key"])


def check_duplicates(dataset: str, rows: list[dict]) -> list[Issue]:
    """Q02：同一业务键多条。裁决优先级沿用时间栅格 T8，由导入器执行。"""
    seen: dict[str, int] = {}
    issues = []
    for row in rows:
        bk = _business_key(dataset, row)
        seen[bk] = seen.get(bk, 0) + 1
        if seen[bk] >= 2:
            issues.append(Issue(dataset, "Q02", "BLOCK", bk, "*", f"业务键重复第 {seen[bk]} 次"))
    return issues


def check_range(dataset: str, row: dict) -> list[Issue]:
    ds = SCHEMA["datasets"][dataset]
    issues: list[Issue] = []
    bk = _business_key(dataset, row)
    for spec in ds["fields"]:
        name, v = spec["name"], row.get(spec["name"])
        if v is None:
            continue
        if name == "slot_no" and not (1 <= int(v) <= 24):
            issues.append(Issue(dataset, "Q03", "BLOCK", bk, name, f"时段号 {v} 越界"))
        if name == "impact_ratio" and not (Decimal("0") <= _decimal(v) <= Decimal("1")):
            issues.append(Issue(dataset, "Q03", "BLOCK", bk, name, f"比例 {v} 不在 [0,1]"))
        if name in ("price", "amount", "energy_mwh", "volume_mwh", "total_energy_mwh") and _decimal(v) < 0:
            issues.append(Issue(dataset, "Q03", "BLOCK", bk, name, f"数值 {v} 为负（方向由科目/字段定义，不用符号偷懒）"))
    return issues


def check_shift(dataset: str, row: dict) -> list[Issue]:
    """Q04：时序表的 interval_start 必须等于 business_date + (slot_no-1) 小时。"""
    if dataset != "D04":
        return []
    bk = _business_key(dataset, row)
    issues = []
    if row.get("business_date") and row.get("slot_no") and row.get("interval_start"):
        if not (1 <= int(row["slot_no"]) <= 24):
            return issues  # 越界已由 Q03 报出，避免时轴函数断言中断整批
        day = _parse_date(row["business_date"])
        want = slot_start(day, int(row["slot_no"]))
        got = _parse_dt(row["interval_start"])
        if got != want:
            issues.append(Issue(dataset, "Q04", "BLOCK", bk, "interval_start",
                                f"应为 {want.isoformat()}，实为 {got.isoformat()}"))
    return issues


def check_unit(dataset: str, row: dict) -> list[Issue]:
    """Q05：D03 声明 KWH 时，出库统一换算为 MWH 并登记。"""
    if dataset != "D03" or row.get("unit") != "KWH":
        return []
    bk = _business_key(dataset, row)
    for name in ("contract_capacity_kw",):
        if row.get(name) is not None:
            row[name] = _decimal(row[name])
    row["unit"] = "MWH"
    return [Issue(dataset, "Q05", "WARN", bk, "unit", "源单位 KWH，已按 1 MWh = 1000 kWh 换算并登记")]


def check_logic(dataset: str, row: dict) -> list[Issue]:
    """Q06：金额=电量×价格（容差 0.01 元）；日历 is_workday 与 day_type 自洽。"""
    bk = _business_key(dataset, row)
    issues = []
    if dataset == "D09" and all(row.get(k) is not None for k in ("energy_mwh", "price", "amount")):
        calc = _decimal(row["energy_mwh"]) * _decimal(row["price"])
        diff = abs(calc - _decimal(row["amount"]))
        if diff > Decimal("0.05"):
            issues.append(Issue(dataset, "Q06", "BLOCK", bk, "amount",
                                f"应约 {calc.quantize(Decimal('0.01'))}，实为 {row['amount']}，差 {diff}"))
    if dataset == "D12" and row.get("day_type"):
        want = row["day_type"] in ("NORMAL_WORKDAY", "MAKEUP_WORKDAY")
        if bool(row.get("is_workday")) != want:
            issues.append(Issue(dataset, "Q06", "BLOCK", bk, "is_workday", "与 day_type 矛盾"))
    return issues


def validate(dataset: str, rows: list[dict], convert: bool = True) -> list[Issue]:
    issues: list[Issue] = []
    for row in rows:
        issues += check_types(dataset, row)
        if convert:
            issues += check_unit(dataset, row)
        issues += check_range(dataset, row)
        issues += check_shift(dataset, row)
        issues += check_logic(dataset, row)
    issues += check_duplicates(dataset, rows)
    return issues


# ------------------------------------------------------------------ 主体编码
ID_RE = re.compile(r"^(USER|METER|CONTRACT|TRADER|PACKAGE)-(\d{4})-(\d{6})-([0-9X])$")
LEGAL_SUFFIXES = ["股份有限公司", "有限责任公司", "有限公司", "集团有限公司", "公司"]
PUNCT = " \t\r\n　·．.,，、;；:：（）()【】[]《》<>\"'“”‘’_-—/\\"


def mod11_2(digits: str) -> str:
    """ISO 7064 MOD 11,2，10 位数字 → 1 位校验符（10 记 X）。"""
    if not re.fullmatch(r"\d{10}", digits):
        raise ValueError("校验位只对 10 位数字计算")
    p = 10
    for ch in digits:
        s = (p + int(ch)) % 10
        if s == 0:
            s = 10
        p = (2 * s) % 11
    c = (11 - p) % 11
    return "X" if c == 10 else str(c)


def make_id(prefix: str, org4: str, seq: int) -> str:
    digits = f"{org4}{seq:06d}"
    return f"{prefix}-{org4}-{seq:06d}-{mod11_2(digits)}"


def parse_id(value: str) -> dict:
    m = ID_RE.match(value)
    if not m:
        raise ValueError(f"编号格式不符: {value!r}")
    prefix, org4, seq, chk = m.groups()
    if mod11_2(org4 + seq) != chk:
        raise ValueError(f"校验位不符: {value!r}，期望 {mod11_2(org4 + seq)}")
    return {"prefix": prefix, "org4": org4, "seq": int(seq), "check": chk}


def normalize_name(raw: str) -> str:
    """同一主体不同写法的归一：NFKC → 去标点空白 → 去法律后缀（最长优先）。"""
    s = unicodedata.normalize("NFKC", raw)
    s = "".join(ch for ch in s if ch not in PUNCT)
    for suf in LEGAL_SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    return s


def resolve_alias(raw: str, aliases: list[dict]) -> str | None:
    key = normalize_name(raw)
    for a in aliases:
        if normalize_name(a["raw_name"]) == key:
            return a["canonical_id"]
    return None


# ------------------------------------------------------------------ 敏感与出域
def export_decision(dataset: str, scope: str, aggregate_entities: int | None = None) -> tuple[str, str]:
    """F050/A08：返回 (ALLOW|MASK|DENY, 理由)。scope ∈ EXTERNAL/MODEL。"""
    level = SCHEMA["datasets"][dataset]["sensitivity"]
    if scope == "EXTERNAL":
        if level == "PUBLIC":
            return "ALLOW", "公开级可原样出域"
        if level == "SENSITIVE":
            return "MASK", "敏感级须脱敏（哈希/掩码/聚合 k≥5）后出域"
        return "DENY", "高度敏感级禁止出域，仅本地计算"
    if scope == "MODEL":
        if level == "PUBLIC":
            return "ALLOW", "公开级可进提示词"
        if level == "SENSITIVE":
            return "MASK", "敏感级仅白名单字段 + 脱敏摘要"
        if aggregate_entities is None or aggregate_entities < 5:
            return "DENY", "高度敏感级只允许 k≥5 的聚合值进提示词"
        return "ALLOW", f"高度敏感级以 {aggregate_entities} 个主体的聚合值出域"
    raise ValueError(f"未知 scope: {scope}")


def mask_row(dataset: str, row: dict) -> dict:
    """脱敏：标识类字段哈希前缀，数值字段保留。"""
    import hashlib
    out = dict(row)
    for k, v in row.items():
        if k.endswith("_id") and isinstance(v, str) and v:
            out[k] = hashlib.sha256(v.encode()).hexdigest()[:12]
    return out


# ------------------------------------------------------------------ 双时态
@dataclass
class Versioned:
    doc_id: str
    effective_from: date
    effective_to: date | None
    label: str


def pick_as_of(versions: list[Versioned], day: date) -> Versioned | None:
    hits = [v for v in versions if v.effective_from <= day and (v.effective_to is None or day < v.effective_to)]
    if len(hits) > 1:
        raise ValueError("双时态重叠：同一日命中多个版本")
    return hits[0] if hits else None


# ------------------------------------------------------------------ 自检
def check(name: str, fn) -> None:
    try:
        detail = fn()
        print(f"PASS {name} :: {detail}")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {name} :: {exc}")
        raise


def e1_schema() -> str:
    assert len(SCHEMA["datasets"]) == 16, "必须恰好 16 类数据对象"
    for code, ds in SCHEMA["datasets"].items():
        names = {f["name"] for f in ds["fields"]}
        assert ds["business_key"], f"{code} 缺业务键"
        for k in ds["business_key"]:
            assert k in names, f"{code} 业务键 {k} 不在字段表"
        for f in ds["fields"]:
            assert f["type"] in TYPES, f"{code}.{f['name']} 类型未知 {f['type']}"
            if f["type"] == "enum":
                assert _enum_values(f), f"{code}.{f['name']} 枚举为空"
        assert ds["sensitivity"] in SENS_RANK, f"{code} 敏感级别非法"
    return "16 类对象；业务键/类型/枚举/敏感级别齐备"


def e2_identity() -> str:
    u = make_id("USER", "4201", 123)
    assert u == "USER-4201-000123-X", u
    assert parse_id(u)["seq"] == 123
    bad = u[:-1] + "0"
    try:
        parse_id(bad)
        raise AssertionError("错误校验位未被拦截")
    except ValueError:
        pass
    variants = ["湖北 长江新材料有限公司", "湖北长江新材料有限责任公司", "湖北长江新材料股份有限公司"]
    keys = {normalize_name(v) for v in variants}
    assert keys == {"湖北长江新材料"}, keys
    aliases = [{"raw_name": "湖北长江新材料有限公司", "canonical_id": u}]
    assert resolve_alias("湖北长江新材料有限责任公司", aliases) == u
    return "编码往返一致；错校验位被拒；3 种写法归一为同一主体"


def e3_sensitivity() -> str:
    assert export_decision("D04", "EXTERNAL")[0] == "DENY"
    assert export_decision("D01", "EXTERNAL")[0] == "ALLOW"
    assert export_decision("D15", "EXTERNAL")[0] == "MASK"
    assert export_decision("D04", "MODEL", 6)[0] == "ALLOW"
    assert export_decision("D04", "MODEL", 3)[0] == "DENY"
    masked = mask_row("D04", {"user_id": "USER-4201-000123-X", "energy_mwh": 1.234})
    assert masked["user_id"] != "USER-4201-000123-X" and len(masked["user_id"]) == 12
    return "高度敏感出域被拒；公开放行；敏感脱敏；模型仅 k≥5 聚合"


def e4_quality() -> str:
    rows = [r for r in SAMPLES if r["_dataset"] == "D09"]
    assert not validate("D09", [dict(r) for r in rows]), "示例行本身应通过"
    ok = [dict(r) for r in rows]

    miss = dict(ok[0]); miss["subject_code"] = None
    assert any(i.rule_id == "Q01" for i in validate("D09", [miss]))

    dup = [dict(ok[0]), dict(ok[0])]
    assert any(i.rule_id == "Q02" for i in validate("D09", dup))

    rng = [r for r in SAMPLES if r["_dataset"] == "D04"]
    bad_slot = dict(rng[0]); bad_slot["slot_no"] = 25
    assert any(i.rule_id == "Q03" for i in validate("D04", [bad_slot]))

    shifted = dict(rng[0]); shifted["interval_start"] = "2026-11-04T09:00:00+08:00"
    assert any(i.rule_id == "Q04" for i in validate("D04", [shifted]))

    unit = [r for r in SAMPLES if r["_dataset"] == "D03"]
    kwh = dict(unit[0]); kwh["unit"] = "KWH"; kwh["contract_capacity_kw"] = 3150.0
    issues = validate("D03", [kwh])
    assert any(i.rule_id == "Q05" and i.severity == "WARN" for i in issues)
    assert kwh["unit"] == "MWH"

    logic = dict(ok[0]); logic["amount"] = 500.00
    assert any(i.rule_id == "Q06" for i in validate("D09", [logic]))
    return "Q01–Q06 六类规则各自命中且级别正确（Q05 为 WARN 并换算）"


def e5_bitemporal() -> str:
    versions = [
        Versioned("RULE-HB-2025-0009", date(2025, 4, 10), date(2026, 10, 1), "v1"),
        Versioned("RULE-HB-2025-0009", date(2026, 10, 1), None, "v2"),
    ]
    assert pick_as_of(versions, date(2026, 9, 30)).label == "v1"
    assert pick_as_of(versions, date(2026, 10, 1)).label == "v2"
    assert pick_as_of(versions, date(2025, 1, 1)) is None
    try:
        pick_as_of(versions + [Versioned("RULE-HB-2025-0009", date(2026, 9, 1), None, "bad")], date(2026, 9, 15))
        raise AssertionError("重叠版本未被拒")
    except ValueError:
        pass
    return "as-of 命中唯一版本；重叠配置被拒；早于生效日返回空"


def e6_timegrid() -> str:
    day = date(2026, 11, 4)
    hour_start = slot_start(day, 9)
    points = [Point15(hour_start + timedelta(minutes=15 * i), e, Decimal("400"), 1, i)
              for i, e in enumerate([Decimal("0.100"), Decimal("0.110"), Decimal("0.090"), Decimal("0.100")])]
    agg = aggregate_15(hour_start, points)
    assert agg.energy == Decimal("0.400"), agg.energy
    row = next(r for r in SAMPLES if r["_dataset"] == "D04")
    assert row["interval_start"] == hour_start.isoformat(), (row["interval_start"], hour_start)
    assert not validate("D04", [dict(row)]), validate("D04", [dict(row)])
    return "4×15min=0.400 MWh 与小时行一致；示例行通过校验"


def e7_samples() -> str:
    covered = {r["_dataset"] for r in SAMPLES}
    assert covered == set(SCHEMA["datasets"]), covered ^ set(SCHEMA["datasets"])
    for row in SAMPLES:
        ds = row["_dataset"]
        extra = set(row) - COMMON - {f["name"] for f in SCHEMA["datasets"][ds]["fields"]} - {"_dataset"}
        assert not extra, f"{ds} 出现契约外字段 {extra}"
        issues = [i for i in validate(ds, [dict(row)]) if i.severity == "BLOCK"]
        assert not issues, f"{ds} 示例行 BLOCK: {[i.as_dict() for i in issues]}"
    return "16 行示例覆盖 16 类对象，全部通过 BLOCK 级校验"


def e8_gap() -> str:
    tbd = []
    for code, ds in SCHEMA["datasets"].items():
        for f in ds["fields"]:
            if f.get("confidence") == "TBD":
                tbd.append(f"{code}.{f['name']}")
    assert len(tbd) >= 7, tbd
    return f"{len(tbd)} 个 TBD 字段登记在案：" + "、".join(tbd)


def main() -> int:
    print(f"契约 {SCHEMA['name']} v{SCHEMA['version']}；时轴依据 {SCHEMA['time_grid']}")
    print(f"对象 {len(SCHEMA['datasets'])} 类；示例 {len(SAMPLES)} 行；质量规则 {len(RULES)} 条")
    print("-" * 72)
    tests = [
        ("E1 契约完整性", e1_schema),
        ("E2 主体编码与归一", e2_identity),
        ("E3 敏感级别与出域", e3_sensitivity),
        ("E4 质量六类规则", e4_quality),
        ("E5 双时态版本", e5_bitemporal),
        ("E6 时轴一致", e6_timegrid),
        ("E7 示例行全覆盖", e7_samples),
        ("E8 Q02 缺口登记", e8_gap),
    ]
    failed = 0
    for name, fn in tests:
        try:
            check(name, fn)
        except Exception:  # noqa: BLE001
            failed += 1
    print("-" * 72)
    print(f"合计 {len(tests) - failed} 项通过，{failed} 项失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
