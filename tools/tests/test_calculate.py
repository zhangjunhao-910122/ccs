"""tools/calculate.py 的 unittest 测试。

覆盖范围（对应 Issue #21 验收要求）：
  * 日历：跨月、跨年、2 月末、闰日（2024 / 2028）、dateutil 与回退实现等价
  * 比例临界：恰好 80%、恰好 1/3、恰好 2/3、恰好 30%（临界值均判为满足）
  * 人数区间边界：15/16、25/26、45/46、65/66、85/86
  * 届满当日：届满当日算届满、起算当日不计入
  * 参数未核验：返回「参数待核」错误，不猜值
  * CLI：四个子命令 + 退出码 0/1/2/4
  * 数值来源：改参数表即改结果（证明数值不硬编码在代码里）
"""

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from fractions import Fraction
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import calculate as calc  # noqa: E402
from calendar_ratio import (  # noqa: E402
    HAS_DATEUTIL,
    add_days,
    add_months,
    add_years,
    at_least,
    ceil_fraction,
    days_between,
    parse_date,
    round_to_step,
    to_fraction,
)

PARAMS_FILE = TOOLS_DIR / "params.yaml"
STORE = calc.ParamStore.load(str(PARAMS_FILE))

RESULT_FIELDS = (
    "inputs",
    "units",
    "basis",
    "formula",
    "rounding",
    "result",
    "recomputed",
    "source_ref",
    "verified",
)


# ---------------------------------------------------------------------------
# 日历与比例基础
# ---------------------------------------------------------------------------
class TestCalendarRatio(unittest.TestCase):
    def test_add_months_cross_month(self):
        self.assertEqual(add_months(date(2026, 1, 15), 1), date(2026, 2, 15))
        self.assertEqual(add_months(date(2026, 6, 1), 3), date(2026, 9, 1))

    def test_add_months_cross_year(self):
        self.assertEqual(add_months(date(2026, 11, 15), 3), date(2027, 2, 15))
        self.assertEqual(add_months(date(2026, 12, 1), 6), date(2027, 6, 1))
        self.assertEqual(add_months(date(2026, 12, 1), 12), date(2027, 12, 1))
        self.assertEqual(add_months(date(2026, 12, 15), 1), date(2027, 1, 15))

    def test_add_months_month_end_converges(self):
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2026, 3, 31), 1), date(2026, 4, 30))
        self.assertEqual(add_months(date(2026, 8, 31), 6), date(2027, 2, 28))
        self.assertEqual(add_months(date(2026, 5, 31), 1), date(2026, 6, 30))

    def test_add_months_leap_day(self):
        self.assertEqual(add_months(date(2024, 2, 29), 1), date(2024, 3, 29))
        self.assertEqual(add_months(date(2024, 2, 29), 12), date(2025, 2, 28))
        self.assertEqual(add_months(date(2028, 2, 29), 12), date(2029, 2, 28))

    def test_add_years_leap_day(self):
        self.assertEqual(add_years(date(2024, 2, 29), 1), date(2025, 2, 28))
        self.assertEqual(add_years(date(2028, 2, 29), 1), date(2029, 2, 28))
        self.assertEqual(add_years(date(2024, 2, 29), 4), date(2028, 2, 29))

    def test_fallback_matches_dateutil_path(self):
        month_cases = [
            (date(2026, 1, 31), 1),
            (date(2026, 12, 31), 2),
            (date(2024, 2, 29), 12),
            (date(2026, 11, 15), 3),
        ]
        for start, months in month_cases:
            self.assertEqual(
                add_months(start, months),
                add_months(start, months, use_dateutil=False),
                "add_months(%s, %d)" % (start, months),
            )
        year_cases = [
            (date(2024, 2, 29), 1),
            (date(2028, 2, 29), 4),
            (date(2026, 6, 1), 3),
        ]
        for start, years in year_cases:
            self.assertEqual(
                add_years(start, years),
                add_years(start, years, use_dateutil=False),
                "add_years(%s, %d)" % (start, years),
            )

    def test_days_between_and_add_days(self):
        self.assertEqual(days_between(date(2026, 6, 1), date(2026, 6, 6)), 5)
        self.assertEqual(add_days(date(2026, 6, 1), 5), date(2026, 6, 6))
        self.assertEqual(days_between(date(2026, 12, 1), date(2027, 6, 1)), 182)

    def test_parse_date_formats(self):
        self.assertEqual(parse_date("2026-06-01"), date(2026, 6, 1))
        self.assertEqual(parse_date("2026/06/01"), date(2026, 6, 1))
        with self.assertRaises(ValueError):
            parse_date("不是日期")

    def test_round_to_step_half_man_day(self):
        self.assertEqual(round_to_step(to_fraction(5.3)), Fraction(11, 2))
        self.assertEqual(round_to_step(to_fraction(5.2)), Fraction(5, 1))
        self.assertEqual(round_to_step(to_fraction(5.25)), Fraction(11, 2))
        self.assertEqual(round_to_step(to_fraction(5.75)), Fraction(6, 1))
        self.assertEqual(round_to_step(to_fraction(7)), Fraction(7, 1))

    def test_exact_fraction_comparison(self):
        self.assertEqual(to_fraction(0.8), Fraction(4, 5))
        self.assertEqual(to_fraction(0.3), Fraction(3, 10))
        self.assertTrue(at_least(Fraction(32, 40), Fraction(4, 5)))
        self.assertFalse(at_least(Fraction(319, 1000), Fraction(4, 5)))

    def test_ceil_fraction(self):
        self.assertEqual(ceil_fraction(Fraction(3)), 3)
        self.assertEqual(ceil_fraction(Fraction(21, 5)), 5)
        self.assertEqual(ceil_fraction(Fraction(4)), 4)
        self.assertEqual(ceil_fraction(Fraction(6, 10) * 10), 6)

    def test_dateutil_available_flag(self):
        self.assertIn(HAS_DATEUTIL, (True, False))


