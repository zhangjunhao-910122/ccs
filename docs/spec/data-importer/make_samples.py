#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 #24 数据导入器的样例文件（CSV / XLSX / PDF / JSON）。

用法（Windows，必须 -X utf8）：
  python -X utf8 docs/spec/data-importer/make_samples.py

生成的样例被 reference_importer.py 的自检 E1–E12 直接消费；
每个脏样例对应 importer.schema.json 里的一条 SRC-TEST-* 来源，只命中一条规则。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAMPLES = Path(os.environ.get("DSH_SAMPLES_DIR") or (HERE / "samples"))
SAMPLES.mkdir(parents=True, exist_ok=True)

# 分片生成：DSH_SELECT="a.csv,b.xlsx" 时只生成列出的文件，其余直接跳过。
# 用途：单进程连续写大量样例文件会被 DSH 文件写代理挂起，分片可规避。
_SELECT = {s.strip() for s in os.environ.get("DSH_SELECT", "").split(",") if s.strip()}


def _enabled(name: str) -> bool:
    return (not _SELECT) or (name in _SELECT)

TEMPLATE_HEADERS = ["用户编号", "业务日期", "时段号", "区间开始", "小时电量(MWh)", "15分钟点数", "质量标识"]


# ------------------------------------------------------------------ CSV

