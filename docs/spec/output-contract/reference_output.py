#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""输出契约参考实现与自检（票 #39）。

用法
----
  python reference_output.py --generate-samples   # 生成 samples/*.json 与 samples.jsonl
  python reference_output.py --verify             # 跑 E1–E12 自检并写 verification.txt
  python reference_output.py --check FILE         # 校验单个底稿 JSON，打印错误清单

只依赖标准库（与仓内其它 reference_*.py 同此约束）。

本文件实现三件事：

1. 最小 JSON Schema 校验器——只实现 output-contract.schema.json 用到的关键字
   （$ref / type / enum / const / required / properties / additionalProperties /
   items / minItems / maxItems / minLength / maximum / minimum / pattern /
   oneOf / allOf / if-then-else / not / contains / format:date-time）。
2. 禁用词检查器——唯一数据源是同目录 prohibited-terms.json。
3. 三处投影——to_api / to_page / to_report，字段逐字沿用 #46 的既有定义。

两条版本轴（不要混同、也不要互相校验）：
  * 本契约版本 = 底稿的 contract_version（本文件 CONTRACT_VERSION）。
  * #46 的 API 契约版本 = meta.contract_version（本文件 API_CONTRACT_VERSION，来源 #46 SPEC.md §「契约版本在 meta.contract_version 回显（当前 1.0.0）」）。

页面块的载荷粒度：#46 页面块不复制底稿载荷，只带 source.output_ref 指向底稿
（SPEC.md §9.2）。因此 to_page 产出的 data 是「载荷清单」而非底稿副本，
state = empty 时 data 必须为 null（§9.4）。清单粒度是否需要升级为渲染视图，
留给 #38／#49 决定。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]

SCHEMA_PATH = HERE / "output-contract.schema.json"
TERMS_PATH = HERE / "prohibited-terms.json"
SAMPLES_DIR = HERE / "samples"
SAMPLES_JSONL = HERE / "samples.jsonl"
VERIFICATION_PATH = HERE / "verification.txt"
PAGE_SCHEMA_PATH = ROOT / "docs" / "spec" / "api-contract" / "page-data.schema.json"
OPENAPI_PATH = ROOT / "docs" / "spec" / "api-contract" / "openapi.json"

CONTRACT_VERSION = "1.0.0"
API_CONTRACT_VERSION = "1.0.0"

DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$"
)

STATUS_TO_CODE = {
    "OK": {"OK"},
    "PARTIAL": {"PARTIAL_GAP"},
    "DEGRADED": {"FEATURE_DEGRADED"},
    "REFUSED": {"SOURCE_UNREACHABLE", "PRECONDITION_FAILED"},
    "OUT_OF_RANGE": {"OUT_OF_DISTRIBUTION", "FEATURE_MISSING_RATIO"},
    "ANOMALY_EXCLUDED": {"REJECTED_ON_INGEST", "DIRTY_EXCLUDED"},
    "SUPERSEDED": {"OVERRIDDEN"},
}

STATUS_TO_PAGE_STATE = {
    "OK": "ok",
    "PARTIAL": "partial",
    "DEGRADED": "partial",
    "REFUSED": "empty",
    "OUT_OF_RANGE": "empty",
    "ANOMALY_EXCLUDED": "partial",
    "SUPERSEDED": "empty",
}

VERIFICATION_WORD = {"VERIFIED": "已核验", "UNVERIFIED": "未核验", "MISSING": "缺失"}

UNIT_PRECISION = {"MWh": 3, "CNY/MWh": 4, "CNY": 2, "ratio": 4, "count": 0, "none": 0}

ACTION_TYPES = {
    "VIEW",
    "EXPORT",
    "REQUEST_REVIEW",
    "SUBMIT_OVERRIDE",
    "SWITCH_MODEL",
    "PROPOSE_RETRAIN",
    "COLLECT_DATA",
}

DOMAIN_TYPES = {
    "SETTLEMENT_REPORT": ("55", "settlement-replay"),
    "STRATEGY_ADVICE": ("56", "position-strategy"),
    "TRAINING_SCORE": ("58", "training-scoring"),
    "RETAIL_ANALYSIS": ("59", "retail-pricing"),
}

FIXED_SENTENCE = "基于历史统计关联，非因果判定"

# status != OK 时，notes 必须给出的可执行下一步关键词（§5.1）
ACTION_HINTS = (
    "补数据",
    "补齐",
    "换源",
    "复核",
    "重训",
    "重跑",
    "剔除",
    "取代",
    "不可达",
    "超出可用范围",
)