# ---------------------------------------------------------------------------
# 人日计算
# ---------------------------------------------------------------------------
class TestManDays(unittest.TestCase):
    def test_qms_120_medium_initial_three_sites(self):
        result = calc.man_days("qms", 120, "medium", sites=3, params=STORE)
        self.assertEqual(result.result["headcount_band"], "86-125")
        self.assertEqual(result.result["base_days"], 7.0)
        self.assertEqual(result.result["risk_factor"], 1.0)
        self.assertEqual(result.result["total_man_days"], 7.0)
        self.assertEqual(result.result["total_hours"], 56)
        self.assertEqual(result.result["per_site"]["sampled_sites"], 3)
        self.assertEqual(result.result["per_site"]["per_site_man_days"], 2.5)
        self.assertTrue(result.verified)
        self.assertIn("CNCA-QMS-01:2025", " ".join(result.source_ref))

    def test_result_has_fixed_fields(self):
        result = calc.man_days("qms", 120, "medium", params=STORE)
        payload = result.to_dict()
        self.assertEqual(tuple(payload.keys()), RESULT_FIELDS)

    def test_qms_band_boundaries(self):
        cases = {
            15: 2.5, 16: 3, 25: 3, 26: 4, 45: 4, 46: 5,
            65: 5, 66: 6, 85: 6, 86: 7,
        }
        for headcount, days in cases.items():
            result = calc.man_days("qms", headcount, "medium", params=STORE)
            self.assertEqual(result.result["total_man_days"], days,
                             "有效人数 %d 应落在 %.1f 人日" % (headcount, days))

    def test_qms_risk_adjust(self):
        low = calc.man_days("qms", 120, "low", params=STORE)
        self.assertEqual(low.result["total_man_days"], 6.5)      # 7 x 0.9 = 6.3 -> 6.5
        medium = calc.man_days("qms", 120, "medium", params=STORE)
        self.assertEqual(medium.result["total_man_days"], 7.0)
        high = calc.man_days("qms", 120, "high", params=STORE)
        self.assertEqual(high.result["total_man_days"], 7.5)      # 7 x 1.1 = 7.7 -> 7.5

    def test_ems_and_ohsms_medium_column(self):
        for profile in ("ems", "ohsms"):
            result = calc.man_days(profile, 120, "medium", params=STORE)
            self.assertEqual(result.result["total_man_days"], 8.0)
            self.assertEqual(result.result["headcount_band"], "86-125")

    def test_ems_band_boundaries(self):
        cases = {15: 3.5, 16: 4.5, 25: 4.5, 26: 5.5, 45: 5.5, 46: 6.0}
        for headcount, days in cases.items():
            result = calc.man_days("ems", headcount, "medium", params=STORE)
            self.assertEqual(result.result["total_man_days"], days,
                             "有效人数 %d 应落在 %.1f 人日" % (headcount, days))

    def test_supervision_and_recert_factor(self):
        supervision = calc.man_days("qms", 120, "medium", audit_type="supervision", params=STORE)
        self.assertEqual(supervision.result["audit_type_factor"], "1/3")
        self.assertEqual(supervision.result["total_man_days"], 2.5)   # 7/3 = 2.33 -> 2.5
        recert = calc.man_days("qms", 120, "medium", audit_type="recert", params=STORE)
        self.assertEqual(recert.result["audit_type_factor"], "2/3")
        self.assertEqual(recert.result["total_man_days"], 4.5)        # 14/3 = 4.67 -> 4.5

    def test_multi_site_allocation_supervision(self):
        result = calc.man_days("qms", 120, "medium", sites=3,
                               audit_type="supervision", params=STORE)
        per_site = result.result["per_site"]
        self.assertEqual(per_site["sampled_sites"], 2)            # ceil(0.6 x 3)
        self.assertEqual(per_site["per_site_man_days"], 1.5)      # 2.5 / 2 = 1.25 -> 1.5
        self.assertEqual(per_site["allocated_total"], 3.0)
        self.assertEqual(per_site["allocation_difference"], 0.5)

    def test_non_similar_sites(self):
        initial = calc.man_days("qms", 120, "medium", sites=4,
                                site_type="non-similar", params=STORE)
        self.assertEqual(initial.result["per_site"]["sampled_sites"], 4)
        supervision = calc.man_days("qms", 120, "medium", sites=10, site_type="non-similar",
                                    audit_type="supervision", params=STORE)
        self.assertEqual(supervision.result["per_site"]["sampled_sites"], 3)

    def test_min_onsite_ratio_reported(self):
        result = calc.man_days("qms", 120, "medium", params=STORE)
        self.assertEqual(result.result["min_onsite_man_days"], 5.6)  # 7 x 0.8

    def test_unverified_param_returns_param_pending(self):
        with self.assertRaises(calc.ParamError) as ctx:
            calc.man_days("ems", 120, "limited", params=STORE)
        self.assertIn("参数待核", str(ctx.exception))

    def test_above_max_headcount_returns_param_pending(self):
        for profile in ("qms", "ems", "ohsms"):
            with self.assertRaises(calc.ParamError) as ctx:
                calc.man_days(profile, 10701, "medium", params=STORE)
            self.assertIn("参数待核", str(ctx.exception))

    def test_risk_not_supported_by_profile(self):
        with self.assertRaises(calc.InputError):
            calc.man_days("ohsms", 120, "limited", params=STORE)
        with self.assertRaises(calc.InputError):
            calc.man_days("qms", 120, "special", params=STORE)

    def test_bad_inputs(self):
        with self.assertRaises(calc.InputError):
            calc.man_days("xxx", 120, "medium", params=STORE)
        with self.assertRaises(calc.InputError):
            calc.man_days("qms", 0, "medium", params=STORE)
        with self.assertRaises(calc.InputError):
            calc.man_days("qms", 120, "medium", sites=0, params=STORE)

    def test_inputs_are_copied_not_shared(self):
        first = calc.man_days("qms", 120, "medium", params=STORE)
        first.inputs["headcount"] = 999
        second = calc.man_days("qms", 120, "medium", params=STORE)
        self.assertEqual(second.inputs["headcount"], 120)

    def test_recomputed_matches_result(self):
        result = calc.man_days("qms", 120, "medium", sites=3, params=STORE)
        self.assertEqual(result.result["total_man_days"], result.recomputed["total_man_days"])
        self.assertEqual(result.result["total_hours"], result.recomputed["total_hours"])