def write_csv(name: str, header: list[str], rows: list[list[str]]) -> Path:
    path = SAMPLES / name
    if not _enabled(name):
        return path
    lines = [",".join(header)]
    for row in rows:
        lines.append(",".join("" if c is None else str(c) for c in row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return path


# ------------------------------------------------------------------ XLSX

def write_xlsx(name: str, sheet_name: str, header: list[str], rows: list[list[str]],
               dictionary: list[list[str]] | None = None) -> Path:
    if not _enabled(name):
        return SAMPLES / name
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(header)
    for row in rows:
        ws.append(["" if c is None else c for c in row])
    if dictionary is not None:
        ws2 = wb.create_sheet("数据字典")
        ws2.append(["列名", "类型", "单位", "必填", "说明"])
        for row in dictionary:
            ws2.append(row)
    path = SAMPLES / name
    wb.save(path)
    return path


# ------------------------------------------------------------------ PDF

def _pdf_escape(text: str) -> bytes:
    out = text.encode("utf-16-be")
    return b"<" + out.hex().upper().encode("ascii") + b">"


def write_pdf(name: str, pairs: list[tuple[str, str]], with_text_layer: bool = True) -> Path:
    """写一个最小 PDF：每行一条 'key: value'，中文值用 UTF-16BE 十六进制串。"""
    content_lines = ["BT", "/F1 10 Tf", "50 760 Td", "13 TL"]
    for key, value in pairs:
        label = ("%s: " % key).encode("ascii", "replace").decode("ascii")
        content_lines.append("(%s) Tj" % label)
        if any(ord(ch) > 127 for ch in value):
            content_lines.append("%s Tj" % _pdf_escape(value).decode("ascii"))
        else:
            content_lines.append("(%s) Tj" % value.replace("(", "").replace(")", ""))
        content_lines.append("T*")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("ascii")

    objs = []
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")

    if not _enabled(name):
        return SAMPLES / name
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref_at)
    path = SAMPLES / name
    path.write_bytes(bytes(out))
    return path


# ------------------------------------------------------------------ 样例内容

def main() -> int:
    written: list[str] = []

    # D02 用户主数据（CSV）
    write_csv("d02_users.csv",
              ["用户编号", "行业编码", "区域编码", "电压等级", "状态", "合同容量(kW)", "套餐编号"],
              [["USER-4201-000123-X", "C", "420100", "10KV", "ACTIVE", "3150.0000", "PACKAGE-4201-000012-2"],
               ["USER-4201-000124-3", "C", "420100", "10KV", "ACTIVE", "1000.0000", "PACKAGE-4201-000012-2"],
               ["USER-4201-000125-7", "B", "420100", "35KV", "PAUSED", "6300.0000", ""]])
    written.append("d02_users.csv")

    # D02 用户主数据（XLSX，同一份数据的另一种载体）
    write_xlsx("d02_users.xlsx", "用户主数据",
               ["用户编号", "行业编码", "区域编码", "电压等级", "状态", "合同容量(kW)", "套餐编号"],
               [["USER-4201-000126-9", "C", "420100", "10KV", "ACTIVE", "3150.0000", "PACKAGE-4201-000012-2"],
                ["USER-4201-000127-5", "C", "420100", "10KV", "ACTIVE", "1000.0000", "PACKAGE-4201-000012-2"],
                ["USER-4201-000128-1", "B", "420100", "35KV", "PAUSED", "6300.0000", ""]])
    written.append("d02_users.xlsx")

    # D03 计量点
    write_csv("d03_meters.csv",
              ["计量点编号", "用户编号", "计量周期", "CT变比", "PT变比", "计量单位", "状态", "投运日期"],
              [["METER-4201-000456-4", "USER-4201-000123-X", "HOURLY", "40.0000", "100.0000", "MWH", "ACTIVE", "2019-06-01"],
               ["METER-4201-000457-8", "USER-4201-000124-3", "MONTHLY", "60.0000", "100.0000", "MWH", "ACTIVE", "2020-03-15"]])
    written.append("d03_meters.csv")

    # D04 负荷 15 分钟源（interval_end 口径；第二个用户第 1 小时故意少 1 个点 → I06）
    load_rows = []
    for i, q in enumerate(("00:15", "00:30", "00:45", "01:00")):
        load_rows.append(["USER-4201-000123-X", "2026-11-04", q, "25"])
    for q in ("01:15", "01:30", "01:45", "02:00"):
        load_rows.append(["USER-4201-000123-X", "2026-11-04", q, "30"])
    for q in ("00:15", "00:30", "00:45"):  # 缺 01:00 → I06 WARN
        load_rows.append(["USER-4201-000124-3", "2026-11-04", q, "20"])
    write_csv("d04_load_15min.csv", ["用户编号", "日期", "时刻", "电量(kWh)"], load_rows)
    written.append("d04_load_15min.csv")

    # D04 标准模板（带数据字典 sheet）与坏模板（表头被改 → 指纹不符）
    template_rows = [["USER-4201-000131-7", "2026-11-05", "1", "2026-11-05T00:00:00+08:00", "0.100", "4", "OK"],
                     ["USER-4201-000131-7", "2026-11-05", "2", "2026-11-05T01:00:00+08:00", "0.120", "4", "OK"]]
    template_dict = [["用户编号", "string", "-", "是", "用户业务标识"],
                     ["业务日期", "date", "-", "是", "电量归属自然日"],
                     ["时段号", "int", "-", "是", "1..24"],
                     ["区间开始", "datetime", "+08:00", "是", "小时区间起点"],
                     ["小时电量(MWh)", "decimal", "MWh", "否", "小时电量"],
                     ["15分钟点数", "int", "-", "是", "该小时 15 分钟源点数"],
                     ["质量标识", "enum", "-", "是", "OK/ESTIMATED/PARTIAL/SUSPECT/MISSING"]]
    write_xlsx("template_load_hourly_template.xlsx", "小时负荷", TEMPLATE_HEADERS, template_rows, template_dict)
    written.append("template_load_hourly_template.xlsx")
    bad_headers = list(TEMPLATE_HEADERS)
    bad_headers[4] = "电量(MWh)"
    write_xlsx("template_load_hourly_bad.xlsx", "小时负荷", bad_headers, template_rows, template_dict)
    written.append("template_load_hourly_bad.xlsx")

    # D05 批发合同（两个版本，供版本替代与 as-of 校验）
    curve = json.dumps([round(0.4 + 0.02 * i, 4) for i in range(24)], ensure_ascii=False)
    whale_header = ["合同编号", "品种", "对手方", "开始日期", "结束日期", "合同电量(MWh)",
                    "电价类型", "价格(元/MWh)", "公式", "版本", "状态", "24时段曲线(JSON)"]
    whale_rows = [
        ["CONTRACT-4201-000001-5", "ANNUAL", "TRADER-4201-000001-3", "2026-01-01", "2026-12-31",
         "12000.000", "FIXED", "435.6000", "", "1", "SIGNED", curve],
        ["CONTRACT-4201-000001-5", "ANNUAL", "TRADER-4201-000001-3", "2026-02-01", "2026-12-31",
         "13200.000", "FIXED", "441.2000", "补充协议：2 月起执行新价", "2", "SIGNED", curve],
    ]
    write_xlsx("d05_wholesale_contract.xlsx", "批发合同", whale_header, whale_rows)
    written.append("d05_wholesale_contract.xlsx")

    # D06 零售合同
    write_xlsx("d06_retail_contract.xlsx", "零售合同",
               ["合同编号", "用户编号", "套餐编号", "电价类型", "公式", "起始日期", "结束日期", "版本", "状态"],
               [["CONTRACT-4201-000002-8", "USER-4201-000123-X", "PACKAGE-4201-000012-2", "TOU",
                 "峰段0.6电价+谷段0.4电价", "2026-01-01", "2026-12-31", "1", "SIGNED"],
                ["CONTRACT-4201-000003-2", "USER-4201-000124-3", "PACKAGE-4201-000013-5", "LINKED",
                 "与湖北现货月均价联动", "2026-01-01", "2026-12-31", "1", "SIGNED"]])
    written.append("d06_retail_contract.xlsx")

    # D07 交易申报
    write_csv("d07_declaration.csv",
              ["申报批次号", "交易日", "品种", "时段号", "申报电量(MWh)", "申报价(元/MWh)", "提交时刻", "状态"],
              [["DECL-20261105-01", "2026-11-05", "DAY_AHEAD", "9", "12.500", "412.3000",
                "2026-11-05T09:30:00+08:00", "ACCEPTED"],
               ["DECL-20261105-01", "2026-11-05", "DAY_AHEAD", "10", "11.000", "410.1000",
                "2026-11-05T09:31:00+08:00", "ACCEPTED"]])
    written.append("d07_declaration.csv")

    # D08 成交结果
    write_csv("d08_trade_result.csv",
              ["成交编号", "交易日", "品种", "时段号", "成交电量(MWh)", "成交价(元/MWh)", "费用(元)", "对手方", "出清时刻"],
              [["TRD-20261105-0001", "2026-11-05", "DAY_AHEAD", "9", "12.500", "412.3000", "5153.75",
                "TRADER-4201-000001-3", "2026-11-05T09:45:00+08:00"],
               ["TRD-20261105-0002", "2026-11-05", "DAY_AHEAD", "10", "11.000", "410.1000", "4511.10",
                "TRADER-4201-000001-3", "2026-11-05T09:45:00+08:00"]])
    written.append("d08_trade_result.csv")

    # D09 日清
    write_csv("d09_daily_settlement.csv",
              ["日期", "时段号", "科目编码", "方向", "电量(MWh)", "电价(元/MWh)", "金额(元)", "结算日", "依据条款", "质量标识"],
              [["2026-11-04", "9", "S01-电能量", "BUY", "12.500", "412.3000", "5153.75",
                "2026-11-05", "湖北零售细则 第3.2条", "OK"],
               ["2026-11-04", "10", "S01-电能量", "BUY", "11.000", "410.1000", "4511.10",
                "2026-11-05", "湖北零售细则 第3.2条", "OK"],
               ["2026-11-04", "11", "S02-输配电费", "SELL", "23.500", "38.6000", "907.10",
                "2026-11-05", "湖北零售细则 第4.1条", "OK"]])
    written.append("d09_daily_settlement.csv")

    # D10 月结
    write_csv("d10_monthly_settlement.csv",
              ["结算月份", "科目编码", "方向", "电量(MWh)", "金额(元)", "追退补类型", "关联月份", "序号", "依据条款", "结算日"],
              [["2026-10", "S01-电能量", "BUY", "9000.000", "3700000.00", "NONE", "", "0", "湖北零售细则 第3.2条", "2026-11-01"],
               ["2026-10", "S03-政府性基金", "SELL", "9000.000", "486000.00", "SUPPLEMENT", "2026-09", "1",
                "湖北零售细则 第6.4条", "2026-11-01"]])
    written.append("d10_monthly_settlement.csv")

    # D11 天气（JSON 数组）
    weather = [
        {"region_code": "420100", "valid_at": "2026-11-04T00:00:00+08:00", "temperature_c": "12.5",
         "humidity_pct": "65.0", "weather_code": "CLEAR", "alert_level": "NONE",
         "source": "SRC-EXT-WEATHER-API"},
        {"region_code": "420100", "valid_at": "2026-11-04T01:00:00+08:00", "temperature_c": "12.0",
         "humidity_pct": "67.0", "weather_code": "CLEAR", "alert_level": "NONE",
         "source": "SRC-EXT-WEATHER-API"},
    ]
    if _enabled("ext_weather.json"):
        (SAMPLES / "ext_weather.json").write_text(json.dumps(weather, ensure_ascii=False, indent=1), encoding="utf-8")
    written.append("ext_weather.json")

    # 外部通道故障样例（→ W09）
    if _enabled("ext_weather_broken.json"):
        (SAMPLES / "ext_weather_broken.json").write_text('[{"region_code": "420100", "valid_at":', encoding="utf-8")
    written.append("ext_weather_broken.json")

    # D12 日历
    write_csv("ext_calendar.csv",
              ["日期", "日期类型", "节假日名称", "是否工作日", "补班日期", "分时版本", "来源"],
              [["2026-11-04", "NORMAL_WORKDAY", "", "是", "", "v1", "SRC-EXT-CALENDAR-CSV"],
               ["2026-10-01", "STATUTORY_HOLIDAY", "国庆节", "否", "", "v1", "SRC-EXT-CALENDAR-CSV"],
               ["2026-10-10", "MAKEUP_WORKDAY", "", "是", "2026-10-04", "v1", "SRC-EXT-CALENDAR-CSV"]])
    written.append("ext_calendar.csv")

    # D13 生产计划修正
    write_csv("ext_production_plan.csv",
              ["修正编号", "用户编号", "业务日期", "类型", "影响比例", "原因", "来源"],
              [["CORR-20261104-001", "USER-4201-000123-X", "2026-11-04", "SHUTDOWN", "0.3500",
                "计划检修", "SRC-EXT-PLAN-CSV"],
               ["CORR-20261104-002", "USER-4201-000124-3", "2026-11-04", "BOOST", "0.1200",
                "临时增产", "SRC-EXT-PLAN-CSV"]])
    written.append("ext_production_plan.csv")

    # D01 规则文件（PDF 文本层）与无文本层扫描件（→ I02）
    write_pdf("d01_rule_doc.pdf", [
        ("doc_id", "RULE-HB-2025-0009"),
        ("doc_no", "鄂电交易〔2025〕9号"),
        ("title", "湖北电力零售市场交易实施细则（2025年版）"),
        ("issuer", "湖北电力交易中心"),
        ("doc_type", "PROVINCIAL"),
        ("publish_date", "2025-12-20"),
        ("effective_from", "2026-01-01"),
        ("status", "ACTIVE"),
        ("url", "https://example.invalid/hb/2025/9"),
        ("clause_count", "120"),
    ])
    written.append("d01_rule_doc.pdf")
    write_pdf("d01_rule_doc_scanned.pdf", [], with_text_layer=False)
    written.append("d01_rule_doc_scanned.pdf")

    # ---- 脏样例：每个只命中一条规则 ----
    d09_header = ["日期", "时段号", "科目编码", "方向", "电量(MWh)", "电价(元/MWh)", "金额(元)", "结算日", "依据条款", "质量标识"]
    write_csv("dirty_q01_missing.csv", d09_header,
              [["2026-11-06", "9", "S01-电能量", "BUY", "", "412.3000", "5153.75", "2026-11-07", "细则 3.2", "OK"]])
    written.append("dirty_q01_missing.csv")
    write_csv("dirty_q02_dup.csv", d09_header,
              [["2026-11-06", "9", "S01-电能量", "BUY", "12.500", "412.3000", "5153.75", "2026-11-07", "细则 3.2", "OK"],
               ["2026-11-06", "9", "S01-电能量", "BUY", "12.500", "412.3000", "5153.75", "2026-11-07", "细则 3.2", "OK"]])
    written.append("dirty_q02_dup.csv")
    write_csv("dirty_q03_range.csv", d09_header,
              [["2026-11-06", "9", "S01-电能量", "BUY", "12.500", "412.3000", "5153.75", "2026-11-07", "细则 3.2", "ESTIMATED"],
               ["2026-11-06", "99", "S01-电能量", "BUY", "1.000", "412.3000", "412.30", "2026-11-07", "细则 3.2", "OK"]])
    written.append("dirty_q03_range.csv")
    write_csv("dirty_q06_logic.csv", d09_header,
              [["2026-11-06", "9", "S01-电能量", "BUY", "12.500", "412.3000", "9999.99", "2026-11-07", "细则 3.2", "OK"]])
    written.append("dirty_q06_logic.csv")
    write_csv("dirty_q04_shift.csv",
              ["申报批次号", "交易日", "品种", "时段号", "申报电量(MWh)", "申报价(元/MWh)", "提交时刻", "状态"],
              [["DECL-20261105-09", "2026-11-05", "DAY_AHEAD", "9", "12.500", "412.3000",
                "2026-11-06T09:30:00+08:00", "SUBMITTED"]])
    written.append("dirty_q04_shift.csv")
    write_csv("dirty_q05_unit.csv",
              ["用户编号", "业务日期", "时段号", "区间开始", "电量(kWh)", "15分钟点数", "质量标识"],
              [["USER-4201-000129-1", "2026-11-05", "1", "2026-11-05T00:00:00+08:00", "150", "4", "OK"]])
    written.append("dirty_q05_unit.csv")
    write_csv("dirty_i03_unit.csv",
              ["用户编号", "业务日期", "时段号", "区间开始", "电量(斤)", "15分钟点数", "质量标识"],
              [["USER-4201-000130-5", "2026-11-05", "1", "2026-11-05T00:00:00+08:00", "150", "4", "OK"]])
    written.append("dirty_i03_unit.csv")

    write_csv("dirty_i04_missing_required.csv",
              ["计量点编号", "用户编号", "计量周期", "CT变比", "PT变比", "计量单位", "状态", "投运日期"],
              [["METER-4201-000458-1", "USER-4201-000123-X", "HOURLY", "40.0000", "100.0000", "MWH", "ACTIVE", "2021-07-01"]])
    written.append("dirty_i04_missing_required.csv")

    write_csv("dirty_i05_too_large.csv", d09_header,
              [["2026-11-06", "9", "S01-电能量", "BUY", "12.500", "412.3000", "5153.75", "2026-11-07", "细则 3.2", "OK"],
               ["2026-11-06", "10", "S01-电能量", "BUY", "11.000", "410.1000", "4511.00", "2026-11-07", "细则 3.2", "OK"],
               ["2026-11-06", "11", "S02-输配电费", "SELL", "23.500", "38.6000", "907.10", "2026-11-07", "细则 3.2", "OK"]])
    written.append("dirty_i05_too_large.csv")

    actual = [n for n in written if (SAMPLES / n).exists()]
    print("generated %d sample files under %s" % (len(actual), SAMPLES))
    for name in actual:
        print("  -", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