# --------------------------------------------------------------------------
# 通用工具
# --------------------------------------------------------------------------
def load_json(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def compute_document_id(doc: dict) -> str:
    """SPEC.md §4.1：sha256(output_type|scope_digest|as_of|revision|generated_at) 前 16 位。"""
    scope_digest = sha16(canonical(doc["scope"]))
    raw = "|".join(
        [
            doc["output_type"],
            scope_digest,
            doc["as_of"],
            str(doc["revision"]),
            doc["generated_at"],
        ]
    )
    return sha16(raw)


def compute_content_hash(doc: dict) -> str:
    """SPEC.md §4.1：去掉 content_hash 自身、并把 report_sections[].content_hash 置空后的规范化 JSON。"""
    payload = copy.deepcopy(doc)
    payload.pop("content_hash", None)
    for section in payload.get("projections", {}).get("report_sections", []):
        section["content_hash"] = ""
    return sha16(canonical(payload))


def finalize(doc: dict) -> dict:
    """补齐 document_id / projections 登记 / content_hash，并保证三者自洽。"""
    doc["document_id"] = compute_document_id(doc)
    projections = doc.get("projections", {})
    for block in projections.get("page_blocks", []):
        block["document_id"] = doc["document_id"]
        block["output_ref"]["output_id"] = doc["document_id"]
    for endpoint in projections.get("api_endpoints", []):
        endpoint["document_id"] = doc["document_id"]
    for section in projections.get("report_sections", []):
        section["document_id"] = doc["document_id"]
    doc["content_hash"] = compute_content_hash(doc)
    for section in projections.get("report_sections", []):
        section["content_hash"] = doc["content_hash"]
    return doc


# --------------------------------------------------------------------------
# 最小 JSON Schema 校验器
# --------------------------------------------------------------------------
def resolve_pointer(root, ref: str):
    if not ref.startswith("#/"):
        raise ValueError("只支持本地 JSON Pointer 引用：%s" % ref)
    node = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        node = node[part]
    return node


def is_type(inst, name: str) -> bool:
    if name == "object":
        return isinstance(inst, dict)
    if name == "array":
        return isinstance(inst, list)
    if name == "string":
        return isinstance(inst, str)
    if name == "integer":
        return isinstance(inst, int) and not isinstance(inst, bool)
    if name == "number":
        return isinstance(inst, (int, float)) and not isinstance(inst, bool)
    if name == "boolean":
        return isinstance(inst, bool)
    if name == "null":
        return inst is None
    raise ValueError("未知 type：%s" % name)


def validate(inst, schema, root, path: str = "$"):
    errs = []
    if schema is True:
        return errs
    if schema is False:
        return ["%s: 该值不被允许" % path]
    if not isinstance(schema, dict):
        return errs

    if "$ref" in schema:
        errs += validate(inst, resolve_pointer(root, schema["$ref"]), root, path)
        rest = {k: v for k, v in schema.items() if k != "$ref"}
        if rest:
            errs += validate(inst, rest, root, path)
        return errs

    declared = schema.get("type")
    if declared is not None:
        names = declared if isinstance(declared, list) else [declared]
        if not any(is_type(inst, n) for n in names):
            errs.append("%s: 类型应为 %s，实际为 %s" % (path, names, type(inst).__name__))
            return errs

    if "enum" in schema and inst not in schema["enum"]:
        errs.append("%s: 取值不在 enum 内" % path)
    if "const" in schema and inst != schema["const"]:
        errs.append("%s: 取值不等于 const" % path)

    if isinstance(inst, str):
        if "minLength" in schema and len(inst) < schema["minLength"]:
            errs.append("%s: 长度小于 minLength" % path)
        if "pattern" in schema and not re.search(schema["pattern"], inst):
            errs.append("%s: 不匹配 pattern %s" % (path, schema["pattern"]))
        if schema.get("format") == "date-time" and not DATETIME_RE.match(inst):
            errs.append("%s: 不是合法 date-time" % path)

    if isinstance(inst, (int, float)) and not isinstance(inst, bool):
        if "minimum" in schema and inst < schema["minimum"]:
            errs.append("%s: 小于 minimum" % path)
        if "maximum" in schema and inst > schema["maximum"]:
            errs.append("%s: 大于 maximum" % path)

    if isinstance(inst, dict):
        for key in schema.get("required", []):
            if key not in inst:
                errs.append("%s: 缺少必填字段 %s" % (path, key))
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in inst:
                errs += validate(inst[key], sub, root, "%s.%s" % (path, key))
        extra = [k for k in inst if k not in props]
        additional = schema.get("additionalProperties", True)
        if additional is False:
            for key in extra:
                errs.append("%s: 不允许的额外字段 %s" % (path, key))
        elif isinstance(additional, dict):
            for key in extra:
                errs += validate(inst[key], additional, root, "%s.%s" % (path, key))

    if isinstance(inst, list):
        if "minItems" in schema and len(inst) < schema["minItems"]:
            errs.append("%s: 元素少于 minItems" % path)
        if "maxItems" in schema and len(inst) > schema["maxItems"]:
            errs.append("%s: 元素多于 maxItems" % path)
        if "items" in schema:
            for idx, value in enumerate(inst):
                errs += validate(value, schema["items"], root, "%s[%d]" % (path, idx))
        if "contains" in schema:
            hit = any(
                not validate(value, schema["contains"], root, "%s[%d]" % (path, idx))
                for idx, value in enumerate(inst)
            )
            if not hit:
                errs.append("%s: 没有元素满足 contains" % path)

    for sub in schema.get("allOf", []):
        errs += validate(inst, sub, root, path)

    if "oneOf" in schema:
        matched = sum(
            1 for sub in schema["oneOf"] if not validate(inst, sub, root, path)
        )
        if matched != 1:
            errs.append("%s: oneOf 命中 %d 个（应为 1）" % (path, matched))

    if "anyOf" in schema:
        if not any(not validate(inst, sub, root, path) for sub in schema["anyOf"]):
            errs.append("%s: anyOf 无命中" % path)

    if "not" in schema:
        if not validate(inst, schema["not"], root, path):
            errs.append("%s: 不应命中 not 子模式" % path)

    if "if" in schema:
        matched = not validate(inst, schema["if"], root, path)
        if matched and "then" in schema:
            errs += validate(inst, schema["then"], root, path)
        if (not matched) and "else" in schema:
            errs += validate(inst, schema["else"], root, path)

    return errs


# --------------------------------------------------------------------------
# 禁用词检查器（唯一数据源：prohibited-terms.json）
# --------------------------------------------------------------------------
def normalize_text(text: str, options: dict) -> str:
    if options.get("fullwidth_to_halfwidth", True):
        text = unicodedata.normalize("NFKC", text)
    if options.get("case_fold", True):
        text = text.casefold()
    if options.get("collapse_whitespace", True):
        text = re.sub(r"\s+", " ", text)
    if options.get("strip_zero_width", True):
        text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    if options.get("trim", True):
        text = text.strip()
    return text


class TermChecker:
    def __init__(self, spec: dict):
        self.options = spec.get("normalization", {})
        self.terms = [t["term"] for t in spec.get("prohibited_terms", [])]
        self.patterns = spec.get("prohibited_patterns", [])
        self.allowed = [p["text"] for p in spec.get("allowed_phrases", [])]

    def hits(self, text: str):
        """返回命中列表；每项为 (类别, 命中内容)。"""
        norm = normalize_text(text, self.options)
        for phrase in self.allowed:
            norm = norm.replace(normalize_text(phrase, self.options), " ")
        found = []
        for term in self.terms:
            needle = normalize_text(term, self.options)
            if needle and needle in norm:
                found.append(("term", term))
        for pattern in self.patterns:
            for match in re.finditer(pattern["pattern"], norm):
                found.append(("pattern", "%s:%s" % (pattern["id"], match.group(0))))
        return found


def scoped_texts(doc: dict):
    """按 prohibited-terms.json 的 check_scope 取待检文本。"""
    scoped = []
    for index, attribution in enumerate(doc.get("attributions", [])):
        scoped.append(("attributions[%d].text" % index, attribution.get("text", "")))
    for index, item in enumerate(doc.get("items", [])):
        if item.get("item_type") == "STATEMENT":
            for vindex, value in enumerate(item.get("values", [])):
                scoped.append(
                    (
                        "items[%d].values[%d].value" % (index, vindex),
                        str(value.get("value", "")),
                    )
                )
    for index, risk in enumerate(doc.get("risks", [])):
        scoped.append(("risks[%d].statement" % index, risk.get("statement", "")))
    for index, drift in enumerate(doc.get("drift_signals", [])):
        scoped.append(("drift_signals[%d].signal_text" % index, drift.get("signal_text", "")))
    for index, note in enumerate(doc.get("notes", [])):
        scoped.append(("notes[%d]" % index, note))
    for index, override in enumerate(doc.get("overrides", [])):
        scoped.append(("overrides[%d].reason_text" % index, override.get("reason_text", "")))
    for index, action in enumerate(doc.get("actions", [])):
        scoped.append(("actions[%d].label" % index, action.get("label", "")))
    return scoped


# --------------------------------------------------------------------------
# 三处投影
# --------------------------------------------------------------------------
def to_api(doc: dict, request_id: str = "req_39sample000001"):
    """API 投影：#46 GET /api/v1/outputs/{output_id} 的 200 报文。"""
    return {
        "data": {
            "output_id": doc["document_id"],
            "contract": "#39",
            "status": "READY",
            "payload": doc,
        },
        "meta": {
            "request_id": request_id,
            "api_version": "v1",
            "contract_version": API_CONTRACT_VERSION,
            "generated_at": doc["generated_at"],
        },
    }


def to_page(doc: dict, page_id: str = "trader_home", block_id: str = "tomorrow_forecast"):
    """页面投影：#46 GET /api/v1/pages/{page_id} 的一个块。"""
    state = STATUS_TO_PAGE_STATE[doc["status"]]
    counts = {"VERIFIED": 0, "UNVERIFIED": 0, "MISSING": 0}
    for item in doc.get("items", []):
        for value in item.get("values", []):
            counts[value["verification_status"]] += 1
    coverage = doc["metrics"]["coverage"]["value"]
    data = None
    if state != "empty":
        data = {
            "output_id": doc["document_id"],
            "contract": "#39",
            "contract_version": doc["contract_version"],
            "status": doc["status"],
            "item_refs": [item["item_ref"] for item in doc.get("items", [])],
            "coverage_ratio": coverage,
        }
    title = doc.get("items")[0]["label"] if doc.get("items") else "输出底稿"
    return {
        "id": block_id,
        "type": "output_ref",
        "title": title,
        "source": {
            "operation_id": "getOutput",
            "output_ref": {
                "output_id": doc["document_id"],
                "contract": "#39",
                "status": "READY",
            },
        },
        "state": state,
        "freshness": {"as_of": doc["as_of"], "stale": False},
        "quality": {
            "valid": counts["VERIFIED"],
            "partial": counts["UNVERIFIED"],
            "missing": counts["MISSING"],
            "coverage_ratio": coverage,
        },
        "notes": list(doc.get("notes", [])),
        "actions": [
            {
                "id": action["action_id"],
                "label": action["label"],
                "capability": "OUTPUT:VIEW",
            }
            for action in doc.get("actions", [])
            if action["type"] == "VIEW"
        ],
        "data": data,
    }


def to_report(doc: dict, section_id: str = "forecast_result"):
    """报告投影：章节引用同一 document_id，并附快照三元组。"""
    return {
        "section_id": section_id,
        "document_id": doc["document_id"],
        "contract_version": doc["contract_version"],
        "content_hash": doc["content_hash"],
    }


# --------------------------------------------------------------------------
# 语义检查（每条检查产出带 [Ex] 前缀的错误）
# --------------------------------------------------------------------------
def check_provenance(doc: dict):
    """E2 溯源最小集 / E3 未核验数值不进自动判定。"""
    errs = []
    if doc["status"] == "OK" and not doc.get("sources"):
        errs.append("[E2] status = OK 但 sources 为空，来源缺失")
    unverified_sources = {
        (s["data_source_id"], s["batch_id"])
        for s in doc.get("sources", [])
        if not s["verified"]
    }

    def walk(values, where):
        for index, value in enumerate(values):
            ref = value.get("source_ref")
            status = value["verification_status"]
            if ref is None and status == "VERIFIED":
                errs.append("[E2] %s[%d]: 标为已核验却没有来源" % (where, index))
            if (
                status == "MISSING"
                and value["unit"] != "none"
                and value.get("value") is not None
            ):
                errs.append("[E3] %s[%d]: 缺失值不得呈现数值" % (where, index))
            if (
                ref is not None
                and status == "VERIFIED"
                and (ref["data_source_id"], ref["batch_id"]) in unverified_sources
            ):
                errs.append("[E3] %s[%d]: 未核验来源支撑的数值不得标为 VERIFIED" % (where, index))

    walk([doc["metrics"]["coverage"]], "metrics.coverage")
    walk([doc["metrics"]["refusal_rate"]], "metrics.refusal_rate")
    walk(doc["metrics"]["accuracy"], "metrics.accuracy")
    for index, item in enumerate(doc.get("items", [])):
        walk(item.get("values", []), "items[%d].values" % index)
    for index, attribution in enumerate(doc.get("attributions", [])):
        if attribution.get("contribution") is not None:
            walk([attribution["contribution"]], "attributions[%d].contribution" % index)
    for index, risk in enumerate(doc.get("risks", [])):
        if risk["severity"] != "INFO" and risk["verification_status"] != "VERIFIED":
            errs.append(
                "[E3] risks[%d]: severity 高于 INFO 但依据未核验，应降级为 INFO" % index
            )
    for index, drift in enumerate(doc.get("drift_signals", [])):
        walk([drift["magnitude"]], "drift_signals[%d].magnitude" % index)
    for index, override in enumerate(doc.get("overrides", [])):
        for key in ("before", "after"):
            if override.get(key) is not None:
                walk([override[key]], "overrides[%d].%s" % (index, key))
    return errs


def check_format(doc: dict):
    """E10 数值格式：单位位宽、字符串十进制、null 不补零。"""
    errs = []

    def walk(values, where):
        for index, value in enumerate(values):
            expected = UNIT_PRECISION.get(value["unit"])
            if expected is not None and value["precision"] != expected:
                errs.append(
                    "[E10] %s[%d]: unit=%s 的 precision 应为 %d，实际 %d"
                    % (where, index, value["unit"], expected, value["precision"])
                )
            raw = value.get("value")
            if isinstance(raw, float):
                errs.append("[E10] %s[%d]: 十进制必须用字符串承载" % (where, index))
            if value["verification_status"] == "MISSING" and raw is not None:
                errs.append("[E10] %s[%d]: 缺失值必须为 null，不得补零" % (where, index))

    walk([doc["metrics"]["coverage"]], "metrics.coverage")
    walk([doc["metrics"]["refusal_rate"]], "metrics.refusal_rate")
    walk(doc["metrics"]["accuracy"], "metrics.accuracy")
    for index, item in enumerate(doc.get("items", [])):
        walk(item.get("values", []), "items[%d].values" % index)
    for index, attribution in enumerate(doc.get("attributions", [])):
        if attribution.get("contribution") is not None:
            walk([attribution["contribution"]], "attributions[%d].contribution" % index)
    for index, item in enumerate(doc.get("items", [])):
        if item["item_type"] == "INTERVAL":
            if len(item["values"]) != 3:
                errs.append("[E10] items[%d]: INTERVAL 必须恰有三个值" % index)
            roles = (item.get("payload") or {}).get("roles")
            if not roles or len(roles) != len(item["values"]):
                errs.append("[E10] items[%d]: INTERVAL 的 payload.roles 长度必须与 values 一致" % index)
        if item["item_type"] == "SERIES":
            slots = (item.get("payload") or {}).get("slots")
            if not slots or len(slots) != len(item["values"]):
                errs.append("[E10] items[%d]: SERIES 的 payload.slots 长度必须与 values 一致" % index)
    return errs


def check_metrics(doc: dict):
    """E9 指标并列、精度不预填、插值禁用。"""
    errs = []
    metrics = doc["metrics"]
    if metrics["coverage"].get("value") is None:
        errs.append("[E9] coverage 与误差指标并列，coverage 不得为空")
    if doc["output_type"] == "FORECAST" and not metrics["accuracy"]:
        errs.append("[E9] FORECAST 必须给出误差指标项（与 coverage 并列）")
    baseline = metrics["baseline_comparison"].get("baseline_value")
    for index, item in enumerate(metrics["accuracy"]):
        if baseline is None:
            if item.get("value") is not None:
                errs.append(
                    "[E9] metrics.accuracy[%d]: 基线未确认前不得预填精度数值" % index
                )
            note = item.get("note") or ""
            if item.get("value") is None and "待基线确认" not in note:
                errs.append("[E9] metrics.accuracy[%d]: 空精度须注明「待基线确认」" % index)
        note = item.get("note") or ""
        for word in ("插值", "外推"):
            if word in note:
                errs.append(
                    "[E9] metrics.accuracy[%d]: %s数值不得进入 accuracy 计算" % (index, word)
                )
        if item.get("excluded_count"):
            if not item.get("excluded_reason"):
                errs.append("[E9] metrics.accuracy[%d]: 剔除样本必须写 excluded_reason" % index)
            size = item.get("sample_size") or 0
            if size and item["excluded_count"] / size > 0.10:
                if "MAPE_NOT_APPLICABLE" not in item["name"]:
                    errs.append(
                        "[E9] metrics.accuracy[%d]: 剔除比例超过 10%%，name 须标 MAPE_NOT_APPLICABLE"
                        % index
                    )
    return errs


def check_attributions(doc: dict, checker: TermChecker):
    """E6 归因：四项限定语 + 固定句 + 禁用词；E7 禁用词（全部作用域）。"""
    errs = []
    for index, attribution in enumerate(doc.get("attributions", [])):
        text = attribution.get("text", "")
        word = VERIFICATION_WORD.get(attribution["verification_status"], "")
        if word and word not in text:
            errs.append(
                "[E6] attributions[%d]: text 缺少核验状态词「%s」" % (index, word)
            )
        label = attribution.get("time_window", {}).get("label", "")
        if label and label not in text:
            errs.append("[E6] attributions[%d]: text 缺少时间窗 %s" % (index, label))
        confidence = attribution.get("confidence", {})
        if confidence.get("kind") == "PREDICTION_INTERVAL":
            if "P10" not in text or "P90" not in text:
                errs.append("[E6] attributions[%d]: text 缺少 P10/P90 置信区间" % index)
        elif confidence.get("kind") == "LEVEL":
            level = confidence.get("level") or ""
            if level and level not in text:
                errs.append("[E6] attributions[%d]: text 缺少置信等级 %s" % (index, level))
        if attribution.get("statement") != FIXED_SENTENCE:
            errs.append("[E6] attributions[%d]: statement 必须是固定句" % index)
        if FIXED_SENTENCE not in text:
            errs.append("[E6] attributions[%d]: text 缺少固定句" % index)
        if not attribution.get("attributable"):
            if attribution.get("contribution") is not None:
                errs.append("[E6] attributions[%d]: 不可归因时不得给出贡献" % index)
            if not attribution.get("not_attributable_reason"):
                errs.append("[E6] attributions[%d]: 不可归因时须写 not_attributable_reason" % index)
        if "概率" in text and not confidence.get("calibration"):
            errs.append("[E6] attributions[%d]: 给概率表述时 calibration 必填" % index)
    for where, text in scoped_texts(doc):
        for kind, hit in checker.hits(text):
            errs.append("[E7] %s 命中禁用%s：%s" % (where, kind, hit))
    return errs


def check_status(doc: dict):
    """E4 三类情形确定路径 / E5 status 与 code 一一对应。"""
    errs = []
    status = doc["status"]
    code = doc["status_reason"]["code"]
    if code not in STATUS_TO_CODE.get(status, set()):
        errs.append("[E5] status=%s 与 status_reason.code=%s 不匹配" % (status, code))
    if status != "OK":
        notes = doc.get("notes", [])
        if not notes:
            errs.append("[E4] status=%s 时 notes 必须写清可执行的下一步" % status)
        elif not any(hint in note for note in notes for hint in ACTION_HINTS):
            errs.append("[E4] status=%s 时 notes 未给出可执行的下一步" % status)
    if status == "PARTIAL":
        detail = doc["status_reason"].get("detail", {})
        if not detail.get("gap_slots"):
            errs.append("[E4] PARTIAL 必须给出缺口标注 gap_slots")
        if not any(item["item_type"] == "INTERVAL" for item in doc.get("items", [])):
            errs.append("[E4] PARTIAL 必须同时给出区间")
    if status == "REFUSED":
        if doc.get("attributions"):
            errs.append("[E4] REFUSED 不得给出归因")
        if not any(
            hint in note for note in doc.get("notes", []) for hint in ("不可达", "剔除", "前提")
        ):
            errs.append("[E4] REFUSED 必须写明剔除原因")
    if status == "OUT_OF_RANGE":
        if not any(
            "超出可用范围" in note for note in doc.get("notes", [])
        ):
            errs.append("[E4] OUT_OF_RANGE 必须写明超出可用范围")
        for index, attribution in enumerate(doc.get("attributions", [])):
            if attribution.get("attributable"):
                errs.append("[E4] OUT_OF_RANGE 不得给出贡献排序")
    if status == "ANOMALY_EXCLUDED":
        if not any(
            "剔除" in note or "批次" in note for note in doc.get("notes", [])
        ):
            errs.append("[E4] ANOMALY_EXCLUDED 必须写明剔除批次与受影响范围")
    return errs


def check_actions(doc: dict):
    """E11 actions：类型白名单、无 EXECUTE。"""
    errs = []
    for index, action in enumerate(doc.get("actions", [])):
        if action["type"] not in ACTION_TYPES:
            errs.append("[E11] actions[%d]: type=%s 不在白名单内" % (index, action["type"]))
        if action["type"] == "EXECUTE":
            errs.append("[E11] actions[%d]: 禁止写入真实交易系统" % index)
    return errs


def check_overrides(doc: dict, corpus):
    """E8 人工修正：版本与责任人留痕、修正链双向。"""
    errs = []
    overrides = doc.get("overrides", [])
    if not overrides:
        if doc["revision"] == 1 and doc.get("supersedes") is not None:
            errs.append("[E8] revision = 1 时 supersedes 必须为 null")
        return errs
    if doc["revision"] < 2:
        errs.append("[E8] 带人工修正的底稿 revision 必须大于 1")
    if not doc.get("supersedes"):
        errs.append("[E8] 带人工修正的底稿必须写 supersedes（原文档标识）")
    for index, override in enumerate(overrides):
        if override.get("revision") != doc["revision"]:
            errs.append(
                "[E8] overrides[%d]: revision 与底稿 revision 不一致" % index
            )
        if override.get("supersedes") != doc.get("supersedes"):
            errs.append(
                "[E8] overrides[%d]: supersedes 必须指向被修正的原底稿 document_id" % index
            )
        if override.get("target_document_id") != doc.get("supersedes"):
            errs.append(
                "[E8] overrides[%d]: target_document_id 必须等于底稿 supersedes 指向的原底稿"
                % index
            )
        if not override.get("audit_ref"):
            errs.append("[E8] overrides[%d]: 缺少审计留痕 audit_ref" % index)
        actor = override.get("actor", {})
        if not actor.get("user_id"):
            errs.append("[E8] overrides[%d]: 缺少责任人 user_id" % index)
        if not (override.get("reason_text") or "").strip():
            errs.append("[E8] overrides[%d]: 修正原因不得为空" % index)
        after = override.get("after")
        if after is not None and not after.get("verification_status"):
            errs.append("[E8] overrides[%d]: 修正后的数值必须带核验状态" % index)
        original = corpus.get(override.get("target_document_id"))
        if original is None:
            errs.append("[E8] overrides[%d]: 找不到被修正的原底稿" % index)
            continue
        if original.get("superseded_by") != doc["document_id"]:
            errs.append("[E8] overrides[%d]: 原底稿 superseded_by 必须指向本修正文档" % index)
        if original.get("status") != "SUPERSEDED":
            errs.append("[E8] overrides[%d]: 原底稿状态必须是 SUPERSEDED" % index)
    return errs


def check_terminology(doc: dict):
    """E12 业务域挂载规则。"""
    errs = []
    if doc["output_type"] not in DOMAIN_TYPES:
        return errs
    ref = doc.get("payload_contract_ref")
    if not isinstance(ref, dict):
        errs.append("[E12] 业务域底稿必须声明 payload_contract_ref")
    else:
        ticket, contract = DOMAIN_TYPES[doc["output_type"]]
        if ref.get("ticket") != ticket:
            errs.append("[E12] payload_contract_ref.ticket 应为 %s" % ticket)
        if ref.get("contract") != contract:
            errs.append("[E12] payload_contract_ref.contract 应为 %s" % contract)
    if not any((item.get("payload") or None) for item in doc.get("items", [])):
        errs.append("[E12] 业务域字段必须落在 items[].payload")
    if doc["metrics"]["coverage"].get("value") is None:
        errs.append("[E12] 业务域底稿的 coverage 仍是一等指标")
    return errs


def check_projections(doc: dict, openapi: dict, page_schema: dict):
    """E1 三处同源：API、页面、报告必须指回同一 document_id。"""
    errs = []
    api = to_api(doc)
    page = to_page(doc)
    report = to_report(doc)

    errs += [
        "[E1] %s" % message
        for message in validate(api, openapi["components"]["schemas"]["OutputResponse"], openapi)
    ]
    errs += [
        "[E1] %s" % message
        for message in validate(
            page, resolve_pointer(page_schema, "#/$defs/PageBlock"), page_schema
        )
    ]

    if api["data"]["payload"] != doc:
        errs.append("[E1] API 投影的 payload 必须是底稿全文")
    if api["data"]["output_id"] != doc["document_id"]:
        errs.append("[E1] API 投影的 output_id 必须等于 document_id")
    if api["meta"]["contract_version"] != API_CONTRACT_VERSION:
        errs.append("[E1] API 投影的 meta.contract_version 必须是 #46 的 API 契约版本")
    if page["source"]["output_ref"]["output_id"] != doc["document_id"]:
        errs.append("[E1] 页面投影的 output_ref.output_id 必须等于 document_id")
    if page["source"]["output_ref"].get("contract_version"):
        errs.append("[E1] 页面 output_ref 不得追加 contract_version（#46 OutputRef 无此字段）")
    if report["document_id"] != doc["document_id"]:
        errs.append("[E1] 报告投影的 document_id 必须等于 document_id")
    if report["content_hash"] != doc["content_hash"]:
        errs.append("[E1] 报告投影的 content_hash 必须等于底稿 content_hash")
    if report["contract_version"] != doc["contract_version"]:
        errs.append("[E1] 报告投影的 contract_version 必须等于底稿 contract_version")
    expected_state = STATUS_TO_PAGE_STATE[doc["status"]]
    if page["state"] != expected_state:
        errs.append("[E1] 页面 state 必须是 %s" % expected_state)
    if page["state"] == "empty" and page["data"] is not None:
        errs.append("[E1] state=empty 时页面 data 必须为 null")
    if page["state"] != "ok" and not page["notes"]:
        errs.append("[E1] state != ok 时页面块必须给出 notes")
    if page["quality"]["coverage_ratio"] != doc["metrics"]["coverage"]["value"]:
        errs.append("[E1] 页面 quality.coverage_ratio 必须取自 metrics.coverage.value")
    for block in doc["projections"]["page_blocks"]:
        if block["document_id"] != doc["document_id"]:
            errs.append("[E1] projections.page_blocks 的 document_id 必须自洽")
    for endpoint in doc["projections"]["api_endpoints"]:
        if endpoint["document_id"] != doc["document_id"]:
            errs.append("[E1] projections.api_endpoints 的 document_id 必须自洽")
    for section in doc["projections"]["report_sections"]:
        if section["document_id"] != doc["document_id"]:
            errs.append("[E1] projections.report_sections 的 document_id 必须自洽")
        if section["content_hash"] != doc["content_hash"]:
            errs.append("[E1] projections.report_sections 的 content_hash 必须自洽")
    return errs


def collect_errors(doc: dict, corpus: dict, checker: TermChecker, openapi: dict, page_schema: dict):
    errs = []
    schema = load_json(SCHEMA_PATH)
    errs += ["[E1] %s" % message for message in validate(doc, schema, schema)]
    errs += check_provenance(doc)
    errs += check_format(doc)
    errs += check_metrics(doc)
    errs += check_attributions(doc, checker)
    errs += check_status(doc)
    errs += check_actions(doc)
    errs += check_overrides(doc, corpus)
    errs += check_terminology(doc)
    errs += check_projections(doc, openapi, page_schema)
    return errs


def error_codes(errs):
    codes = set()
    for message in errs:
        match = re.match(r"\[(E\d+)\]", message)
        if match:
            codes.add(match.group(1))
    return codes


# --------------------------------------------------------------------------
# 样例底稿构造：覆盖七种 status 与四类业务域
# --------------------------------------------------------------------------
SAMPLE_AS_OF = "2026-11-06T00:00:00+08:00"
SAMPLE_GENERATED_AT = "2026-11-06T09:00:00+08:00"
ZERO_ID = "0" * 16


def _scope(entity_type="PORTFOLIO", entity_id="PORT-2026-A"):
    return {
        "subject_scope": "COMPANY",
        "entity_type": entity_type,
        "entity_id": entity_id,
        "period": {
            "kind": "DAY_CURVE",
            "start": "2026-11-07T00:00:00+08:00",
            "end": "2026-11-07T23:00:00+08:00",
            "slot_count": 24,
        },
        "timezone": "Asia/Shanghai",
    }


def _src(field="load_mwh", data_source_id="D01", batch_id="B20261106A"):
    return {
        "data_source_id": data_source_id,
        "batch_id": batch_id,
        "field": field,
    }


def _num(value, unit="MWh", precision=3, status="VERIFIED", source=None, note=None):
    return {
        "value": value,
        "unit": unit,
        "precision": precision,
        "source_ref": _src() if source is None else source,
        "verification_status": status,
        "as_of": SAMPLE_AS_OF,
        "note": note,
    }


def _text(text, status="UNVERIFIED", source=None):
    return {
        "value": text,
        "unit": "none",
        "precision": 0,
        "source_ref": _src("note_text") if source is None else source,
        "verification_status": status,
        "as_of": SAMPLE_AS_OF,
        "note": None,
    }


def _accuracy(name="MAPE", sample_size=240):
    return {
        "value": None,
        "unit": "ratio",
        "precision": 4,
        "source_ref": None,
        "verification_status": "MISSING",
        "as_of": SAMPLE_AS_OF,
        "note": "待基线确认：#61 A06 基线未锁定前不预填精度数值",
        "name": name,
        "sample_size": sample_size,
        "excluded_count": 0,
        "excluded_reason": None,
    }


def _metrics(coverage="1.0000", refusal="0.0000", accuracy=None, sampling=None):
    return {
        "coverage": _num(coverage, unit="ratio", precision=4),
        "accuracy": list(accuracy or []),
        "refusal_rate": _num(refusal, unit="ratio", precision=4),
        "baseline_comparison": {
            "baseline_id": "seasonal_naive",
            "baseline_value": None,
            "window": "2026-01-01/2026-10-31",
        },
        "sampling_note": sampling or "回测口径与样本外窗口见 SPEC §5.3",
    }


def _model(model_id="seasonal_naive_v1", origin="P_CURVE", degraded=None, missing=None):
    return {
        "model_id": model_id,
        "version": "1.0.0",
        "feature_set_hash": "sha256:0f1e2d3c4b5a6978",
        "training_window": {"start": "2025-11-01", "end": "2026-10-31"},
        "origin_type": origin,
        "degraded_features": list(degraded or []),
        "missing_features": list(missing or []),
    }


def _item(ref, item_type, label, values, payload=None, notes=None, evidence=None):
    return {
        "item_ref": ref,
        "item_type": item_type,
        "label": label,
        "values": list(values),
        "payload": payload,
        "notes": list(notes or []),
        "evidence_refs": list(evidence or []),
    }


def _attribution(
    feature_id,
    text,
    status="VERIFIED",
    label="2026-01-01/2026-10-31",
    kind="PREDICTION_INTERVAL",
    p10="0.120",
    p90="0.180",
    level=None,
    contribution=None,
    direction=None,
    attributable=True,
    reason=None,
):
    return {
        "feature_id": feature_id,
        "contribution": contribution,
        "direction": direction,
        "text": text,
        "verification_status": status,
        "time_window": {"start": "2026-01-01", "end": "2026-10-31", "label": label},
        "confidence": {
            "kind": kind,
            "p10": p10,
            "p90": p90,
            "level": level,
            "calibration": None,
        },
        "statement": FIXED_SENTENCE,
        "attributable": attributable,
        "not_attributable_reason": reason,
    }


def _risk(code, severity, statement, target_ref, status="VERIFIED", recommendation="NONE"):
    return {
        "risk_code": code,
        "severity": severity,
        "statement": statement,
        "target_ref": target_ref,
        "verification_status": status,
        "recommendation": recommendation,
    }


def _drift(
    signal_id,
    metric_name,
    value,
    baseline_value,
    degradation,
    magnitude,
    signal_text,
    recommendation,
    evidence=None,
):
    return {
        "signal_id": signal_id,
        "window": {"start": "2026-10-07", "end": "2026-11-05"},
        "metric": {"name": metric_name, "value": value, "baseline_value": baseline_value},
        "degradation": degradation,
        "magnitude": magnitude,
        "signal_text": signal_text,
        "recommendation": recommendation,
        "evidence_refs": list(evidence or []),
    }


def _action(action_id, type_, label, target_ref, requires_approval=False):
    return {
        "action_id": action_id,
        "type": type_,
        "label": label,
        "target_ref": target_ref,
        "requires_approval": requires_approval,
    }


def _override(
    override_id,
    target_document_id,
    revision,
    reason_code,
    reason_text,
    role,
    user_id,
    item_ref,
    field,
    before,
    after,
    supersedes,
    audit_ref,
):
    return {
        "override_id": override_id,
        "target_document_id": target_document_id,
        "revision": revision,
        "reason_code": reason_code,
        "reason_text": reason_text,
        "actor": {"role": role, "user_id": user_id},
        "created_at": "2026-11-06T10:12:00+08:00",
        "scope": {"item_ref": item_ref, "field": field},
        "before": before,
        "after": after,
        "supersedes": supersedes,
        "audit_ref": audit_ref,
    }


def _projections(page_id="trader_home", block_id="tomorrow_forecast"):
    return {
        "page_blocks": [
            {
                "page_id": page_id,
                "block_id": block_id,
                "document_id": ZERO_ID,
                "output_ref": {"output_id": ZERO_ID, "contract": "#39", "status": "READY"},
            }
        ],
        "api_endpoints": [
            {"method": "GET", "path": "/api/v1/outputs/{output_id}", "document_id": ZERO_ID}
        ],
        "report_sections": [
            {"section_id": "forecast_result", "document_id": ZERO_ID, "content_hash": ZERO_ID}
        ],
    }


def _doc(
    output_type,
    status,
    code,
    scope,
    metrics,
    items,
    detail=None,
    sources=None,
    model=None,
    attributions=None,
    risks=None,
    overrides=None,
    drift_signals=None,
    notes=None,
    actions=None,
    revision=1,
    supersedes=None,
    superseded_by=None,
    payload_contract_ref=None,
    as_of=None,
    generated_at=None,
):
    return {
        "document_id": ZERO_ID,
        "output_type": output_type,
        "contract_version": CONTRACT_VERSION,
        "payload_contract_ref": payload_contract_ref,
        "scope": scope,
        "as_of": as_of or SAMPLE_AS_OF,
        "generated_at": generated_at or SAMPLE_GENERATED_AT,
        "revision": revision,
        "status": status,
        "status_reason": {
            "code": code,
            "message": "%s 状态说明" % status,
            "detail": detail or {},
        },
        "sources": list(
            sources
            if sources is not None
            else [
                {"data_source_id": "D01", "batch_id": "B20261106A", "verified": True},
                {"data_source_id": "D11", "batch_id": "B20261106A", "verified": True},
            ]
        ),
        "model": model,
        "metrics": metrics,
        "items": list(items),
        "attributions": list(attributions or []),
        "risks": list(risks or []),
        "overrides": list(overrides or []),
        "drift_signals": list(drift_signals or []),
        "notes": list(notes or []),
        "actions": list(actions or []),
        "projections": _projections(),
        "supersedes": supersedes,
        "superseded_by": superseded_by,
        "content_hash": ZERO_ID,
    }


def build_samples():
    """构造覆盖七种 status 与四类业务域的样例底稿（返回 [(名称, 底稿)]）。"""
    samples = []

    # ---------------- FORECAST / OK ----------------
    ok = _doc(
        "FORECAST",
        "OK",
        "OK",
        _scope(),
        _metrics(accuracy=[_accuracy("MAPE", 240)]),
        [
            _item("item:1", "POINT_VALUE", "次日 24 时段组合负荷点预测", [_num("128.400")]),
            _item(
                "item:2",
                "INTERVAL",
                "次日 24 时段组合负荷区间",
                [_num("110.200"), _num("128.400"), _num("146.700")],
                payload={"roles": ["P10", "P50", "P90"]},
            ),
            _item(
                "item:3",
                "SERIES",
                "24 时段组合负荷序列",
                [_num("128.400") for _ in range(24)],
                payload={"slots": list(range(1, 25))},
            ),
            _item(
                "item:4",
                "STATEMENT",
                "预测口径说明",
                [_text("本输出为次日 24 时段组合负荷预测，基于历史统计关联，非因果判定。")],
            ),
        ],
        model=_model(),
        attributions=[
            _attribution(
                "D11_weather_temp",
                "气温与次日负荷在 2026-01-01/2026-10-31 窗口内存在统计关联（已核验）；"
                "置信区间 P10 0.120 至 P90 0.180。基于历史统计关联，非因果判定",
                status="VERIFIED",
                contribution=_num("0.120", unit="ratio", precision=4),
                direction="UP",
            ),
            _attribution(
                "D12_holiday_flag",
                "节假日与次日负荷在 2026-01-01/2026-10-31 窗口内存在统计关联（未核验）；"
                "置信区间 P10 0.040 至 P90 0.060。基于历史统计关联，非因果判定",
                status="UNVERIFIED",
                contribution=_num(
                    "0.040",
                    unit="ratio",
                    precision=4,
                    status="UNVERIFIED",
                    source=_src("holiday_flag", "D12", "B20261105C"),
                ),
                direction="DOWN",
            ),
        ],
        risks=[
            _risk("DATA_GAP", "INFO", "近 30 天实测负荷入库缺口 2.1%，未超出本次输出范围", "D01")
        ],
        sources=[
            {"data_source_id": "D01", "batch_id": "B20261106A", "verified": True},
            {"data_source_id": "D11", "batch_id": "B20261106A", "verified": True},
            {"data_source_id": "D12", "batch_id": "B20261105C", "verified": False},
        ],
        actions=[
            _action("ACT-VIEW", "VIEW", "查看输出底稿", "document"),
            _action("ACT-EXPORT", "EXPORT", "导出报告快照", "document", True),
        ],
    )
    samples.append(("forecast_ok", ok))

    # ---------------- FORECAST / PARTIAL ----------------
    partial = _doc(
        "FORECAST",
        "PARTIAL",
        "PARTIAL_GAP",
        _scope(),
        _metrics(coverage="0.8750", accuracy=[_accuracy("MAPE", 221)]),
        [
            _item(
                "item:1",
                "INTERVAL",
                "次日 24 时段组合负荷区间（缺口已标注）",
                [_num("110.200"), _num("128.400"), _num("146.700")],
                payload={"roles": ["P10", "P50", "P90"]},
            ),
            _item(
                "item:2",
                "SERIES",
                "24 时段组合负荷序列（缺口时段为空值）",
                [
                    _num(None, status="MISSING") if slot in (7, 8, 9) else _num("128.400")
                    for slot in range(1, 25)
                ],
                payload={"slots": list(range(1, 25))},
            ),
            _item(
                "item:3",
                "STATEMENT",
                "缺口说明",
                [
                    _text(
                        "第 7 至第 9 时段缺少实测负荷，本输出按缺口标注给出区间，"
                        "区间宽度随缺口比例放大。"
                    )
                ],
            ),
        ],
        detail={
            "gap_slots": [7, 8, 9],
            "gap_ratio": "0.1250",
            "last_available_at": "2026-11-06T23:00:00+08:00",
        },
        model=_model(),
        risks=[
            _risk(
                "DATA_GAP",
                "LOW",
                "第 7 至第 9 时段缺 3 个数据点",
                "item:1",
                "VERIFIED",
                "DATA_COLLECTION_RECOMMENDED",
            )
        ],
        notes=["缺 3 个时段的实测负荷，请补数据后重跑预测并复核区间宽度。"],
        actions=[
            _action("ACT-VIEW", "VIEW", "查看输出底稿", "document"),
            _action("ACT-COLLECT", "COLLECT_DATA", "补齐缺口时段数据", "item:1", True),
        ],
    )
    samples.append(("forecast_partial", partial))

    # ---------------- FORECAST / DEGRADED ----------------
    degraded = _doc(
        "FORECAST",
        "DEGRADED",
        "FEATURE_DEGRADED",
        _scope(),
        _metrics(coverage="0.9167", accuracy=[_accuracy("MAPE", 220)]),
        [
            _item(
                "item:1",
                "POINT_VALUE",
                "次日 24 时段组合负荷点预测（降级）",
                [_num("128.400")],
            ),
            _item(
                "item:2",
                "INTERVAL",
                "次日 24 时段组合负荷区间（降级）",
                [_num("105.000"), _num("128.400"), _num("151.800")],
                payload={"roles": ["P10", "P50", "P90"]},
            ),
            _item(
                "item:3",
                "STATEMENT",
                "特征降级说明",
                [_text("气象特征缺失，输出标注为降级，区间整体变宽，精度影响见 metrics。")],
            ),
        ],
        detail={
            "degraded_features": ["D11_weather_temp"],
            "accuracy_impact": "缺少气温特征，区间整体变宽",
        },
        model=_model(degraded=["D11_weather_temp"], missing=["D11_weather_temp"]),
        attributions=[
            _attribution(
                "D11_weather_temp",
                "气温特征缺失占比超过阈值，该特征与次日负荷在 2026-01-01/2026-10-31 "
                "窗口内的统计关联无法给出，置信等级 LOW（未核验）。"
                "基于历史统计关联，非因果判定",
                status="UNVERIFIED",
                kind="LEVEL",
                p10=None,
                p90=None,
                level="LOW",
                contribution=None,
                direction=None,
                attributable=False,
                reason="FEATURE_MISSING_RATIO",
            )
        ],
        risks=[
            _risk(
                "DATA_GAP",
                "MEDIUM",
                "气象特征缺失，区间整体变宽",
                "D11",
                "VERIFIED",
                "DATA_COLLECTION_RECOMMENDED",
            )
        ],
        notes=["缺少气温特征，请补数据或换源后重跑；当前精度影响见 metrics。"],
        actions=[
            _action("ACT-VIEW", "VIEW", "查看输出底稿", "document"),
            _action("ACT-REVIEW", "REQUEST_REVIEW", "复核降级范围", "document", True),
        ],
    )
    samples.append(("forecast_degraded", degraded))

    # ---------------- FORECAST / REFUSED ----------------
    refused = _doc(
        "FORECAST",
        "REFUSED",
        "SOURCE_UNREACHABLE",
        _scope(),
        _metrics(coverage="0.0000", refusal="1.0000", accuracy=[_accuracy("MAPE", 0)]),
        [
            _item(
                "item:1",
                "STATEMENT",
                "拒答说明",
                [
                    _text(
                        "湖北省零售套餐公告需登录平台，当前数据源不可达，"
                        "本目标拒答，不以历史值冒充当前值。"
                    )
                ],
            )
        ],
        detail={"reason": "LOGIN_REQUIRED", "target_ref": "D09"},
        model=_model(),
        risks=[
            _risk(
                "SOURCE_UNREACHABLE",
                "HIGH",
                "零售套餐公告需登录，源不可达",
                "D09",
                "VERIFIED",
                "SOURCE_REPLACEMENT_RECOMMENDED",
            )
        ],
        notes=["零售套餐公告源不可达（需登录），本目标拒答；请换源或补数据后重跑。"],
        actions=[_action("ACT-VIEW", "VIEW", "查看拒答说明", "document")],
    )
    samples.append(("forecast_refused", refused))

    # ---------------- FORECAST / OUT_OF_RANGE ----------------
    out_of_range = _doc(
        "FORECAST",
        "OUT_OF_RANGE",
        "OUT_OF_DISTRIBUTION",
        _scope(),
        _metrics(coverage="0.0000", refusal="1.0000", accuracy=[_accuracy("MAPE", 0)]),
        [
            _item(
                "item:1",
                "STATEMENT",
                "超范围说明",
                [
                    _text(
                        "本次样本的日最高气温超出训练分布，输出标注为超出可用范围，"
                        "不给出点预测与特征贡献排序。"
                    )
                ],
            )
        ],
        detail={
            "feature_id": "D11_weather_temp",
            "distance": "12.5000",
            "train_range": ["-5.0000", "42.0000"],
        },
        model=_model(),
        attributions=[
            _attribution(
                "D11_weather_temp",
                "日最高气温落在训练分布之外，统计关联无法给出，置信等级 LOW（未核验），"
                "统计窗口 2026-01-01/2026-10-31。基于历史统计关联，非因果判定",
                status="UNVERIFIED",
                kind="LEVEL",
                p10=None,
                p90=None,
                level="LOW",
                contribution=None,
                direction=None,
                attributable=False,
                reason="OUT_OF_DISTRIBUTION",
            )
        ],
        risks=[
            _risk(
                "OUT_OF_DISTRIBUTION",
                "MEDIUM",
                "样本超出训练分布，该点无法给出特征贡献",
                "item:1",
                "VERIFIED",
                "MANUAL_REVIEW_RECOMMENDED",
            )
        ],
        notes=["样本超出可用范围，请复核特征口径或补数据后重跑。"],
        actions=[_action("ACT-VIEW", "VIEW", "查看超范围说明", "document")],
    )
    samples.append(("forecast_out_of_range", out_of_range))

    # ---------------- FORECAST / ANOMALY_EXCLUDED ----------------
    anomaly = _doc(
        "FORECAST",
        "ANOMALY_EXCLUDED",
        "DIRTY_EXCLUDED",
        _scope(),
        _metrics(coverage="0.9583", accuracy=[_accuracy("MAPE", 230)]),
        [
            _item(
                "item:1",
                "POINT_VALUE",
                "次日 24 时段组合负荷点预测（脏批次已剔除）",
                [_num("128.400")],
            ),
            _item(
                "item:2",
                "STATEMENT",
                "脏批次说明",
                [
                    _text(
                        "批次 B20261105B 检出 12 个异常跳变点，已剔除且不参与训练与回测，"
                        "剔除点数计入覆盖率。"
                    )
                ],
            ),
        ],
        detail={
            "batch_id": "B20261105B",
            "excluded_count": 12,
            "affected_scope": "PORT-2026-A 2026-11-05 全时段",
        },
        model=_model(),
        risks=[
            _risk(
                "DATA_GAP",
                "MEDIUM",
                "批次 B20261105B 有 12 个点被标脏剔除",
                "D01",
                "VERIFIED",
                "DATA_COLLECTION_RECOMMENDED",
            )
        ],
        notes=["批次 B20261105B 的脏数据已剔除，请复核入库校验结果并补数据后重跑。"],
        actions=[
            _action("ACT-VIEW", "VIEW", "查看输出底稿", "document"),
            _action("ACT-REVIEW", "REQUEST_REVIEW", "复核入库校验报告", "document", True),
        ],
    )
    samples.append(("forecast_anomaly_excluded", anomaly))

    # ---------------- DRIFT / OK ----------------
    drift = _doc(
        "DRIFT",
        "OK",
        "OK",
        _scope(),
        _metrics(accuracy=[_accuracy("MAPE", 240)]),
        [
            _item(
                "item:1",
                "STATEMENT",
                "漂移说明",
                [_text("本底稿记录近 30 天的效果衰减信号，供重训决策参考。")],
            )
        ],
        drift_signals=[
            _drift(
                "DRIFT-2026-1106-01",
                "MAPE_30D",
                _num("0.1850", unit="ratio", precision=4),
                None,
                True,
                _num("0.0320", unit="ratio", precision=4),
                "近 30 天误差较基线上升，已识别为效果衰减信号，建议重训后复核。",
                "RETRAIN_RECOMMENDED",
                ["回测批次 B20261106A"],
            )
        ],
        risks=[
            _risk(
                "DRIFT_DEGRADATION",
                "MEDIUM",
                "效果衰减信号已触发，建议重训",
                "D01",
                "VERIFIED",
                "RETRAIN_RECOMMENDED",
            )
        ],
        actions=[
            _action("ACT-VIEW", "VIEW", "查看漂移信号", "document"),
            _action("ACT-RETRAIN", "PROPOSE_RETRAIN", "发起重训建议", "document", True),
        ],
    )
    samples.append(("drift_report", drift))

    # ---------------- 四类业务域挂载 ----------------
    settlement = _doc(
        "SETTLEMENT_REPORT",
        "OK",
        "OK",
        _scope(entity_type="COMPANY", entity_id="COMPANY-2026-10"),
        _metrics(accuracy=[_accuracy("对账差异率", 128)]),
        [
            _item(
                "item:1",
                "SET",
                "结算复现汇总",
                [_num("182340.00", unit="CNY", precision=2)],
                payload={"settlement_batch": "2026-10", "line_count": 128, "unit": "CNY"},
            )
        ],
        payload_contract_ref={
            "ticket": "55",
            "contract": "settlement-replay",
            "version": "1.0.0",
        },
        actions=[
            _action("ACT-VIEW", "VIEW", "查看结算复现汇总", "document"),
            _action("ACT-EXPORT", "EXPORT", "导出对账快照", "document", True),
        ],
    )
    samples.append(("settlement_report", settlement))

    strategy = _doc(
        "STRATEGY_ADVICE",
        "OK",
        "OK",
        _scope(),
        _metrics(accuracy=[_accuracy("组合覆盖偏差", 24)]),
        [
            _item(
                "item:1",
                "SET",
                "头寸策略参考（仅供人工审批）",
                [_num("0.1500", unit="ratio", precision=4)],
                payload={"position_scope": "PORT-2026-A", "advice_kind": "reference_only"},
            )
        ],
        payload_contract_ref={
            "ticket": "56",
            "contract": "position-strategy",
            "version": "1.0.0",
        },
        actions=[_action("ACT-VIEW", "VIEW", "查看策略参考", "document")],
    )
    samples.append(("strategy_advice", strategy))

    training = _doc(
        "TRAINING_SCORE",
        "OK",
        "OK",
        _scope(entity_type="USER", entity_id="U-2026-0731"),
        _metrics(accuracy=[_accuracy("七维评分", 7)]),
        [
            _item(
                "item:1",
                "SET",
                "训战评分汇总",
                [_num("82", unit="count", precision=0)],
                payload={"exam_id": "EX-2026-11", "dimension_count": 7},
            )
        ],
        payload_contract_ref={
            "ticket": "58",
            "contract": "training-scoring",
            "version": "1.0.0",
        },
        actions=[_action("ACT-VIEW", "VIEW", "查看训战评分", "document")],
    )
    samples.append(("training_score", training))

    retail = _doc(
        "RETAIL_ANALYSIS",
        "OK",
        "OK",
        _scope(entity_type="COMPANY", entity_id="COMPANY-2026-12"),
        _metrics(accuracy=[_accuracy("档位命中率", 12)]),
        [
            _item(
                "item:1",
                "SET",
                "零售档位分析",
                [_num("0.5000", unit="ratio", precision=4)],
                payload={"tariff_period": "2026-12", "band_hit_rate": None},
            )
        ],
        payload_contract_ref={
            "ticket": "59",
            "contract": "retail-pricing",
            "version": "1.0.0",
        },
        actions=[_action("ACT-VIEW", "VIEW", "查看零售档位分析", "document")],
    )
    samples.append(("retail_analysis", retail))

    # ---------------- 人工修正链（原底稿 → 修正底稿） ----------------
    original = _doc(
        "FORECAST",
        "OK",
        "OK",
        _scope(entity_id="PORT-2026-B"),
        _metrics(accuracy=[_accuracy("MAPE", 240)]),
        [
            _item("item:1", "POINT_VALUE", "次日 24 时段组合负荷点预测", [_num("128.400")]),
            _item(
                "item:2",
                "STATEMENT",
                "预测口径说明",
                [_text("本输出为次日 24 时段组合负荷预测，基于历史统计关联，非因果判定。")],
            ),
        ],
        model=_model(),
        actions=[_action("ACT-VIEW", "VIEW", "查看输出底稿", "document")],
    )
    finalize(original)
    original_id = original["document_id"]

    revised = _doc(
        "FORECAST",
        "OK",
        "OK",
        _scope(entity_id="PORT-2026-B"),
        _metrics(accuracy=[_accuracy("MAPE", 240)]),
        [
            _item(
                "item:1",
                "POINT_VALUE",
                "次日 24 时段组合负荷点预测（人工修正后）",
                [_num("126.900")],
            ),
            _item(
                "item:2",
                "STATEMENT",
                "修正说明",
                [_text("第 7 时段实测负荷经人工修正后重算点预测，原底稿保留为 revision 1。")],
            ),
        ],
        model=_model(),
        revision=2,
        supersedes=original_id,
        overrides=[
            _override(
                "OVR-2026-1106-001",
                original_id,
                2,
                "DATA_ERROR",
                "第 7 时段实测负荷入库后发现数值异常，按修正值重算点预测",
                "交易主管",
                "u_zhangjunhao",
                "item:1",
                "value",
                _num("128.400"),
                _num("126.900"),
                original_id,
                "AUD-2026-1106-0001",
            )
        ],
        actions=[
            _action("ACT-VIEW", "VIEW", "查看输出底稿", "document"),
            _action("ACT-OVERRIDE", "SUBMIT_OVERRIDE", "提交人工修正", "item:1", True),
        ],
    )
    finalize(revised)
    revised_id = revised["document_id"]

    original["status"] = "SUPERSEDED"
    original["status_reason"] = {
        "code": "OVERRIDDEN",
        "message": "该底稿已被人工修正取代",
        "detail": {"superseding_document_id": revised_id},
    }
    original["superseded_by"] = revised_id
    original["notes"] = ["该底稿已被人工修正取代，请以修正后底稿为准并复核。"]
    finalize(original)
    finalize(revised)

    samples.append(("forecast_original_superseded", original))
    samples.append(("forecast_manual_override", revised))

    return samples


# --------------------------------------------------------------------------
# 自检：E1–E12，每条一正例一负例
# --------------------------------------------------------------------------
def _mutate(doc, path, value):
    """按 "a.b[0].c" 路径改值，返回深拷贝。"""
    target = copy.deepcopy(doc)
    node = target
    parts = path.split(".")
    for part in parts[:-1]:
        if "[" in part:
            name, index = part.split("[")
            node = node[name][int(index.rstrip("]"))]
        else:
            node = node[part]
    last = parts[-1]
    if "[" in last:
        name, index = last.split("[")
        node[name][int(index.rstrip("]"))] = value
    else:
        node[last] = value
    return target


CASES = [
    (
        "E1",
        "输出契约字段级定义与三处同源",
        "forecast_ok",
        lambda d: _mutate(d, "projections.report_sections[0].document_id", "f" * 16),
    ),
    (
        "E2",
        "任一数值可追到来源与核验状态",
        "forecast_ok",
        lambda d: _mutate(d, "items[0].values[0].source_ref", None),
    ),
    (
        "E3",
        "未核验数值不进自动判定",
        "forecast_ok",
        lambda d: _mutate(d, "items[0].values[0].verification_status", "MISSING"),
    ),
    (
        "E4",
        "缺数据与源不可达各有确定输出路径",
        "forecast_partial",
        lambda d: _mutate(d, "status_reason.detail.gap_slots", []),
    ),
    (
        "E5",
        "状态与原因码一一对应",
        "forecast_partial",
        lambda d: _mutate(d, "status_reason.code", "FEATURE_DEGRADED"),
    ),
    (
        "E6",
        "归因四项限定语与固定句",
        "forecast_ok",
        lambda d: _mutate(
            d,
            "attributions[0].text",
            "气温与次日负荷在 2026-01-01/2026-10-31 窗口内存在统计关联（已核验）；"
            "置信区间 P10 0.120 至 P90 0.180。",
        ),
    ),
    (
        "E7",
        "归因与建议文本过禁用词检查",
        "forecast_ok",
        lambda d: _mutate(
            d,
            "attributions[0].text",
            "因为气温升高，气温与次日负荷在 2026-01-01/2026-10-31 窗口内存在统计关联"
            "（已核验）；置信区间 P10 0.120 至 P90 0.180。基于历史统计关联，非因果判定",
        ),
    ),
    (
        "E8",
        "人工修正留痕与修正链",
        "forecast_manual_override",
        lambda d: _mutate(d, "overrides[0].revision", 99),
    ),
    (
        "E9",
        "覆盖率与误差指标并列且不预填",
        "forecast_ok",
        lambda d: _mutate(d, "metrics.accuracy[0].value", "0.1234"),
    ),
    (
        "E10",
        "数值格式与单位位宽",
        "forecast_ok",
        lambda d: _mutate(d, "items[0].values[0].precision", 2),
    ),
    (
        "E11",
        "动作清单不含写入真实交易系统",
        "forecast_ok",
        lambda d: _mutate(d, "actions[0].type", "EXECUTE"),
    ),
    (
        "E12",
        "业务域挂载规则",
        "settlement_report",
        lambda d: _mutate(d, "payload_contract_ref", None),
    ),
]


def _context():
    return {
        "schema": load_json(SCHEMA_PATH),
        "checker": TermChecker(load_json(TERMS_PATH)),
        "openapi": load_json(OPENAPI_PATH),
        "page_schema": load_json(PAGE_SCHEMA_PATH),
    }


def _run(doc, corpus, ctx):
    return collect_errors(
        doc,
        corpus,
        ctx["checker"],
        ctx["openapi"],
        ctx["page_schema"],
    )


def verify():
    ctx = _context()
    samples = build_samples()
    corpus = {doc["document_id"]: doc for _, doc in samples}
    by_name = {name: doc for name, doc in samples}

    sample_failures = []
    for name, doc in samples:
        errs = _run(doc, corpus, ctx)
        if errs:
            sample_failures.append((name, errs))

    results = []
    for code, label, sample_name, negate in CASES:
        detail = ""
        ok = True
        positive = by_name[sample_name]
        positive_codes = error_codes(_run(positive, corpus, ctx))
        if code in positive_codes:
            ok = False
            detail = "正例不应触发 %s" % code
        negative_codes = error_codes(_run(negate(positive), corpus, ctx))
        if code not in negative_codes:
            ok = False
            detail = "负例未触发 %s" % code
        results.append((code, label, ok, detail))

    if sample_failures:
        results = [(c, l, False, "样例自检失败") for c, l, _, _ in results]

    lines = []
    lines.append("输出契约参考实现自检（票 #39）")
    lines.append("时间基准: %s" % SAMPLE_GENERATED_AT)
    lines.append("契约: output-contract.schema.json")
    lines.append("-" * 56)
    for code, label, ok, detail in results:
        lines.append("%s %s %s" % (code, label.ljust(22), "PASS" if ok else "FAIL"))
    lines.append("-" * 56)
    passed = sum(1 for _, _, ok, _ in results if ok)
    lines.append("结果: %d/%d PASS" % (passed, len(results)))

    text = "\n".join(lines) + "\n"
    VERIFICATION_PATH.write_text(text, encoding="utf-8")
    print(text, end="")

    if sample_failures:
        print("")
        print("样例自检失败：")
        for name, errs in sample_failures:
            print("  %s" % name)
            for message in errs:
                print("    %s" % message)
    for code, label, ok, detail in results:
        if not ok and detail:
            print("  %s %s：%s" % (code, label, detail))
    return 0 if passed == len(results) else 1


def generate_samples():
    samples = build_samples()
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    for name, doc in samples:
        path = SAMPLES_DIR / ("%s.json" % name)
        path.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    with SAMPLES_JSONL.open("w", encoding="utf-8") as fh:
        for name, doc in samples:
            fh.write(json.dumps(doc, ensure_ascii=False, sort_keys=True) + "\n")
    print("已生成 %d 个样例 → %s" % (len(samples), SAMPLES_DIR))
    print("已生成 %s" % SAMPLES_JSONL)
    return 0


def check_file(path):
    ctx = _context()
    samples = build_samples()
    corpus = {doc["document_id"]: doc for _, doc in samples}
    doc = load_json(Path(path))
    errs = _run(doc, corpus, ctx)
    if errs:
        for message in errs:
            print(message)
        return 1
    print("通过：%s" % path)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="输出契约参考实现（票 #39）")
    parser.add_argument("--generate-samples", action="store_true", help="生成样例底稿")
    parser.add_argument("--verify", action="store_true", help="跑 E1–E12 自检并写 verification.txt")
    parser.add_argument("--check", metavar="FILE", help="校验单个底稿 JSON")
    args = parser.parse_args(argv)
    if args.generate_samples:
        return generate_samples()
    if args.verify:
        return verify()
    if args.check:
        return check_file(args.check)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