# ---------------------------------------------------------------------------
# 比例计算
# ---------------------------------------------------------------------------
class TestRatio(unittest.TestCase):
    def test_onsite_ratio_exactly_80_percent(self):
        result = calc.ratio_onsite(40, 32, params=STORE)
        self.assertEqual(result.result["conclusion"], "合规")
        self.assertEqual(result.result["onsite_ratio"], 0.8)
        self.assertEqual(result.result["required_onsite_man_days"], 32.0)
        self.assertEqual(result.result["required_total_man_days_from_onsite"], 40.0)

    def test_onsite_ratio_below_80_percent(self):
        result = calc.ratio_onsite(40, 31.9, params=STORE)
        self.assertEqual(result.result["conclusion"], "不合规")
        self.assertEqual(result.result["onsite_check"], "不合规")

    def test_onsite_ratio_total_side_check(self):
        ok = calc.ratio_onsite(40, 32, params=STORE)
        self.assertEqual(ok.result["total_check"], "合规")
        bad = calc.ratio_onsite(39.9, 32, params=STORE)
        self.assertEqual(bad.result["total_check"], "不合规")
        self.assertEqual(bad.result["conclusion"], "不合规")

    def test_supervision_exactly_one_third(self):
        ok = calc.ratio_audit_time("supervision", 6, 2, params=STORE)
        self.assertEqual(ok.result["conclusion"], "合规")
        self.assertEqual(ok.result["required_man_days"], 2.0)
        self.assertEqual(ok.result["min_ratio_exact"], "1/3")
        bad = calc.ratio_audit_time("supervision", 6, 1.9, params=STORE)
        self.assertEqual(bad.result["conclusion"], "不合规")

    def test_recert_exactly_two_thirds(self):
        ok = calc.ratio_audit_time("recert", 6, 4, params=STORE)
        self.assertEqual(ok.result["conclusion"], "合规")
        self.assertEqual(ok.result["required_man_days"], 4.0)
        self.assertEqual(ok.result["min_ratio_exact"], "2/3")
        bad = calc.ratio_audit_time("recert", 6, 3.9, params=STORE)
        self.assertEqual(bad.result["conclusion"], "不合规")

    def test_combined_exactly_80_percent(self):
        ok = calc.ratio_combined(10, 8, params=STORE)
        self.assertEqual(ok.result["conclusion"], "合规")
        self.assertEqual(ok.result["required_man_days"], 8.0)
        bad = calc.ratio_combined(10, 7.9, params=STORE)
        self.assertEqual(bad.result["conclusion"], "不合规")

    def test_sampling_counts_similar_sites(self):
        self.assertEqual(
            calc.sampling_count(5, "initial", "similar", params=STORE).result["sampled_sites"], 5)
        self.assertEqual(
            calc.sampling_count(5, "supervision", "similar", params=STORE).result["sampled_sites"], 3)
        self.assertEqual(
            calc.sampling_count(7, "supervision", "similar", params=STORE).result["sampled_sites"], 5)
        self.assertEqual(
            calc.sampling_count(5, "recert", "similar", params=STORE).result["sampled_sites"], 4)

    def test_non_similar_sampling_rules(self):
        self.assertEqual(
            calc.sampling_count(4, "initial", "non-similar", params=STORE).result["sampled_sites"], 4)
        self.assertEqual(
            calc.sampling_count(4, "recert", "non-similar", params=STORE).result["sampled_sites"], 4)
        result = calc.sampling_count(10, "supervision", "non-similar", params=STORE)
        self.assertEqual(result.result["sampled_sites"], 3)
        self.assertTrue(result.result["includes_central_function"])

    def test_supervision_sites_exactly_30_percent(self):
        ok = calc.ratio_supervision_sites(10, 3, params=STORE)
        self.assertEqual(ok.result["conclusion"], "合规")
        self.assertEqual(ok.result["min_sampled_sites"], 3)
        bad = calc.ratio_supervision_sites(10, 2, params=STORE)
        self.assertEqual(bad.result["conclusion"], "不合规")

    def test_ratio_results_have_fixed_fields(self):
        result = calc.ratio_onsite(40, 32, params=STORE)
        self.assertEqual(tuple(result.to_dict().keys()), RESULT_FIELDS)


