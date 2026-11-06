# -*- coding: utf-8 -*-
"""模板缺陷扫描器（GitHub Issue #19 收尾补缺）。

扫描模板目录，按六类缺陷逐条给出「文件路径 / 行号 / 类别 / 命中文本 / 判定理由」：

  1. agency_form_number    机构表单号（命中登记表 entries 形态或机构差异配置 form_number.pattern）
  2. agency_name           机构名称硬编码（机构差异配置的 agency_name / agency_aliases 字面）
  3. internal_deadline     机构内部相对时限口径（RELATIVE_TIME_RE 一类）
  4. residual_real_date    残留真实日期（完整日期或四位年份，未被 R-D2/R-D3 标准号保护）
  5. cleaning_damage       清洗误伤（标准号被占位符切断，如 ISO 90{{时间}}15）
  6. residual_placeholder  残留占位符（全角双花括号 / 字典外半角占位符 / 裸花括号）

纪律：
  * 原件一个字节都不动：本工具**只读**被扫目录，从不写回。
  * 配置文件（form_number_registry.yaml、agency_profiles/*.yaml、sample_values.yaml）默认豁免；
    --include-config 可打开扫描，打开后命中项归类为 exempt_config，不计入缺陷。

用法：
  python tools/scan_templates.py --dir tools/templates/families --out docs/forms/defect-report.md
  python tools/scan_templates.py --dir tools/templates/families --json .research-tmp/defects.json
  python tools/scan_templates.py --selftest
  python tools/scan_templates.py --verify-profiles tools/templates/agency_profiles --dir tools/templates/families

退出码：0 = 无缺陷；2 = 有缺陷（--fail-on=none 放宽为 0）；1 = 运行错误。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent
TEMPLATES_DIR = TOOLS_DIR / "templates"
DEFAULT_PROFILE_PATH = TEMPLATES_DIR / "agency_profiles" / "example.yaml"
DEFAULT_REGISTRY_PATH = TEMPLATES_DIR / "form_number_registry.yaml"
DEFAULT_TARGET_DIR = TEMPLATES_DIR / "families"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import agency_isolation as ai  # noqa: E402


# --------------------------------------------------------------------------
# 类别定义
# --------------------------------------------------------------------------

CATEGORY_AGENCY_FORM_NUMBER = "agency_form_number"
CATEGORY_AGENCY_NAME = "agency_name"
CATEGORY_INTERNAL_DEADLINE = "internal_deadline"
CATEGORY_RESIDUAL_REAL_DATE = "residual_real_date"
CATEGORY_CLEANING_DAMAGE = "cleaning_damage"
CATEGORY_RESIDUAL_PLACEHOLDER = "residual_placeholder"
CATEGORY_EXEMPT_CONFIG = "exempt_config"

CATEGORY_DESCRIPTIONS: Dict[str, str] = {
    CATEGORY_AGENCY_FORM_NUMBER: "机构表单号",
    CATEGORY_AGENCY_NAME: "机构名称硬编码",
    CATEGORY_INTERNAL_DEADLINE: "机构内部相对时限口径",
    CATEGORY_RESIDUAL_REAL_DATE: "残留真实日期",
    CATEGORY_CLEANING_DAMAGE: "清洗误伤（标准号被占位符切断）",
    CATEGORY_RESIDUAL_PLACEHOLDER: "残留占位符",
}
DEFECT_CATEGORIES: Tuple[str, ...] = tuple(CATEGORY_DESCRIPTIONS.keys())

SEVERITY_DEFECT = "defect"
SEVERITY_INFO = "info"

#: 可扫描的文件后缀
SCAN_SUFFIXES: Tuple[str, ...] = (".yaml", ".yml", ".md", ".txt", ".json")

#: 配置文件（默认豁免）
CONFIG_FILE_NAMES = frozenset({"form_number_registry.yaml", "sample_values.yaml"})
CONFIG_DIR_NAMES = frozenset({"agency_profiles"})

#: 候选机构表单号 token（字母打头，可含分隔符与数字）
FORM_TOKEN_RE = re.compile(
    r"(?<![0-9A-Za-z])[0-9A-Za-z]*[A-Za-z][0-9A-Za-z]*(?:[-–/][0-9A-Za-z]+)+"
)

#: 标准编号前缀（R-D2）
STD_PREFIX_RE = re.compile(
    r"(?:ISO(?:/IEC)?|GB/T|CNCA(?:-[A-Z]{2,6})?|CNAS(?:-[A-Z]{2,6})?|GB|IAF|JF)"
)

#: 紧贴占位符左侧的标准号前缀（允许中间有空格、连接号与数字）
STD_PREFIX_TAIL_RE = re.compile(
    r"(?:ISO(?:/IEC)?|GB/T|CNCA(?:-[A-Z]{2,6})?|CNAS(?:-[A-Z]{2,6})?|GB|IAF|JF)\s*[-–]?\s*\d*$"
)

#: 紧贴占位符右侧的标准号前缀
STD_PREFIX_HEAD_RE = re.compile(
    r"^\s*[-–]?\s*(?:ISO(?:/IEC)?|GB/T|CNCA(?:-[A-Z]{2,6})?|CNAS(?:-[A-Z]{2,6})?|GB|IAF|JF)"
)

#: 日期类占位符（用于判断「数字被夹断」是否构成清洗误伤）
DATE_LIKE_KEYS = frozenset({"日期", "时间", "起止日期", "签字日期"})


# --------------------------------------------------------------------------
# 结果类型
# --------------------------------------------------------------------------


@dataclass
class Finding:
    """一条缺陷。"""

    category: str
    path: str
    line: int
    column: int
    match: str
    reason: str
    severity: str = SEVERITY_DEFECT

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "match": self.match,
            "reason": self.reason,
            "severity": self.severity,
        }


@dataclass
class ScanResult:
    """一次扫描的完整结果。"""

    roots: List[str] = field(default_factory=list)
    files_scanned: List[str] = field(default_factory=list)
    files_exempt: List[str] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    profile: Dict[str, Any] = field(default_factory=dict)
    registry: Dict[str, Any] = field(default_factory=dict)
    include_config: bool = False
    generated_at: str = ""

    # -- 统计 --------------------------------------------------------------

    def by_category(self) -> Dict[str, int]:
        counts = {cat: 0 for cat in DEFECT_CATEGORIES}
        for f in self.findings:
            if f.severity == SEVERITY_DEFECT and f.category in counts:
                counts[f.category] += 1
        return counts

    def defect_total(self) -> int:
        return sum(1 for f in self.findings if f.severity == SEVERITY_DEFECT)

    def exempt_total(self) -> int:
        return sum(1 for f in self.findings if f.severity == SEVERITY_INFO)

    # -- 序列化 ------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "tool": "scan_templates",
            "generated_at": self.generated_at,
            "roots": list(self.roots),
            "profile": dict(self.profile),
            "registry": dict(self.registry),
            "include_config": self.include_config,
            "files_scanned": list(self.files_scanned),
            "files_exempt": list(self.files_exempt),
            "summary": {
                "by_category": self.by_category(),
                "defect_total": self.defect_total(),
                "exempt_total": self.exempt_total(),
                "file_count": len(self.files_scanned),
            },
            "findings": [f.to_dict() for f in self.findings],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------


def _line_starts(text: str) -> List[int]:
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _line_col(line_starts: Sequence[int], offset: int) -> Tuple[int, int]:
    lo, hi = 0, len(line_starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if line_starts[mid] <= offset:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1, offset - line_starts[lo] + 1


def _merge_spans(spans: Iterable[Tuple[int, int]]) -> List[Tuple[int, int]]:
    items = sorted(spans)
    merged: List[Tuple[int, int]] = []
    for s, e in items:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _overlaps(span: Tuple[int, int], spans: Sequence[Tuple[int, int]]) -> bool:
    s, e = span
    for ts, te in spans:
        if s < te and ts < e:
            return True
    return False


def is_config_path(path: Path) -> bool:
    """判断某个文件是否是「配置文件」（默认豁免）。"""
    if path.name in CONFIG_FILE_NAMES:
        return True
    return any(part in CONFIG_DIR_NAMES for part in path.parts)


def load_profile(path: Any) -> Dict[str, Any]:
    """读取机构差异配置（单点化）。"""
    p = Path(path)
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("机构差异配置格式错误（应为映射）：%s" % p)
    data.setdefault("profile_id", p.stem)
    return data


def _protected_spans(text: str) -> List[Tuple[int, int]]:
    """受保护的标准编号区间（R-D2 / R-D3）。区间内的年份不算残留真实日期。"""
    spans: List[Tuple[int, int]] = []
    for rx in (ai.PROTECT_RE, ai.PROTECT_RE_R3_COLON, ai.PROTECT_RE_R3_DASH):
        for m in rx.finditer(text):
            spans.append((m.start(), m.end()))
    return _merge_spans(spans)


def _placeholder_spans(text: str) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    for rx in (ai.FULLWIDTH_PLACEHOLDER_RE, ai.HALFWIDTH_PLACEHOLDER_RE):
        for m in rx.finditer(text):
            spans.append((m.start(), m.end()))
    return _merge_spans(spans)


# --------------------------------------------------------------------------
# 扫描器
# --------------------------------------------------------------------------


class Scanner:
    """模板缺陷扫描器。"""

    def __init__(
        self,
        *,
        profile: Any = None,
        registry: Any = None,
        include_config: bool = False,
        root: Any = None,
    ) -> None:
        self.root = Path(root) if root is not None else REPO_ROOT
        self.profile_path = Path(profile) if profile is not None else DEFAULT_PROFILE_PATH
        self.registry_path = Path(registry) if registry is not None else DEFAULT_REGISTRY_PATH
        self.profile = load_profile(self.profile_path)
        self.registry = ai.load_registry(self.registry_path)
        self.include_config = include_config

        # 机构表单号形态：登记表 entries + 机构差异配置 form_number.pattern
        self._form_patterns: List[Tuple[str, Any]] = []
        for entry in self.registry.entries:
            pattern = entry.get("pattern")
            if not pattern:
                continue
            try:
                rx = re.compile(pattern)
            except re.error:
                continue
            self._form_patterns.append(
                ("登记表条目 " + str(entry.get("id", "?")), rx)
            )
        form_cfg = self.profile.get("form_number") or {}
        pattern = form_cfg.get("pattern")
        if pattern:
            try:
                self._form_patterns.append(
                    ("机构差异配置 form_number.pattern", re.compile(pattern))
                )
            except re.error:
                pass
        self._form_prefix = form_cfg.get("prefix")

        # 机构名称字面
        names: List[str] = []
        agency = self.profile.get("agency_name")
        if agency:
            names.append(str(agency))
        for alias in self.profile.get("agency_aliases") or []:
            if alias:
                names.append(str(alias))
        # 长字面优先，避免「示例认证」与「示例认证机构」重复命中
        self._agency_literals: List[str] = sorted(set(names), key=lambda s: (-len(s), s))

        # 机构内部时限口径字面
        self._deadline_literals: List[str] = [
            str(x) for x in (self.profile.get("internal_deadlines") or []) if x
        ]

        # 已知占位符键（半角，去掉花括号）
        self._known_keys = {k.strip("{}") for k in ai.KNOWN_PLACEHOLDERS}

    # -- 路径 --------------------------------------------------------------

    def rel(self, path: Path) -> str:
        try:
            return Path(path).resolve().relative_to(Path(self.root).resolve()).as_posix()
        except ValueError:
            return Path(path).as_posix()

    # -- 主流程 ------------------------------------------------------------

    def collect_files(self, paths: Iterable[Any]) -> List[Path]:
        out: List[Path] = []
        for raw in paths:
            p = Path(raw)
            if p.is_dir():
                for f in sorted(p.rglob("*")):
                    if f.is_file() and f.suffix.lower() in SCAN_SUFFIXES:
                        out.append(f)
            elif p.is_file():
                out.append(p)
        return out

    def scan_paths(self, paths: Iterable[Any]) -> ScanResult:
        files = self.collect_files(paths)
        result = ScanResult(
            roots=[str(p) for p in paths],
            include_config=self.include_config,
            generated_at=datetime.now().isoformat(timespec="seconds"),
        )
        result.profile = {
            "path": self.rel(self.profile_path),
            "profile_id": self.profile.get("profile_id"),
            "agency_name": self.profile.get("agency_name"),
        }
        result.registry = {
            "path": self.rel(self.registry_path),
            "entries": [e.get("id") for e in self.registry.entries],
        }
        for f in files:
            config = is_config_path(f)
            if config and not self.include_config:
                result.files_exempt.append(self.rel(f))
                continue
            result.files_scanned.append(self.rel(f))
            findings = self.scan_text(f.read_text(encoding="utf-8"), f)
            if config:
                for item in findings:
                    item.category = CATEGORY_EXEMPT_CONFIG
                    item.severity = SEVERITY_INFO
                    item.reason = "配置文件豁免：" + item.reason
            result.findings.extend(findings)
        result.findings.sort(
            key=lambda x: (x.path, x.line, x.column, x.category)
        )
        return result

    # -- 单文件扫描 --------------------------------------------------------

    def scan_text(self, text: str, path: Path) -> List[Finding]:
        findings: List[Finding] = []
        line_starts = _line_starts(text)
        seen: set = set()

        def add(category: str, start: int, end: int, reason: str) -> None:
            key = (category, start, end)
            if key in seen:
                return
            seen.add(key)
            line, col = _line_col(line_starts, start)
            findings.append(
                Finding(
                    category=category,
                    path=self.rel(path),
                    line=line,
                    column=col,
                    match=text[start:end].strip().replace("\n", "\\n")[:120],
                    reason=reason,
                )
            )

        protected = _protected_spans(text)
        placeholders = _placeholder_spans(text)

        # 1) 机构表单号
        for m in FORM_TOKEN_RE.finditer(text):
            token = m.group(0)
            span = (m.start(), m.end())
            if _overlaps(span, placeholders):
                continue
            for label, rx in self._form_patterns:
                if rx.fullmatch(token):
                    add(
                        CATEGORY_AGENCY_FORM_NUMBER,
                        m.start(),
                        m.end(),
                        "命中%s（%s）：机构表单号属机构私有信息，模板内只能出现占位符形态"
                        % (label, getattr(rx, "pattern", "")),
                    )
                    break
            else:
                prefix = str(self._form_prefix or "")
                if prefix and token.startswith(prefix):
                    add(
                        CATEGORY_AGENCY_FORM_NUMBER,
                        m.start(),
                        m.end(),
                        "命中机构差异配置的表单号前缀 %s：机构表单号只能以占位符形态出现"
                        % prefix,
                    )

        # 2) 机构名称硬编码
        for start, end, literal in self._agency_matches(text):
            if _overlaps((start, end), placeholders):
                continue
            add(
                CATEGORY_AGENCY_NAME,
                start,
                end,
                "命中机构差异配置的机构名/别名「%s」：机构名称属机构私有信息，"
                "模板必须保持机构中立（以 {{认证机构名称}} 承载）" % literal,
            )

        # 3) 机构内部时限口径
        deadline_spans: List[Tuple[int, int]] = []
        for literal in self._deadline_literals:
            start = 0
            while True:
                i = text.find(literal, start)
                if i < 0:
                    break
                deadline_spans.append((i, i + len(literal)))
                add(
                    CATEGORY_INTERNAL_DEADLINE,
                    i,
                    i + len(literal),
                    "命中机构差异配置 internal_deadlines 中的内部时限口径「%s」："
                    "机构内部考核口径一律占位化，不得写入成果" % literal,
                )
                start = i + 1
        for m in ai.RELATIVE_TIME_RE.finditer(text):
            span = (m.start(), m.end())
            if _overlaps(span, deadline_spans):
                continue
            add(
                CATEGORY_INTERNAL_DEADLINE,
                m.start(),
                m.end(),
                "命中相对时限口径（RELATIVE_TIME_RE）：相对时限属机构内部口径，"
                "应走 #21 计算或占位化，不得直接出现在成果里",
            )

        # 4) 残留真实日期
        date_spans: List[Tuple[int, int]] = []
        for rx in ai.FULL_DATE_RES:
            for m in rx.finditer(text):
                span = (m.start(), m.end())
                if _overlaps(span, protected):
                    continue
                date_spans.append(span)
                add(
                    CATEGORY_RESIDUAL_REAL_DATE,
                    m.start(),
                    m.end(),
                    "残留完整日期：模板不得写具体日期，日期类字段一律占位化",
                )
        for m in ai.YEAR_RE.finditer(text):
            span = (m.start(), m.end())
            if _overlaps(span, protected):
                continue
            if _overlaps(span, date_spans):
                continue
            add(
                CATEGORY_RESIDUAL_REAL_DATE,
                m.start(),
                m.end(),
                "残留四位年份（R-D1）：未被 R-D2/R-D3 标准号保护的年份应占位化",
            )

        # 5) 清洗误伤：标准号被占位符切断
        for m in ai.HALFWIDTH_PLACEHOLDER_RE.finditer(text):
            start, end = m.start(), m.end()
            before = text[max(0, start - 24):start]
            after = text[end:end + 24]
            left_digits = re.search(r"(\d{1,6})$", before)
            right_digits = re.match(r"^(\d{1,6})", after)
            prefix_left = STD_PREFIX_TAIL_RE.search(before)
            prefix_right = STD_PREFIX_HEAD_RE.match(after)
            key = m.group(0)[2:-2].strip()
            if prefix_left and (left_digits or right_digits):
                add(
                    CATEGORY_CLEANING_DAMAGE,
                    start,
                    end,
                    "标准号前缀「%s」与占位符 %s 粘连/被切断：清洗把标准号内部的数字"
                    "替换成了占位符（如 ISO 90{{时间}}15），属 §9.3 不变式被破坏"
                    % (prefix_left.group(0).strip(), m.group(0)),
                )
            elif prefix_left:
                add(
                    CATEGORY_CLEANING_DAMAGE,
                    start,
                    end,
                    "标准号前缀「%s」与占位符 %s 直接相邻：标准号被占位符切断"
                    % (prefix_left.group(0).strip(), m.group(0)),
                )
            elif prefix_right and right_digits:
                add(
                    CATEGORY_CLEANING_DAMAGE,
                    start,
                    end,
                    "占位符 %s 之后紧跟数字并连向标准号前缀：标准号被占位符切断"
                    % m.group(0),
                )
            elif left_digits and right_digits:
                merged = left_digits.group(1) + right_digits.group(1)
                if len(merged) >= 4 or key in DATE_LIKE_KEYS:
                    add(
                        CATEGORY_CLEANING_DAMAGE,
                        start,
                        end,
                        "数字被占位符 %s 夹断（还原后为 %s）：疑似标准号或年份被误伤"
                        % (m.group(0), merged),
                    )

        # 6) 残留占位符
        for m in ai.FULLWIDTH_PLACEHOLDER_RE.finditer(text):
            add(
                CATEGORY_RESIDUAL_PLACEHOLDER,
                m.start(),
                m.end(),
                "全角双花括号占位符：§9.2 要求全角形态残留数为 0",
            )
        for m in ai.HALFWIDTH_PLACEHOLDER_RE.finditer(text):
            key = m.group(0)
            if key.strip("{}") in self._known_keys:
                continue
            add(
                CATEGORY_RESIDUAL_PLACEHOLDER,
                m.start(),
                m.end(),
                "字典外半角占位符：不在 §9.1 字典（30 条）与模板层扩展（4+17 条）之内",
            )
        for m in ai.BARE_BRACE_RE.finditer(text):
            add(
                CATEGORY_RESIDUAL_PLACEHOLDER,
                m.start(),
                m.end(),
                "裸花括号：§9.2 要求裸花括号残留数为 0",
            )

        return findings

    # -- 机构名匹配（长字面优先、互不重叠） --------------------------------

    def _agency_matches(self, text: str) -> List[Tuple[int, int, str]]:
        raw: List[Tuple[int, int, str]] = []
        for literal in self._agency_literals:
            if not literal:
                continue
            start = 0
            while True:
                i = text.find(literal, start)
                if i < 0:
                    break
                raw.append((i, i + len(literal), literal))
                start = i + 1
        raw.sort(key=lambda x: (x[0], -(x[1] - x[0])))
        kept: List[Tuple[int, int, str]] = []
        for item in raw:
            if any(not (item[1] <= k[0] or item[0] >= k[1]) for k in kept):
                continue
            kept.append(item)
        return kept


# --------------------------------------------------------------------------
# 机构差异配置验证（验收标准 3）
# --------------------------------------------------------------------------


def verify_profiles(
    targets: Sequence[Any],
    profiles_dir: Any,
    registry: Any = None,
) -> List[Dict[str, Any]]:
    """对 profiles_dir 下每份机构差异配置分别扫描 targets。

    返回每份配置的统计：机构名称残留 / 机构表单号残留必须为 0。
    """
    rows: List[Dict[str, Any]] = []
    d = Path(profiles_dir)
    profiles = sorted(d.glob("*.yaml")) + sorted(d.glob("*.yml"))
    for prof in profiles:
        scanner = Scanner(profile=prof, registry=registry)
        result = scanner.scan_paths(targets)
        rows.append(
            {
                "profile": prof.name,
                "profile_id": scanner.profile.get("profile_id"),
                "agency_name": scanner.profile.get("agency_name"),
                "form_number_prefix": (scanner.profile.get("form_number") or {}).get("prefix"),
                "files_scanned": len(result.files_scanned),
                "defect_total": result.defect_total(),
                "agency_name_residuals": result.by_category()[CATEGORY_AGENCY_NAME],
                "agency_form_number_residuals": result.by_category()[CATEGORY_AGENCY_FORM_NUMBER],
                "counts": result.by_category(),
            }
        )
    return rows


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------


def render_markdown(result: ScanResult) -> str:
    """生成 Markdown 缺陷清单。"""
    counts = result.by_category()
    lines: List[str] = []
    lines.append("# 模板缺陷扫描报告（tools/scan_templates.py）")
    lines.append("")
    lines.append("- 生成时间：%s" % (result.generated_at or datetime.now().isoformat(timespec="seconds")))
    lines.append("- 扫描目标：%s" % ("、".join(result.roots) if result.roots else "（未指定）"))
    lines.append(
        "- 机构差异配置：%s（%s / %s）"
        % (
            result.profile.get("path"),
            result.profile.get("profile_id"),
            result.profile.get("agency_name"),
        )
    )
    lines.append(
        "- 表单号登记表：%s（条目：%s）"
        % (result.registry.get("path"), "、".join(str(x) for x in result.registry.get("entries") or []))
    )
    lines.append("- 配置文件豁免：%s" % ("开启（--include-config）" if result.include_config else "默认豁免"))
    lines.append(
        "- 扫描文件数：%d；缺陷合计：%d；豁免命中：%d"
        % (len(result.files_scanned), result.defect_total(), result.exempt_total())
    )
    lines.append("")
    lines.append("## 分类统计")
    lines.append("")
    lines.append("| 类别 | 说明 | 数量 |")
    lines.append("| --- | --- | --- |")
    for cat in DEFECT_CATEGORIES:
        lines.append("| %s | %s | %d |" % (cat, CATEGORY_DESCRIPTIONS[cat], counts[cat]))
    lines.append("| **合计** | 缺陷 | **%d** |" % result.defect_total())
    lines.append("")
    lines.append("## 缺陷明细")
    lines.append("")
    defects = [f for f in result.findings if f.severity == SEVERITY_DEFECT]
    if not defects:
        lines.append("（无缺陷）")
    else:
        lines.append("| 文件 | 行 | 列 | 类别 | 命中文本 | 判定理由 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for f in defects:
            lines.append(
                "| %s | %d | %d | %s | %s | %s |"
                % (
                    f.path,
                    f.line,
                    f.column,
                    f.category,
                    f.match.replace("|", "\\|"),
                    f.reason.replace("|", "\\|"),
                )
            )
    if result.exempt_total():
        lines.append("")
        lines.append("## 豁免命中（配置类，不计缺陷）")
        lines.append("")
        lines.append("| 文件 | 行 | 类别 | 命中文本 | 说明 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for f in result.findings:
            if f.severity != SEVERITY_INFO:
                continue
            lines.append(
                "| %s | %d | %s | %s | %s |"
                % (
                    f.path,
                    f.line,
                    f.category,
                    f.match.replace("|", "\\|"),
                    f.reason.replace("|", "\\|"),
                )
            )
    lines.append("")
    lines.append("## 扫描文件清单")
    lines.append("")
    for p in result.files_scanned:
        lines.append("- %s" % p)
    if result.files_exempt:
        lines.append("")
        lines.append("## 豁免文件清单（配置文件，默认不扫）")
        lines.append("")
        for p in result.files_exempt:
            lines.append("- %s" % p)
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------


def selftest() -> int:
    """扫描器自检：标准号不被日期替换误伤 + 故意构造的破坏形态被判 cleaning_damage。"""
    failures: List[str] = []

    standard_text = "依据 GB/T 19001-2016 与 ISO 9001:2015 开展审核，CNCA-QMS-01:2025 适用。"
    before = ai.protected_tokens(standard_text)
    try:
        replaced, _tokens = ai.replace_relative_time(standard_text)
    except ValueError as exc:  # 不变式被破坏
        failures.append("标准号被日期替换误伤：%s" % exc)
        replaced = standard_text
    after = ai.protected_tokens(replaced)
    if before != after:
        failures.append("保护 token 序列前后不等：before=%r after=%r" % (before, after))
    if replaced != standard_text:
        failures.append("标准号文本被改写：%r" % replaced)

    scanner = Scanner(profile=DEFAULT_PROFILE_PATH, registry=DEFAULT_REGISTRY_PATH)

    damaged = "ISO 90{{时间}}15 与 GB/T 19001-2016"
    findings = scanner.scan_text(damaged, Path("selftest.yaml"))
    cats = [f.category for f in findings]
    if CATEGORY_CLEANING_DAMAGE not in cats:
        failures.append("故意构造的 ISO 90{{时间}}15 未被判为 cleaning_damage（实际类别：%r）" % cats)

    clean = scanner.scan_text(standard_text, Path("selftest.yaml"))
    clean_cats = sorted({f.category for f in clean})
    for cat in (CATEGORY_CLEANING_DAMAGE, CATEGORY_RESIDUAL_REAL_DATE):
        if cat in clean_cats:
            failures.append("标准号文本被误报为 %s" % cat)

    if failures:
        for f in failures:
            print("[FAIL] " + f)
        return 1
    print("[OK] selftest 通过：标准号不变式成立，ISO 90{{时间}}15 被判为 cleaning_damage")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scan_templates",
        description="模板缺陷扫描器（GitHub Issue #19）：机构私有信息残留与清洗误伤",
    )
    p.add_argument("--dir", action="append", default=None, metavar="DIR",
                   help="要扫描的目录（可重复；递归扫 .yaml/.yml/.md/.txt/.json）")
    p.add_argument("--file", action="append", default=None, metavar="FILE",
                   help="要扫描的单个文件（可重复）")
    p.add_argument("--profile", default=str(DEFAULT_PROFILE_PATH), metavar="YAML",
                   help="机构差异配置（默认 %s）" % DEFAULT_PROFILE_PATH)
    p.add_argument("--registry", default=str(DEFAULT_REGISTRY_PATH), metavar="YAML",
                   help="表单号登记表（默认 %s）" % DEFAULT_REGISTRY_PATH)
    p.add_argument("--include-config", action="store_true",
                   help="连配置文件一起扫（命中归类为 exempt_config，不计缺陷）")
    p.add_argument("--json", dest="json_path", default=None, metavar="PATH",
                   help="JSON 结果落盘路径")
    p.add_argument("--out", dest="md_path", default=None, metavar="PATH",
                   help="Markdown 缺陷清单落盘路径")
    p.add_argument("--fail-on", default="any", metavar="SPEC",
                   help="失败条件：any（默认，有缺陷即退出码 2）/ none（放宽为 0）/ 具体类别名")
    p.add_argument("--verify-profiles", default=None, metavar="DIR",
                   help="对某目录下的每份机构差异配置分别扫描目标目录并汇总")
    p.add_argument("--selftest", action="store_true", help="只跑自检")
    p.add_argument("--quiet", action="store_true", help="控制台只打印汇总")
    return p


def _print_summary(result: ScanResult) -> None:
    counts = result.by_category()
    print("扫描目标：%s" % ("、".join(result.roots) if result.roots else "（未指定）"))
    print("机构差异配置：%s（%s）" % (result.profile.get("path"), result.profile.get("agency_name")))
    print("扫描文件数：%d（豁免 %d）" % (len(result.files_scanned), len(result.files_exempt)))
    print("分类计数：")
    for cat in DEFECT_CATEGORIES:
        print("  %-24s %4d  %s" % (cat, counts[cat], CATEGORY_DESCRIPTIONS[cat]))
    print("  %-24s %4d  合计缺陷" % ("TOTAL", result.defect_total()))
    if result.exempt_total():
        print("  %-24s %4d  豁免命中（不计缺陷）" % ("exempt_config", result.exempt_total()))
    if not result.findings:
        print("未发现缺陷。")
        return
    print("明细：")
    for f in result.findings:
        print("  %s:%d:%d [%s] %s —— %s" % (f.path, f.line, f.column, f.category, f.match, f.reason))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.selftest:
        return selftest()

    targets: List[str] = list(args.dir or [])
    targets.extend(args.file or [])
    if not targets:
        targets = [str(DEFAULT_TARGET_DIR)]

    try:
        scanner = Scanner(
            profile=args.profile,
            registry=args.registry,
            include_config=args.include_config,
        )
    except Exception as exc:  # noqa: BLE001
        print("读取配置失败：%s" % exc, file=sys.stderr)
        return 1

    if args.verify_profiles:
        rows = verify_profiles(targets, args.verify_profiles, args.registry)
        print("机构差异配置切换验证（目标：%s）" % "、".join(targets))
        print("%-24s %-16s %-12s %8s %8s %8s" % ("配置", "profile_id", "表单号前缀", "机构名残留", "表单号残留", "缺陷合计"))
        bad = 0
        for row in rows:
            print(
                "%-24s %-16s %-12s %8d %8d %8d"
                % (
                    row["profile"],
                    row["profile_id"],
                    row["form_number_prefix"],
                    row["agency_name_residuals"],
                    row["agency_form_number_residuals"],
                    row["defect_total"],
                )
            )
            bad += row["agency_name_residuals"] + row["agency_form_number_residuals"]
        if bad:
            print("验证失败：存在机构名/机构表单号残留 %d 处" % bad)
            return 2
        print("验证通过：各配置下机构名称与机构表单号残留均为 0")
        return 0

    try:
        result = scanner.scan_paths(targets)
    except Exception as exc:  # noqa: BLE001
        print("扫描失败：%s" % exc, file=sys.stderr)
        return 1

    if args.json_path:
        Path(args.json_path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_path).write_text(result.to_json() + "\n", encoding="utf-8")
        if not args.quiet:
            print("JSON 已写入：%s" % args.json_path)
    if args.md_path:
        Path(args.md_path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md_path).write_text(render_markdown(result), encoding="utf-8")
        if not args.quiet:
            print("Markdown 缺陷清单已写入：%s" % args.md_path)

    if not args.quiet:
        _print_summary(result)
    else:
        counts = result.by_category()
        print("分类计数：" + "，".join("%s=%d" % (c, counts[c]) for c in DEFECT_CATEGORIES)
              + "，合计=%d" % result.defect_total())

    fail_on = (args.fail_on or "any").strip()
    if fail_on == "none":
        return 0
    if fail_on == "any":
        return 2 if result.defect_total() else 0
    if fail_on in DEFECT_CATEGORIES:
        return 2 if result.by_category().get(fail_on) else 0
    print("未知的 --fail-on 取值：%s" % fail_on, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
