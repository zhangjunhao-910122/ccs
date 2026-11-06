"""机构成果模板的清洗、占位化与表单号隔离（GitHub Issue #19）。

三个动作**互相独立、可单独调用**：

* `sanitize_template(template, ...)`   —— 清洗：把机构专有信息抽成占位符；
  返回「清洗后模板 + 抽出的机构标识清单 + 可逆映射表 + 清洗日志」。
* `templatize_template(template, ...)` —— 占位化：把机构模板转成机构中立成果模板
  （机构标识换占位符、日期占位符不误伤标准号、字段名中立化）。
* `isolate_form_numbers(text, ...)`     —— 表单号隔离：按
  `tools/templates/form_number_registry.yaml` 判定文本里的编号；
  命中已登记的机构表单号即报冲突并阻断，中性编号与公开标准号放行。

口径来源（全部照抄，不自造）：

* `docs/spec/output-formats.md` §9.1（占位符字典 30 条）、§9.2（残留校验）、
  §9.3（日期类替换排除规则 R-D1～R-D8）、§9.4（机构私有信息隔离）、
  §11（机构表单族 12 类）、§11.2（占位化与清洗要求）。
* `docs/spec/verdict-anchor-rules.md` §0（三条红线）、§11（审核报告 19 项）、§12（人工闸口）。

铁律：

1. 原件一个字节都不动；替换只在复制出的副本上做（§2.3 硬规则①、§11.2①）。
2. 机构表单号体系属机构私有信息：仓库里只登记「占位符形态的编号模板」与出处说明，
   不复制真实表单号原文（§9.4、§11.1）。
3. 机构内部考核口径（如「多少天内交整改资料」）不是法定期限，
   一律占位化，不得写进成果（§9.4、§9.3 R-D7）。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

__all__ = [
    "PLACEHOLDER_DICT",
    "AGENCY_DIFF_PLACEHOLDERS",
    "TEMPLATE_FIELD_PLACEHOLDERS",
    "TEMPLATE_EXTRA_PLACEHOLDERS",
    "KNOWN_PLACEHOLDERS",
    "ARTIFACT_FORMS",
    "ARTIFACT_BASES",
    "AGENCY_FAMILY_IDS",
    "FormNumberConflictError",
    "FormNumberVerdict",
    "IsolationResult",
    "SanitizeResult",
    "TemplatizeResult",
    "FormNumberRegistry",
    "find_residual_placeholders",
    "count_bare_braces",
    "load_registry",
    "load_family_template",
    "load_family_templates",
    "is_known_form_number",
    "check_form_number",
    "sanitize_template",
    "restore_sanitized",
    "templatize_template",
    "render_template",
    "isolate_form_numbers",
    "replace_relative_time",
    "protected_tokens",
    "residual_counts",
    "residual_report",
]

# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------

TOOLS_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = TOOLS_DIR / "templates"
FAMILIES_DIR = TEMPLATES_DIR / "families"
REGISTRY_PATH = TEMPLATES_DIR / "form_number_registry.yaml"

# --------------------------------------------------------------------------
# §9.1 占位符字典（唯一来源，30 条，逐字照抄 output-formats.md §9.1）
# --------------------------------------------------------------------------

PLACEHOLDER_DICT: Dict[str, str] = {
    "{{任务标识}}": "审核任务唯一标识",
    "{{节点编号}}": "流程节点编号",
    "{{认证机构名称}}": "认证机构全称",
    "{{受审核方名称}}": "委托人全称",
    "{{受审核方地址}}": "委托人地址",
    "{{受审核方统一社会信用代码}}": "统一社会信用代码",
    "{{认证范围}}": "申请／证书范围描述",
    "{{专业代码}}": "认证业务范围专业代码",
    "{{合同编号}}": "认证合同编号",
    "{{审核类型}}": "初次第一阶段／第二阶段／监督／再认证／特殊",
    "{{审核准则}}": "标准号 + 版本 + 规则号 + 版本",
    "{{审核目的}}": "本次审核目的",
    "{{审核时间}}": "本次审核时间（引用 #21 计算结果）",
    "{{审核组长姓名}}": "审核组长姓名",
    "{{审核组长职务}}": "审核组长职务／资格",
    "{{审核员姓名}}": "审核员姓名（多人逗号分隔）",
    "{{审核职务}}": "审核员职务／专业领域",
    "{{同行人员姓名}}": "同行人员姓名",
    "{{委托方代表姓名}}": "受审核方授权代表姓名",
    "{{部门}}": "受审核部门名称",
    "{{场所}}": "受审核场所名称",
    "{{日期}}": "文档出具／签署日期",
    "{{起止日期}}": "审核起止日期",
    "{{时间}}": "具体时刻",
    "{{联系人姓名}}": "受审核方联络人",
    "{{签字人姓名}}": "签字人姓名",
    "{{签字人职务}}": "签字人职务",
    "{{签字日期}}": "签字日期",
    "{{版本号}}": "成果版本号",
    "{{成果状态}}": "成果态（waiting/review/final/terminated）",
}

# 机构差异占位符（模板层扩展）：只允许出现在模板集与清洗中间产物里，
# 渲染进交付物前必须全部填掉，否则违反 §9.2「双花括号残留数 = 0」。
AGENCY_DIFF_PLACEHOLDERS: Dict[str, str] = {
    "{{表单编号}}": "机构表单编号（机构差异占位符）",
    "{{表单名称}}": "机构表单名称（机构差异占位符）",
    "{{机构内部编号}}": "机构内部文件编号（机构差异占位符）",
    "{{机构标识}}": "机构标识（徽标／图形）引用（机构差异占位符）",
}

# 模板层字段占位符：机构表单族模板里的**结构性字段**（审核点、条款、判定、不符合项…）。
# 不属于 §9.1 的 30 条，但同属模板词表：模板层不算残留，渲染进交付物前必须填掉。
TEMPLATE_FIELD_PLACEHOLDERS: Dict[str, str] = {
    "{{审核点编号}}": "审核点唯一编号（点键）",
    "{{条款号}}": "条款号",
    "{{条款标题}}": "条款标题",
    "{{条款引用}}": "条款原文引用出处",
    "{{审核问题}}": "审核问题",
    "{{判定结论}}": "条款判定结论（conform/nonconform/insufficient/na）",
    "{{判定依据}}": "判定依据（逐句可追到 confirmed 锚点）",
    "{{证据锚点}}": "证据锚点引用（如 [A-0007]）",
    "{{评审人}}": "评审／复核人",
    "{{确认状态}}": "确认状态（pending/confirmed/rejected）",
    "{{不符合编号}}": "不符合项编号",
    "{{不符合类别}}": "不符合类别（工具产出时留空，由人填）",
    "{{事实陈述}}": "不符合事实陈述",
    "{{整改要求}}": "整改要求",
    "{{完成时限}}": "整改完成时限（引用 #21 计算结果）",
    "{{不符合状态}}": "不符合项状态",
    "{{缺口说明}}": "未解决事项／缺口说明",
}

#: 模板层全部扩展占位符（机构差异 + 模板字段）
TEMPLATE_EXTRA_PLACEHOLDERS: Dict[str, str] = {
    **AGENCY_DIFF_PLACEHOLDERS,
    **TEMPLATE_FIELD_PLACEHOLDERS,
}

KNOWN_PLACEHOLDERS = frozenset(PLACEHOLDER_DICT) | frozenset(TEMPLATE_EXTRA_PLACEHOLDERS)

# --------------------------------------------------------------------------
# §3 / §2.1 产物形态（用于模板与成果的对应关系校验）
# --------------------------------------------------------------------------

#: §3 四类产物形态（格式）
ARTIFACT_FORMS = ("xlsx", "docx", "json", "md")

#: §2.1 五件产物基名
ARTIFACT_BASES = ("审查表", "条款-证据对照表", "审核报告", "过程底稿", "预览")

#: §11.1 机构表单族 12 类（顺序即 §11.1 表序）
AGENCY_FAMILY_IDS = tuple("F%02d" % i for i in range(1, 13))

# --------------------------------------------------------------------------
# §9.3 日期类替换的排除规则
# --------------------------------------------------------------------------

#: R-D2 标准编号
PROTECT_RE = re.compile(
    r"(?<![0-9A-Za-z])(?:ISO(?:/IEC)?|GB/T|GB|CNCA(?:-[A-Z]{2,6})?|CNAS(?:-[A-Z]{2,6})?|IAF|JF)"
    r"\s*[-–]?\s*\d+(?:[.\-:/]\d+)*"
)

#: R-D3 版本号后缀（前半：xxx:2025 / xxx：2025，带前缀数字一并保护）
PROTECT_RE_R3_COLON = re.compile(r"(?<![0-9A-Za-z])[\w.\-/]*\d+\s*[:：]\s*(?:19|20)\d{2}")

#: R-D3 版本号后缀（后半：xxx-2025）
PROTECT_RE_R3_DASH = re.compile(r"(?<![0-9A-Za-z])(?:19|20)\d{2}\s*[-–]\s*(?:19|20)\d{2}")

#: R-D1 年份匹配单元必须四位（禁止 \\d{2} / \\d{1,2}）
YEAR_RE = re.compile(r"(?<![0-9A-Za-z])(?:19|20)\d{2}(?![0-9])")

#: 完整日期（先后后斜杠、年月日）
FULL_DATE_RES = (
    re.compile(r"(?<![0-9A-Za-z])(?:19|20)\d{2}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}\s*日?"),
    re.compile(r"(?<![0-9A-Za-z])(?:19|20)\d{2}\s*年\s*\d{1,2}\s*月(?![0-9])"),
)

#: 相对时限（机构内部口径 / 合同口径）：整体占位化，不进入成果
RELATIVE_TIME_RE = re.compile(
    r"(?<![0-9A-Za-z])\d+(?:\.\d+)?\s*"
    r"(?:个?日历日|个工作日|个月|周|天|日)"
    r"(?:之内|以内|以内之内|内|之后|以内期|起)?"
)

#: 候选编号 token（带字母前缀 + 分隔符），用于表单号扫描
FORM_NUMBER_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-–/][A-Za-z0-9]+)+")

#: 全角双花括号
FULLWIDTH_PLACEHOLDER_RE = re.compile(r"｛｛[^{}]*｝｝")

#: 半角双花括号
HALFWIDTH_PLACEHOLDER_RE = re.compile(r"\{\{[^{}]*\}\}")

#: 裸花括号（单个 { 或 }，不属于双花括号）
BARE_BRACE_RE = re.compile(r"(?<!\{)\{(?!\{)|(?<!\})\}(?!\})")


# --------------------------------------------------------------------------
# 错误与结果类型
# --------------------------------------------------------------------------


class FormNumberConflictError(RuntimeError):
    """命中已登记的机构表单号：必须阻断，不得写出产物。"""

    def __init__(self, message: str, conflicts: Optional[Sequence["FormNumberVerdict"]] = None):
        super().__init__(message)
        self.conflicts = list(conflicts or [])


@dataclass
class FormNumberVerdict:
    """一次编号判定的结果。"""

    status: str  # conflict | neutral | public_standard | unregistered
    text: str
    matched: Optional[str] = None
    entry_id: Optional[str] = None
    agency: Optional[str] = None
    form_number_template: Optional[str] = None
    reason: str = ""

    @property
    def is_conflict(self) -> bool:
        return self.status == "conflict"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "text": self.text,
            "matched": self.matched,
            "entry_id": self.entry_id,
            "agency": self.agency,
            "form_number_template": self.form_number_template,
            "reason": self.reason,
        }


@dataclass
class IsolationResult:
    """表单号隔离的判定汇总。"""

    text: str
    verdicts: List[FormNumberVerdict] = field(default_factory=list)
    conflicts: List[FormNumberVerdict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.conflicts

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "verdicts": [v.to_dict() for v in self.verdicts],
        }


@dataclass
class SanitizeResult:
    """清洗结果：清洗后模板 + 机构标识清单 + 可逆映射 + 清洗日志。"""

    template: Any
    markers: List[Dict[str, Any]] = field(default_factory=list)
    mapping: Dict[str, str] = field(default_factory=dict)
    log: Dict[str, Any] = field(default_factory=dict)
    #: 精确编辑清单（可逆性依据）：path + 新文本中的 start/end 坐标 + 原值 + 占位符
    edits: List[Dict[str, Any]] = field(default_factory=list)
    #: 字段名中立化清单：path + 原字段名 + 中立字段名
    label_edits: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def categories(self) -> List[str]:
        seen: List[str] = []
        for m in self.markers:
            if m["category"] not in seen:
                seen.append(m["category"])
        return seen

    def to_dict(self) -> Dict[str, Any]:
        return {
            "markers": copy.deepcopy(self.markers),
            "mapping": dict(self.mapping),
            "log": copy.deepcopy(self.log),
        }


@dataclass
class TemplatizeResult:
    """占位化结果：机构中立模板 + 中立化字段名 + 保护 token + 残留检查。"""

    template: Any
    neutral_labels: Dict[str, str] = field(default_factory=dict)
    protected: List[str] = field(default_factory=list)
    residuals: List[Dict[str, Any]] = field(default_factory=list)
    log: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "neutral_labels": dict(self.neutral_labels),
            "protected": list(self.protected),
            "residuals": copy.deepcopy(self.residuals),
            "log": copy.deepcopy(self.log),
        }


# --------------------------------------------------------------------------
# §9.2 残留校验
# --------------------------------------------------------------------------


def find_residual_placeholders(text: str, *, strict: bool = True) -> List[Dict[str, Any]]:
    """列出残留占位符。

    参数
    ----
    strict
        * `True`（§9.2 交付物口径）：任何 `{{...}}`（半角）与 `｛｛...｝｝`（全角）都算残留。
        * `False`（模板口径）：只有**全角**形态、或**字典外**的半角键才算残留；
          字典内的合法占位符不算残留。
    """
    residuals: List[Dict[str, Any]] = []
    for m in FULLWIDTH_PLACEHOLDER_RE.finditer(text):
        residuals.append(
            {"kind": "fullwidth", "token": m.group(0), "start": m.start(), "end": m.end()}
        )
    for m in HALFWIDTH_PLACEHOLDER_RE.finditer(text):
        key = m.group(0)
        if strict or key not in KNOWN_PLACEHOLDERS:
            residuals.append(
                {"kind": "halfwidth", "token": key, "start": m.start(), "end": m.end()}
            )
    return residuals


def count_bare_braces(text: str) -> int:
    """统计裸花括号（单个 { 或 }）数量。§9.2 要求裸花括号也必须清零。"""
    return len(BARE_BRACE_RE.findall(text))


# --------------------------------------------------------------------------
# §9.3 保护 token 与日期/相对时间替换
# --------------------------------------------------------------------------


def protected_tokens(text: str) -> List[str]:
    """返回文本中受保护的标准编号 token 序列（R-D2 + R-D3）。"""
    tokens = list(PROTECT_RE.findall(text))
    masked, _ = _mask_protected(text)
    tokens.extend(PROTECT_RE_R3_COLON.findall(masked))
    tokens.extend(PROTECT_RE_R3_DASH.findall(masked))
    return tokens


def _mask_protected(text: str) -> Tuple[str, List[str]]:
    """把 R-D2 / R-D3 保护对象换成哨兵，返回 (掩码文本, token 列表)。"""
    tokens: List[str] = []

    def _sub(match: "re.Match[str]") -> str:
        tokens.append(match.group(0))
        return "\x00P%d\x00" % (len(tokens) - 1)

    masked = PROTECT_RE.sub(_sub, text)
    masked = PROTECT_RE_R3_COLON.sub(_sub, masked)
    masked = PROTECT_RE_R3_DASH.sub(_sub, masked)
    return masked, tokens


def _unmask(text: str, tokens: Sequence[str]) -> str:
    def _sub(match: "re.Match[str]") -> str:
        idx = int(match.group(1))
        return tokens[idx] if 0 <= idx < len(tokens) else match.group(0)

    return re.sub(r"\x00P(\d+)\x00", _sub, text)


def replace_relative_time(
    text: str,
    *,
    date_placeholder: str = "{{日期}}",
    year_placeholder: str = "{{日期}}",
    strict: bool = True,
) -> Tuple[str, List[str]]:
    """把具体日期与相对时限替换为占位符，**不得**误伤标准编号。

    顺序（§9.3 替换流水线的前三步）：

    1. 掩码 R-D2 / R-D3 保护 token；
    2. 结构化替换：完整日期 → `date_placeholder`；
    3. 日期类替换：四位年份（R-D1）与相对时限 → 占位符；
    4. 还原哨兵；
    5. 不变式校验（保护 token 序列逐项相等），不等即报错。

    返回 (替换后文本, 受保护 token 列表)。`strict=True` 时不变式不成立会抛
    `ValueError`（对应 §9.3「任一步失败即回滚报错，不写盘」）。
    """
    before_tokens = protected_tokens(text)
    masked, tokens = _mask_protected(text)

    for pat in FULL_DATE_RES:
        masked = pat.sub(date_placeholder, masked)
    # R-D1：年份匹配单元必须是四位
    masked = YEAR_RE.sub(year_placeholder, masked)
    # 相对时限：机构内部考核口径不得进入成果（§9.4）
    masked = RELATIVE_TIME_RE.sub(date_placeholder, masked)

    out = _unmask(masked, tokens)
    after_tokens = protected_tokens(out)
    if strict and before_tokens != after_tokens:
        raise ValueError(
            "占位符替换污染标准编号：保护 token 序列前后不相等 "
            "before=%r after=%r" % (before_tokens, after_tokens)
        )
    return out, list(tokens)


# --------------------------------------------------------------------------
# 表单号登记表
# --------------------------------------------------------------------------


def load_registry(path: Optional[Any] = None) -> "FormNumberRegistry":
    """加载 `form_number_registry.yaml`。"""
    p = Path(path) if path is not None else REGISTRY_PATH
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return FormNumberRegistry(data, source_path=p)


class FormNumberRegistry:
    """机构表单号登记表。

    登记表**只登记占位符形态的编号模板与来源出处说明**，不复制真实表单号原文。
    """

    def __init__(self, data: Dict[str, Any], *, source_path: Optional[Path] = None):
        self.data = data or {}
        self.source_path = source_path
        self.entries: List[Dict[str, Any]] = list(self.data.get("entries") or [])
        self.public_standards: List[Dict[str, Any]] = list(self.data.get("public_standards") or [])
        self.neutral_patterns: List[Dict[str, Any]] = list(self.data.get("neutral_patterns") or [])
        self._entry_res = [(e, re.compile(e["pattern"])) for e in self.entries]
        self._std_res = [(s, re.compile(s["pattern"])) for s in self.public_standards]
        self._neutral_res = [(s, re.compile(s["pattern"])) for s in self.neutral_patterns]

    # -- 单串判定 ---------------------------------------------------------

    def check(self, text: str) -> FormNumberVerdict:
        """判定单个编号串。

        判定顺序：中性形态 → 公开标准号 → 已登记机构表单号 → 未登记。
        """
        raw = (text or "").strip()
        if not raw:
            return FormNumberVerdict(
                status="unregistered", text=raw, reason="空串，无需判定"
            )

        for spec, pat in self._neutral_res:
            if pat.fullmatch(raw):
                return FormNumberVerdict(
                    status="neutral",
                    text=raw,
                    matched=raw,
                    entry_id=spec.get("id"),
                    form_number_template=spec.get("form_number_template"),
                    reason="已登记的中性编号形态，放行（%s）" % spec.get("label", spec.get("id")),
                )

        for spec, pat in self._std_res:
            if pat.fullmatch(raw):
                return FormNumberVerdict(
                    status="public_standard",
                    text=raw,
                    matched=raw,
                    entry_id=spec.get("id"),
                    form_number_template=spec.get("label"),
                    reason="公开标准号，放行（%s）" % spec.get("label", spec.get("id")),
                )

        for spec, pat in self._entry_res:
            m = pat.fullmatch(raw)
            if m is None:
                m = pat.search(raw)
            if m is not None:
                return FormNumberVerdict(
                    status="conflict",
                    text=raw,
                    matched=m.group(0),
                    entry_id=spec.get("id"),
                    agency=spec.get("agency"),
                    form_number_template=spec.get("form_number_template"),
                    reason="命中已登记的机构表单号（%s / %s），属机构私有信息，须阻断"
                    % (spec.get("id"), spec.get("agency")),
                )

        return FormNumberVerdict(
            status="unregistered",
            text=raw,
            reason="未登记编号，不构成已知机构表单号冲突",
        )

    # -- 文本扫描 ---------------------------------------------------------

    def scan(self, text: str) -> List[FormNumberVerdict]:
        """扫描整段文本里的所有候选编号并逐个判定。"""
        verdicts: List[FormNumberVerdict] = []
        seen: set = set()
        # 中性占位符（形如 {{表单编号}}）先识别，直接放行
        for m in HALFWIDTH_PLACEHOLDER_RE.finditer(text or ""):
            if m.group(0) in AGENCY_DIFF_PLACEHOLDERS and m.group(0) not in seen:
                seen.add(m.group(0))
                verdicts.append(
                    FormNumberVerdict(
                        status="neutral",
                        text=m.group(0),
                        matched=m.group(0),
                        entry_id="TEMPLATE-EXTRA",
                        form_number_template=m.group(0),
                        reason="模板层机构差异占位符，放行",
                    )
                )
        for m in FORM_NUMBER_TOKEN_RE.finditer(text or ""):
            token = m.group(0)
            if token in seen:
                continue
            seen.add(token)
            verdicts.append(self.check(token))
        return verdicts

    def to_dict(self) -> Dict[str, Any]:
        return copy.deepcopy(self.data)


def is_known_form_number(text: str, *, registry: Optional[FormNumberRegistry] = None) -> bool:
    """判定 `text` 是否命中**已登记的机构表单号**。

    命中 → True（冲突，必须阻断）；未命中（中性占位形态、公开标准号、未登记编号）→ False。
    """
    reg = registry if registry is not None else load_registry()
    return reg.check(text).is_conflict


def check_form_number(text: str, *, registry: Optional[FormNumberRegistry] = None) -> FormNumberVerdict:
    """返回一次编号判定的完整结果（含 status / entry_id / 理由）。"""
    reg = registry if registry is not None else load_registry()
    return reg.check(text)


# --------------------------------------------------------------------------
# 表单号隔离（动作三）
# --------------------------------------------------------------------------


def isolate_form_numbers(
    target: Any,
    *,
    registry: Optional[FormNumberRegistry] = None,
    strict: bool = True,
) -> IsolationResult:
    """表单号隔离：扫描 `target`（字符串或嵌套结构）里的编号并判定。

    命中已登记的机构表单号 → 记为冲突；`strict=True` 时抛
    `FormNumberConflictError` 阻断（§9.4「机构表单号体系属机构私有信息」）。
    """
    reg = registry if registry is not None else load_registry()
    if isinstance(target, str):
        texts = [target]
    elif isinstance(target, (list, tuple)):
        texts = [t for t in target if isinstance(t, str)]
    elif isinstance(target, dict):
        texts = []
        _collect_strings(target, texts)
    else:
        texts = []

    verdicts: List[FormNumberVerdict] = []
    conflicts: List[FormNumberVerdict] = []
    for t in texts:
        for v in reg.scan(t):
            verdicts.append(v)
            if v.is_conflict:
                conflicts.append(v)

    result = IsolationResult(text=target if isinstance(target, str) else "", verdicts=verdicts, conflicts=conflicts)
    if strict and conflicts:
        raise FormNumberConflictError(
            "检出 %d 处已登记的机构表单号，必须阻断：%s"
            % (
                len(conflicts),
                ", ".join("%s(%s)" % (c.matched, c.entry_id) for c in conflicts),
            ),
            conflicts=conflicts,
        )
    return result


def _collect_strings(node: Any, out: List[str]) -> None:
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str):
                out.append(k)
            _collect_strings(v, out)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _collect_strings(v, out)


# --------------------------------------------------------------------------
# 机构标识抽取（清洗 / 占位化共用底层工具，两个动作本身互不调用）
# --------------------------------------------------------------------------

#: 机构标识类别 → 占位符（label: 前缀的用于字段名中立化）
AGENCY_MARKER_CATEGORIES: Dict[str, Optional[str]] = {
    "agency_name": "{{认证机构名称}}",
    "form_number": "{{表单编号}}",
    "agency_logo": "{{机构标识}}",
    "agency_address": "{{受审核方地址}}",
    "contact_name": "{{联系人姓名}}",
    "internal_number": "{{机构内部编号}}",
    "template_date": "{{日期}}",
    "agency_field_label": None,
}


def _profile_from_template(template: Any) -> Dict[str, Any]:
    if isinstance(template, dict):
        prof = template.get("agency_profile")
        if isinstance(prof, dict):
            return prof
    return {}


def _iter_strings(node: Any, path: str = "") -> Iterable[Tuple[str, str]]:
    """遍历嵌套结构里的所有字符串，产出 (路径, 字符串)。"""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _iter_strings(v, "%s.%s" % (path, k) if path else str(k))
            if isinstance(k, str) and k not in ("agency_profile",):
                # dict 的键不参与文本替换，仅字段名中立化时单独处理
                continue
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            yield from _iter_strings(v, "%s[%d]" % (path, i))


def _replace_in_tree(node: Any, replacer) -> Any:
    """在嵌套结构里做字符串替换（原地返回新对象）。"""
    if isinstance(node, str):
        return replacer(node)
    if isinstance(node, dict):
        return {k: _replace_in_tree(v, replacer) for k, v in node.items()}
    if isinstance(node, list):
        return [_replace_in_tree(v, replacer) for v in node]
    if isinstance(node, tuple):
        return tuple(_replace_in_tree(v, replacer) for v in node)
    return node


def _sha256_of(value: Any) -> str:
    if isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = json.dumps(
            value, sort_keys=True, ensure_ascii=False, default=str
        ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _collect_agency_values(profile: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """从机构 profile 取出 (category, 原值, 占位符) 列表。"""
    out: List[Tuple[str, str, str]] = []
    keys = {
        "agency_name": "agency_name",
        "form_number": "form_number",
        "agency_logo": "logo_ref",
        "agency_address": "address",
        "contact_name": "contact_name",
        "internal_number": "internal_number",
        "template_date": "template_date",
    }
    for category, key in keys.items():
        val = profile.get(key)
        if isinstance(val, str) and val.strip():
            out.append((category, val, AGENCY_MARKER_CATEGORIES[category] or ""))
    return out


def _neutralize_label(label: str, profile: Dict[str, Any]) -> Tuple[str, Optional[Tuple[str, str]]]:
    """字段名中立化：去掉机构前缀 / 套用机构自有字段名映射。

    返回 (中立化后的字段名, (原名, 新名) 或 None)。
    """
    if not label:
        return label, None

    aliases = profile.get("field_label_aliases") or {}
    if isinstance(aliases, dict) and label in aliases:
        return aliases[label], (label, aliases[label])

    agency = profile.get("agency_name") or ""
    if agency and agency in label:
        new = label.replace(agency, "").strip("：:（）() 　")
        if new and new != label:
            return new, (label, new)

    # 机构名汉字前缀（如「示例认证机构」+「审核计划书」）
    if agency:
        for cut in range(len(agency) - 1, 1, -1):
            prefix = agency[:cut]
            if prefix and label.startswith(prefix) and len(label) > cut:
                new = label[cut:].strip("：:（）() 　")
                if new:
                    return new, (label, new)

    return label, None


# --------------------------------------------------------------------------
# 动作一：清洗
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# 位置感知替换工具（清洗 / 占位化共用底层，两个动作本身互不调用）
# --------------------------------------------------------------------------


def _iter_leaf_paths(node: Any, path: Tuple[Any, ...] = ()) -> Iterable[Tuple[Tuple[Any, ...], str]]:
    """遍历所有叶子字符串及其路径（路径是 tuple，可用于精确改写）。"""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _iter_leaf_paths(v, path + (k,))
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            yield from _iter_leaf_paths(v, path + (i,))


def _get_path(tree: Any, path: Sequence[Any]) -> Any:
    node = tree
    for p in path:
        node = node[p]
    return node


def _set_path(tree: Any, path: Sequence[Any], value: Any) -> None:
    node = tree
    for p in path[:-1]:
        node = node[p]
    node[path[-1]] = value


def _is_label_path(path: Sequence[Any]) -> bool:
    """字段名路径：形如 ('fields', 0, 'label')。"""
    return bool(path) and path[-1] == "label"


def _candidate_values(
    profile: Dict[str, Any], registry: Optional["FormNumberRegistry"], text: str
) -> List[Tuple[str, str, str]]:
    """本段文本里要抽走的机构标识候选：(类别, 原值, 占位符)，**长串优先**。"""
    cands: List[Tuple[str, str, str]] = list(_collect_agency_values(profile))
    if registry is not None and text:
        for verdict in registry.scan(text):
            if verdict.is_conflict and verdict.matched:
                cands.append(("form_number", verdict.matched, "{{表单编号}}"))
    seen = set()
    out: List[Tuple[str, str, str]] = []
    for c in cands:
        if not c[1] or c[1] in seen:
            continue
        seen.add(c[1])
        out.append(c)
    out.sort(key=lambda item: len(item[1]), reverse=True)
    return out


def _replace_positioned(
    text: str, candidates: Sequence[Tuple[str, str, str]]
) -> Tuple[str, List[Dict[str, Any]]]:
    """单趟扫描替换（长串优先），返回 (新文本, 编辑清单)。

    编辑清单里的 start/end 是**新文本**里的坐标，配合原值可把文本逐字还原。
    """
    if not text:
        return text, []
    out: List[str] = []
    edits: List[Dict[str, Any]] = []
    pos = 0
    i = 0
    n = len(text)
    while i < n:
        hit = None
        for category, original, placeholder in candidates:
            if original and text.startswith(original, i):
                hit = (category, original, placeholder)
                break
        if hit is None:
            ch = text[i]
            out.append(ch)
            pos += len(ch)
            i += 1
            continue
        category, original, placeholder = hit
        out.append(placeholder)
        edits.append(
            {
                "start": pos,
                "end": pos + len(placeholder),
                "original": original,
                "placeholder": placeholder,
                "category": category,
            }
        )
        pos += len(placeholder)
        i += len(original)
    return "".join(out), edits


# --------------------------------------------------------------------------
# 动作一：清洗
# --------------------------------------------------------------------------


def sanitize_template(
    template: Any,
    *,
    agency_profile: Optional[Dict[str, Any]] = None,
    registry: Optional[FormNumberRegistry] = None,
    stamp_time: bool = False,
) -> SanitizeResult:
    """清洗：把机构专有信息抽成占位符（**可逆**）。

    抽出范围（§9.4 + §11.2②）：机构名、表单号、logo／机构标识、地址、联系人、
    内部编号、模板日期、机构自有字段名。

    * 原件一个字节都不动：替换只在深拷贝上做（§2.3①、§11.2①）；
    * 每次替换的坐标被记录在 `edits` 里，`restore_sanitized` 能把模板**逐字**还原；
    * `log` 留档：原件哈希、清洗项、清洗后哈希（§11.2④）。

    返回 `SanitizeResult`：template / markers / mapping / log / edits / label_edits。
    """
    profile = dict(agency_profile) if agency_profile is not None else _profile_from_template(template)
    reg = registry if registry is not None else load_registry()

    working = copy.deepcopy(template)

    # ① 机构自有字段名中立化（先做：机构名一旦被替换成占位符，就认不出原字段名了）
    label_edits: List[Dict[str, Any]] = []
    for path, text in list(_iter_leaf_paths(working)):
        if not _is_label_path(path):
            continue
        new_label, pair = _neutralize_label(text, profile)
        if pair is not None:
            _set_path(working, path, new_label)
            label_edits.append({"path": list(path), "original": pair[0], "neutral": pair[1]})

    # ② 机构标识 → 占位符（位置感知，长串优先）
    edits: List[Dict[str, Any]] = []
    for path, text in list(_iter_leaf_paths(working)):
        if _is_label_path(path):
            continue
        cands = _candidate_values(profile, reg, text)
        if not cands:
            continue
        new_text, path_edits = _replace_positioned(text, cands)
        if path_edits:
            _set_path(working, path, new_text)
            for e in path_edits:
                item = dict(e)
                item["path"] = list(path)
                edits.append(item)

    # ③ 机构标识清单（按 类别 + 原值 + 占位符 聚合）
    agg: Dict[Tuple[str, str, str], int] = {}
    for e in edits:
        key = (e["category"], e["original"], e["placeholder"])
        agg[key] = agg.get(key, 0) + 1
    for e in label_edits:
        key = ("agency_field_label", e["original"], e["neutral"])
        agg[key] = agg.get(key, 0) + 1
    markers = [
        {"category": c, "original": o, "placeholder": p, "occurrences": n}
        for (c, o, p), n in agg.items()
    ]

    # ④ 可逆映射表
    mapping: Dict[str, str] = {}
    for e in edits:
        mapping.setdefault("marker:" + e["placeholder"], e["original"])
        mapping.setdefault(e["placeholder"], e["original"])
    for e in label_edits:
        mapping["label:" + e["original"]] = e["neutral"]

    counts: Dict[str, int] = {}
    for (c, _o, _p), n in agg.items():
        counts[c] = counts.get(c, 0) + n

    log: Dict[str, Any] = {
        "policy": "原件一个字节都不动；替换只在复制出的副本上做（output-formats.md §2.3①、§11.2①）",
        "source_sha256": _sha256_of(template),
        "sanitized_sha256": _sha256_of(working),
        "cleaned_items": len(markers),
        "categories": dict(sorted(counts.items())),
        "reversible": True,
        "edits": len(edits),
        "label_edits": len(label_edits),
    }
    if stamp_time:
        import datetime as _dt

        log["sanitized_at"] = _dt.datetime.now().isoformat(timespec="seconds")

    return SanitizeResult(
        template=working,
        markers=markers,
        mapping=mapping,
        log=log,
        edits=edits,
        label_edits=label_edits,
    )


def restore_sanitized(result: SanitizeResult) -> Any:
    """按编辑清单把清洗结果**逐字**还原（可逆性验证用）。

    还原顺序：文本编辑按新文本坐标倒序回填，再回填字段名。
    """
    restored = copy.deepcopy(result.template)

    by_path: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    for e in result.edits:
        by_path.setdefault(tuple(e["path"]), []).append(e)

    for path, edits in by_path.items():
        try:
            text = _get_path(restored, path)
        except (KeyError, IndexError, TypeError):
            continue
        if not isinstance(text, str):
            continue
        for e in sorted(edits, key=lambda x: x["start"], reverse=True):
            text = text[: e["start"]] + e["original"] + text[e["end"]:]
        _set_path(restored, path, text)

    for e in result.label_edits:
        path = tuple(e["path"])
        try:
            _get_path(restored, path)
        except (KeyError, IndexError, TypeError):
            continue
        _set_path(restored, path, e["original"])

    return restored


# --------------------------------------------------------------------------
# 动作二：占位化
# --------------------------------------------------------------------------


def templatize_template(
    template: Any,
    *,
    agency_profile: Optional[Dict[str, Any]] = None,
    registry: Optional[FormNumberRegistry] = None,
    strict: bool = True,
) -> TemplatizeResult:
    """占位化：把机构模板转成**机构中立**成果模板。

    三件事：

    1. 机构标识 → 占位符（机构名、表单号、logo、地址、联系人、内部编号、模板日期）；
    2. 日期类替换走 §9.3 流水线（保护 R-D2/R-D3 标准号，R-D1 只认四位年份），
       相对时限整体占位化，**不得**出现 `ISO 9001` → `ISO 90{{时间}}15` 这类事故；
    3. 字段名中立化。

    返回 `TemplatizeResult`：`template`（中立模板）、`neutral_labels`、
    `protected`（被保护的标准号 token）、`residuals`（残留检查）、
    `log`（含保护 token 不变式校验结论）。
    """
    profile = dict(agency_profile) if agency_profile is not None else _profile_from_template(template)
    reg = registry if registry is not None else load_registry()

    neutral_labels: Dict[str, str] = {}
    protected_all: List[str] = []

    agency_values = sorted(
        _collect_agency_values(profile), key=lambda item: len(item[1]), reverse=True
    )

    def _repl(text: str) -> str:
        out = text
        for category, original, placeholder in agency_values:
            if not original or original not in out:
                continue
            out = out.replace(original, placeholder)
        # 登记过的机构表单号 → 表单编号占位符
        for verdict in reg.scan(out):
            if verdict.is_conflict and verdict.matched:
                out = out.replace(verdict.matched, "{{表单编号}}")
        # 日期类替换（§9.3）
        out, tokens = replace_relative_time(out, strict=strict)
        protected_all.extend(tokens)
        return out

    neutral = _replace_in_tree(copy.deepcopy(template), _repl)

    if isinstance(neutral, dict):
        for field_def in neutral.get("fields", []) or []:
            if not isinstance(field_def, dict):
                continue
            label = field_def.get("label")
            if not isinstance(label, str):
                continue
            new_label, pair = _neutralize_label(label, profile)
            if pair is not None:
                field_def["label"] = new_label
                neutral_labels[pair[0]] = pair[1]
        fn = neutral.get("form_number")
        if isinstance(fn, dict) and isinstance(fn.get("value"), str):
            if reg.check(fn["value"]).is_conflict:
                fn["value"] = "{{表单编号}}"

    # 残留检查：模板口径（全角形态与字典外键为残留；字典内合法占位符不算）
    residuals: List[Dict[str, Any]] = []
    bare = 0
    for path, text in _iter_strings(neutral):
        for item in find_residual_placeholders(text, strict=False):
            residuals.append(dict(item, path=path))
        bare += count_bare_braces(text)
    if bare:
        residuals.append({"kind": "bare_brace", "token": "{", "count": bare})

    before = protected_tokens(_flatten_text(template))
    after = protected_tokens(_flatten_text(neutral))
    invariant_ok = before == after

    log: Dict[str, Any] = {
        "protected_tokens_before": before,
        "protected_tokens_after": after,
        "protect_invariant_ok": invariant_ok,
        "residual_count": len(residuals),
        "bare_brace_count": bare,
        "neutral_label_count": len(neutral_labels),
    }
    if strict and not invariant_ok:
        raise ValueError("占位化污染标准编号：保护 token 序列前后不相等")

    return TemplatizeResult(
        template=neutral,
        neutral_labels=neutral_labels,
        protected=list(dict.fromkeys(protected_all)),
        residuals=residuals,
        log=log,
    )


def _flatten_text(node: Any) -> str:
    out: List[str] = []
    for _path, text in _iter_strings(node):
        out.append(text)
    return "\n".join(out)


def render_template(template: Any, values: Dict[str, str]) -> Tuple[Any, List[Dict[str, Any]]]:
    """把模板实例化：占位符全部填值，返回 (实例, 残留清单)。

    未提供的占位符填「（待填）」；返回值里 §9.2 口径的残留必须为 0。
    """
    keys = sorted(KNOWN_PLACEHOLDERS, key=len, reverse=True)

    def _repl(text: str) -> str:
        out = text
        for key in keys:
            if key in out:
                out = out.replace(key, values.get(key) or "（待填）")
        return out

    rendered = _replace_in_tree(copy.deepcopy(template), _repl)

    residuals: List[Dict[str, Any]] = []
    for path, text in _iter_strings(rendered):
        for item in find_residual_placeholders(text, strict=True):
            residuals.append(dict(item, path=path))
        bare = count_bare_braces(text)
        if bare:
            residuals.append({"kind": "bare_brace", "token": "{", "path": path, "count": bare})
    return rendered, residuals


# --------------------------------------------------------------------------
# 模板集读取
# --------------------------------------------------------------------------


def load_family_template(family_id: str, *, families_dir: Optional[Any] = None) -> Dict[str, Any]:
    """按 `family_id`（F01..F12）读取模板定义。"""
    d = Path(families_dir) if families_dir is not None else FAMILIES_DIR
    for path in sorted(d.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if str(data.get("family_id", "")).upper() == str(family_id).upper():
            data["_path"] = str(path)
            return data
    raise KeyError("未找到模板族定义：%s" % family_id)


def load_family_templates(*, families_dir: Optional[Any] = None) -> Dict[str, Dict[str, Any]]:
    """读取模板集全部模板定义，按 `family_id` 索引。"""
    d = Path(families_dir) if families_dir is not None else FAMILIES_DIR
    out: Dict[str, Dict[str, Any]] = {}
    for path in sorted(d.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        fid = str(data.get("family_id", "")).upper()
        if not fid:
            continue
        data["_path"] = str(path)
        out[fid] = data
    return out


# --------------------------------------------------------------------------
# §9.2 残留统计
# --------------------------------------------------------------------------


def residual_counts(text: str) -> Dict[str, int]:
    """§9.2 残留计数：全角双花括号 / 半角双花括号 / 裸花括号。

    交付物口径（§9.2）：全角 = 0、半角 = 0、裸花括号 = 0。
    模板口径：合法占位符（§9.1 字典 + 模板词表）不算残留，
    但**全角形态**与**字典外键**一律算残留。
    """
    return {
        "fullwidth": len(FULLWIDTH_PLACEHOLDER_RE.findall(text or "")),
        "halfwidth": len(HALFWIDTH_PLACEHOLDER_RE.findall(text or "")),
        "bare": count_bare_braces(text or ""),
    }


def residual_report(template: Any, *, strict: bool = True) -> Dict[str, Any]:
    """整棵模板的 §9.2 残留统计（逐路径明细 + 汇总计数）。

    * `strict=True`（交付物口径）：任何半角/全角双花括号都算残留；
    * `strict=False`（模板口径）：只有全角形态、字典外半角键算残留。
    """
    fullwidth = 0
    halfwidth = 0
    bare = 0
    unknown = 0
    items: List[Dict[str, Any]] = []
    for path, text in _iter_strings(template):
        counts = residual_counts(text)
        fullwidth += counts["fullwidth"]
        halfwidth += counts["halfwidth"]
        bare += counts["bare"]
        for item in find_residual_placeholders(text, strict=strict):
            if item["kind"] == "halfwidth" and item["token"] not in KNOWN_PLACEHOLDERS:
                unknown += 1
            items.append(dict(item, path=path))
    return {
        "fullwidth": fullwidth,
        "halfwidth": halfwidth,
        "bare": bare,
        "unknown_halfwidth": unknown,
        "items": items,
        "total": len(items),
    }