# ---------------------------------------------------------------------------
# 期限计算
# ---------------------------------------------------------------------------
class TestDeadline(unittest.TestCase):
    def test_nc_verification_initial(self):
        result = calc.deadline_nc_verification("initial", "2026-06-01", params=STORE)
        self.assertEqual(result.result["deadline"], "2026-12-01")
        self.assertTrue(result.result["deadline_day_included"])
        self.assertFalse(result.result["start_day_counted"])
        self.assertIn("届满当日", result.basis)

    def test_nc_verification_supervision(self):
        result = calc.deadline_nc_verification("supervision", "2026-06-01", params=STORE)
        self.assertEqual(result.result["deadline"], "2026-09-01")

    def test_nc_verification_recert_uses_certificate_expiry(self):
        result = calc.deadline_nc_verification("recert", "2026-06-01",
                                               cert_expiry="2026-12-01", params=STORE)
        self.assertEqual(result.result["deadline"], "2026-12-01")
        with self.assertRaises(calc.InputError):
            calc.deadline_nc_verification("recert", "2026-06-01", params=STORE)

    def test_nc_verification_month_end_converges(self):
        result = calc.deadline_nc_verification("initial", "2026-08-31", params=STORE)
        self.assertEqual(result.result["deadline"], "2027-02-28")

    def test_nc_verification_leap_day(self):
        result = calc.deadline_nc_verification("initial", "2024-02-29", params=STORE)
        self.assertEqual(result.result["deadline"], "2024-08-29")

    def test_nc_verification_cross_year(self):
        result = calc.deadline_nc_verification("initial", "2026-11-30", params=STORE)
        self.assertEqual(result.result["deadline"], "2027-05-30")

    def test_nc_verification_clause_differs_by_profile(self):
        qms = calc.deadline_nc_verification("initial", "2026-06-01", profile="qms", params=STORE)
        ems = calc.deadline_nc_verification("initial", "2026-06-01", profile="ems", params=STORE)
        self.assertTrue(any("5.10.3" in ref for ref in qms.source_ref))
        self.assertTrue(any("5.10.4" in ref for ref in ems.source_ref))

    def test_certificate_expiry(self):
        result = calc.deadline_certificate_expiry("2026-06-01", params=STORE)
        self.assertEqual(result.result["expiry_date"], "2029-06-01")
        self.assertEqual(result.result["max_validity_years"], 3)

    def test_certificate_expiry_leap_day(self):
        self.assertEqual(
            calc.deadline_certificate_expiry("2024-02-29", params=STORE).result["expiry_date"],
            "2027-02-28")
        self.assertEqual(
            calc.deadline_certificate_expiry("2028-02-29", params=STORE).result["expiry_date"],
            "2031-02-28")

    def test_certificate_expiry_rejects_more_than_three_years(self):
        with self.assertRaises(calc.InputError):
            calc.deadline_certificate_expiry("2026-06-01", years=4, params=STORE)

    def test_certificate_cycle(self):
        ok = calc.deadline_certificate_cycle("2026-06-01", "2026-05-01", params=STORE)
        self.assertEqual(ok.result["new_expiry_date"], "2029-05-01")
        self.assertEqual(ok.result["max_new_expiry_date"], "2029-06-01")
        self.assertEqual(ok.result["conclusion"], "合规")
        bad = calc.deadline_certificate_cycle("2026-06-01", "2026-07-01", params=STORE)
        self.assertEqual(bad.result["new_expiry_date"], "2029-07-01")
        self.assertEqual(bad.result["conclusion"], "不合规")

    def test_initial_decision(self):
        result = calc.deadline_initial_decision("2026-06-01", params=STORE)
        self.assertEqual(result.result["deadline"], "2026-12-01")
        self.assertTrue(result.result["deadline_day_included"])

    def test_recert_decision(self):
        result = calc.deadline_recert_decision("2026-12-01", params=STORE)
        self.assertEqual(result.result["should_complete_by"], "2026-12-01")
        self.assertEqual(result.result["latest_date"], "2027-06-01")

    def test_two_stage_gap_minimum_five_days(self):
        ok = calc.deadline_two_stage_gap("2026-06-01", "2026-06-06", params=STORE)
        self.assertEqual(ok.result["gap_days"], 5)
        self.assertEqual(ok.result["conclusion"], "合规")
        self.assertEqual(ok.result["earliest_stage2_start"], "2026-06-06")
        bad = calc.deadline_two_stage_gap("2026-06-01", "2026-06-05", params=STORE)
        self.assertEqual(bad.result["gap_days"], 4)
        self.assertEqual(bad.result["conclusion"], "不合规")

    def test_two_stage_gap_max_six_months(self):
        ok = calc.deadline_two_stage_gap("2026-06-01", "2026-12-01", params=STORE)
        self.assertEqual(ok.result["conclusion"], "合规")
        self.assertEqual(ok.result["latest_stage2_start"], "2026-12-01")
        bad = calc.deadline_two_stage_gap("2026-06-01", "2026-12-02", params=STORE)
        self.assertEqual(bad.result["conclusion"], "不合规")

    def test_two_stage_gap_cross_year(self):
        result = calc.deadline_two_stage_gap("2026-11-20", "2027-05-20", params=STORE)
        self.assertEqual(result.result["conclusion"], "合规")
        self.assertEqual(result.result["gap_days"], 181)

    def test_deadline_results_have_fixed_fields(self):
        result = calc.deadline_initial_decision("2026-06-01", params=STORE)
        self.assertEqual(tuple(result.to_dict().keys()), RESULT_FIELDS)


# ---------------------------------------------------------------------------
# 参数表
# ---------------------------------------------------------------------------
class TestParamTable(unittest.TestCase):
    def test_params_file_exists(self):
        self.assertTrue(PARAMS_FILE.exists())

    def test_list_params_contains_key_paths(self):
        paths = {row["path"] for row in calc.list_params(STORE)}
        for expected in (
            "ratio.onsite_min",
            "ratio.surveillance_min",
            "ratio.recert_min",
            "ratio.combined_min_vs_sum",
            "deadline.nc_verification.initial",
            "deadline.two_stage_gap",
            "deadline.certificate_validity_max_years",
            "man_day.qms.table[0]",
            "man_day.ems.table[0]",
            "man_day.ohsms.table[0]",
        ):
            self.assertIn(expected, paths)

    def test_unverified_params_are_listed(self):
        paths = {row["path"] for row in calc.list_params(STORE, only_unverified=True)}
        self.assertIn("man_day.qms.above_max", paths)
        self.assertIn("man_day.ems.above_max", paths)
        self.assertIn("man_day.ohsms.above_max", paths)
        self.assertIn("man_day.ems.limited_column", paths)
        self.assertIn("conventions.calendar_month", paths)

    def test_require_raises_for_unverified_blocking_param(self):
        with self.assertRaises(calc.ParamError) as ctx:
            STORE.require("man_day.ems.limited_column")
        self.assertIn("参数待核", str(ctx.exception))

    def test_require_allows_unverified_non_blocking_convention(self):
        node, verified = STORE.require("conventions.calendar_month")
        self.assertFalse(verified)
        self.assertIn("note", node)

    def test_qms_table_matches_research_md(self):
        expected = [
            (0, 15, 2.5), (16, 25, 3), (26, 45, 4), (46, 65, 5), (66, 85, 6),
            (86, 125, 7), (126, 175, 8), (176, 275, 9), (276, 425, 10),
            (426, 625, 11), (626, 875, 12), (876, 1175, 13), (1176, 1550, 14),
            (1551, 2025, 15), (2026, 2675, 16), (2676, 3450, 17), (3451, 4350, 18),
            (4351, 5450, 19), (5451, 6800, 20), (6801, 8500, 21), (8501, 10700, 22),
        ]
        table = STORE.get("man_day.qms.table")
        for low, high, days in expected:
            row = None
            for item in table:
                if int(item["min"]) == low and int(item["max"]) == high:
                    row = item
                    break
            self.assertIsNotNone(row, "缺 %d-%d 行" % (low, high))
            self.assertEqual(float(row["days"]), days)
            self.assertIsNotNone(row.get("md_ref"))

    def test_ems_ohsms_md_rows_match_research_md(self):
        md_rows = [
            (0, 15, {"high": 4.5, "medium": 3.5, "low": 3.0}),
            (16, 25, {"high": 5.5, "medium": 4.5, "low": 3.5}),
            (26, 45, {"high": 7, "medium": 5.5, "low": 4.0}),
            (46, 65, {"high": 8, "medium": 6, "low": 4.5}),
            (876, 1175, {"high": 19, "medium": 15, "low": 11}),
            (2026, 2675, {"high": 23, "medium": 18, "low": 13}),
        ]
        for profile in ("ems", "ohsms"):
            table = STORE.get("man_day.%s.table" % profile)
            for low, high, values in md_rows:
                row = None
                for item in table:
                    if int(item["min"]) == low and int(item["max"]) == high:
                        row = item
                        break
                self.assertIsNotNone(row, "%s 缺 %d-%d 行" % (profile, low, high))
                for risk, value in values.items():
                    self.assertEqual(float(row["days"][risk]), value)

    def test_values_come_from_param_table_not_hardcoded(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "params.yaml"
            shutil.copyfile(str(PARAMS_FILE), str(target))
            text = target.read_text(encoding="utf-8")
            self.assertIn("value: 0.8", text)
            text = text.replace("value: 0.8", "value: 0.9")
            text = text.replace("days: 7,   md_ref", "days: 9,   md_ref")
            target.write_text(text, encoding="utf-8")
            store = calc.ParamStore.load(str(target))
            ratio_result = calc.ratio_onsite(40, 32, params=store)
            self.assertEqual(ratio_result.result["min_onsite_ratio"], 0.9)
            self.assertEqual(ratio_result.result["conclusion"], "不合规")
            man_day_result = calc.man_days("qms", 120, "medium", params=store)
            self.assertEqual(man_day_result.result["total_man_days"], 9.0)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
class TestCli(unittest.TestCase):
    def _run(self, argv):
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = calc.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_cli_man_days(self):
        code, out, _ = self._run([
            "man-days", "--profile", "qms", "--headcount", "120",
            "--risk", "medium", "--sites", "3",
        ])
        self.assertEqual(code, 0)
        self.assertIn("total_man_days", out)

    def test_cli_ratio_onsite(self):
        code, out, _ = self._run([
            "ratio", "--check", "onsite-ratio", "--total", "40", "--onsite", "30",
        ])
        self.assertEqual(code, 0)
        self.assertIn("不合规", out)

    def test_cli_ratio_onsite_json(self):
        code, out, _ = self._run([
            "ratio", "--check", "onsite-ratio", "--total", "40", "--onsite", "30",
            "--format", "json",
        ])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(tuple(payload.keys()), RESULT_FIELDS)
        self.assertEqual(payload["result"]["conclusion"], "不合规")

    def test_cli_deadline_nc_verification(self):
        code, out, _ = self._run([
            "deadline", "--kind", "nc-verification", "--audit-type", "initial",
            "--start", "2026-06-01",
        ])
        self.assertEqual(code, 0)
        self.assertIn("2026-12-01", out)

    def test_cli_deadline_certificate_expiry(self):
        code, out, _ = self._run([
            "deadline", "--kind", "certificate-expiry", "--issue", "2026-06-01",
        ])
        self.assertEqual(code, 0)
        self.assertIn("2029-06-01", out)

    def test_cli_params_list(self):
        code, out, _ = self._run(["params", "--list"])
        self.assertEqual(code, 0)
        self.assertIn("ratio.onsite_min", out)
        self.assertIn("待核", out)

    def test_cli_exit_code_2_for_unverified_param(self):
        code, _, err = self._run([
            "man-days", "--profile", "ems", "--headcount", "120", "--risk", "limited",
        ])
        self.assertEqual(code, 2)
        self.assertIn("参数待核", err)

    def test_cli_exit_code_1_for_bad_input(self):
        code, _, err = self._run([
            "man-days", "--profile", "xxx", "--headcount", "120",
        ])
        self.assertEqual(code, 1)
        self.assertIn("输入错误", err)

    def test_cli_exit_code_4_for_strict_non_compliant(self):
        code, _, _ = self._run([
            "ratio", "--check", "onsite-ratio", "--total", "40", "--onsite", "30", "--strict",
        ])
        self.assertEqual(code, 4)

    def test_cli_exit_code_0_for_strict_compliant(self):
        code, _, _ = self._run([
            "ratio", "--check", "onsite-ratio", "--total", "40", "--onsite", "32", "--strict",
        ])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
