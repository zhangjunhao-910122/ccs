"""工程结构与运行约定参考实现（票 #32）。

零依赖：用 Python 标准库 ast 解析 apps/ 下各包的导入与源码形态，
把 SPEC.md 的目录布局、层间依赖方向、禁止项、AI 控制边界、
配置分环境与密钥规则变成可自动发现的检查项。

检查项编号与 SPEC.md §3.3 对齐：
  S1 目录与包齐备        S2 层间依赖矩阵      S3 具名禁止项
  S4 跨包导入须为顶层包名 S5 密钥与连接串扫描   S6 配置分环境
  S7 AI 控制边界落点      S8 依赖清单一致性
  S9 第三方依赖白名单     S10 禁 ORM 推 DDL

用法:
  python docs/spec/engineering/reference_structure.py            # 查真实 apps/
  python docs/spec/engineering/reference_structure.py --self     # 跑 E1-E10 自检
  python docs/spec/engineering/reference_structure.py --root DIR # 查指定目录
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[2]
APPS_DIR = REPO_ROOT / "apps"
SAMPLES_DIR = SPEC_DIR / "samples"

# apps/<目录> -> 顶层包名
PKG_OF_DIR = {
    "contracts": "trader_contracts",
    "data": "trader_data",
    "gateway": "trader_gateway",
    "model": "trader_model",
    "core": "trader_core",
    "agents": "trader_agents",
    "api": "trader_api",
}
APP_DIRS = list(PKG_OF_DIR) + ["web"]

# 层间允许依赖（SPEC §3.1）
ALLOWED = {
    "trader_contracts": set(),
    "trader_data": {"trader_contracts"},
    "trader_gateway": {"trader_contracts"},
    "trader_model": {"trader_contracts"},
    "trader_core": {"trader_contracts", "trader_data"},
    "trader_agents": {
        "trader_contracts",
        "trader_core",
        "trader_data",
        "trader_gateway",
        "trader_model",
    },
    "trader_api": {
        "trader_contracts",
        "trader_agents",
        "trader_core",
        "trader_data",
        "trader_gateway",
    },
}
ALL_PKGS = set(PKG_OF_DIR.values())
DIR_OF_PKG = {v: k for k, v in PKG_OF_DIR.items()}

# SPEC §3.2 具名禁止项：编号 -> (说明, 违规条件)
FORBIDDEN = {
    "S3-1-core-no-model-layer": (
        "trader_core 不得依赖模型层/智能体层/访问层",
        ("trader_core", {"trader_model", "trader_agents", "trader_api"}),
    ),
    "S3-2-data-no-upper-layer": (
        "trader_data 不得依赖任何应用层",
        ("trader_data", ALL_PKGS - {"trader_contracts"}),
    ),
    "S3-3-gateway-no-business": (
        "trader_gateway 不得依赖业务层与数据层",
        ("trader_gateway", {"trader_core", "trader_data", "trader_model", "trader_agents", "trader_api"}),
    ),
    "S3-4-model-no-business": (
        "trader_model 不得依赖业务层",
        ("trader_model", {"trader_core", "trader_agents", "trader_api"}),
    ),
    "S3-5-agents-no-http": (
        "trader_agents 不得依赖访问层",
        ("trader_agents", {"trader_api"}),
    ),
    "S3-6-no-frontend-import": (
        "任何包不得导入前端代码",
        (None, set()),
    ),
}

REQUIRED_ENVS = ("dev", "prod")
ENVIRONMENTS = ("dev", "test", "staging", "prod")

# S5 密钥与连接串
SECRET_PATTERNS = [
    (re.compile(r"""(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?key|private[_-]?key|token)\b\s*[:=]\s*["'][^"']{6,}["']"""), "疑似明文凭据"),
    (re.compile(r"""(?i)postgres(ql)?://[^\s'"]+:[^\s'"]+@"""), "数据库连接串含口令"),
    (re.compile(r"""(?i)mysql://[^\s'"]+:[^\s'"]+@"""), "数据库连接串含口令"),
    (re.compile(r"""(?i)\bbearer\s+[A-Za-z0-9\-._]{20,}"""), "疑似 Bearer 令牌"),
]

# S6 默认值兜底（缺环境即应失败）
DEFAULT_FALLBACK = re.compile(
    r"""(?i)os\.(environ\.get|getenv)\(\s*["'][A-Z0-9_]*(ENV|ENVIRONMENT|MODE|PROFILE)["']\s*,\s*["'][^"']+["']"""
)

# S7 AI 控制边界规则：(编号, 说明, 只在这些目录里查, 正则)
AI_RULES = [
    ("S7-AI-1-no-trading-write", "不得写入真实交易系统",
     {"core", "agents", "api"},
     re.compile(r"""\b(place_order|submit_order|cancel_order|insert_order|write_to_trading_system|order_submit|trade_submit)\b""")),
    ("S7-AI-2-audit-append-only", "不得改写或删除历史数据与审计日志",
     {"core", "agents", "api", "data"},
     re.compile(r"""\b(delete_audit|update_audit|truncate_audit|drop_audit|rewrite_audit|delete_history|update_history|drop_history)\b""")),
    ("S7-AI-3-no-hardcoded-limit", "额度与风控阈值不得硬编码",
     {"core", "agents", "api", "data", "gateway", "model"},
     re.compile(r"""(?i)^\s*(quota|.*_quota|limit|.*_limit|threshold|.*_threshold)\s*[:=]\s*[-+]?[0-9]+(\.[0-9]+)?\s*$""")),
    ("S7-AI-4-no-future-data", "不得越过 forecast_origin 取数",
     {"core", "agents"},
     re.compile(r"""\b(fetch_training_window|read_after_origin|use_future_data|future_rows|ignore_forecast_origin)\b""")),
    ("S7-AI-6-no-raw-sensitive", "业务层不得直引高敏原始数据",
     {"core", "agents"},
     re.compile(r"""\b(raw_user_load|raw_meter|user_detail_rows|meter_point_detail|raw_contract_rows)\b""")),
    ("S7-AI-7-no-suggestion-as-value", "模型建议类型不得出现在数值通道",
     {"core"},
     re.compile(r"""\bSuggestion\b""")),
]

# S7-AI-5 出域唯一出口：除 gateway 与 model 外不得出现公网外呼
EGRESS = re.compile(r"\b(requests|httpx|aiohttp)\.(get|post|put|patch|delete)\b|\burllib\.request\b|\burlopen\b")
# S7-AI-9 用途授权：网关出域函数签名必须带 purpose 与 grant_id
PURPOSE_SIG = re.compile(r"def\s+egress\w*\s*\(([^)]*)\)")

# §8 技术栈登记表放行的第三方包（唯一权威白名单）。
# 不在名单内的第三方包必须先在 §8 登记、再改这里；不得靠“大家都这么写”放行。
# 名字取 PyPI 分发名，与 requirements.txt 同名空间（导入名经 PKG_ALIAS 归一）。
ALLOWED_THIRD_PARTY = {
    # 后端与运行时（§8 后端 = FastAPI）
    "fastapi", "starlette", "pydantic", "uvicorn",
    # 数据库访问（§8 = SQLAlchemy 2.0 Core，只做参数化查询）
    "sqlalchemy", "psycopg",
    # 出域调用（§8 = httpx，只允许出现在 trader_gateway 与 trader_model）
    "httpx",
    # 数据与配置
    "numpy", "pandas", "pyyaml",
    # 测试
    "pytest",
}

# §4.2 模型调用只许从 trader_agents 发起且必经安全网关：
# trader_core / trader_agents 不得直接持有 HTTP 客户端，出域能力只在网关与模型包。
HTTP_CLIENT_LIBS = {"httpx", "requests", "aiohttp", "urllib3", "urllib", "http"}
NO_HTTP_CLIENT_PKGS = {"core", "agents"}

# §3.1 + #25：DDL 权威唯一 = storage.schema.json，任何包都不得由 ORM 推表结构。
ORM_DDL = re.compile(
    r"(^|[^A-Za-z0-9_])(create_all|drop_all)|Base[.]metadata|metadata[.]create_all"
)
PY_STDLIB = set(sys.stdlib_module_names) | {"__future__"}

# ---------- 基础设施 ----------

def _v(rule, where, message, level="error"):
    return {"rule": rule, "where": str(where), "message": message, "level": level}


def python_files(root: Path):
    if not root.exists():
        return
    for p in sorted(root.rglob("*.py")):
        if {"__pycache__", "node_modules", ".venv", ".git"} & set(p.parts):
            continue
        yield p


def text_files(root: Path):
    suffix_ok = {".py", ".toml", ".yaml", ".yml", ".json", ".env", ".ini", ".cfg", ".txt", ".js", ".ts"}
    if not root.exists():
        return
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if {"__pycache__", "node_modules", ".venv", ".git", "dist", "build"} & set(p.parts):
            continue
        if p.suffix.lower() in suffix_ok or p.name in {".env", "requirements.txt"}:
            yield p


def parse_imports(path: Path):
    """返回 (模块名列表, 错误信息)。只收绝对导入；包内相对导入视为允许。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except SyntaxError as exc:
        return [], f"语法错误：{exc}"
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue
            if node.module:
                mods.append(node.module)
    return mods, None


def _importer(root: Path, path: Path):
    rel = path.relative_to(root)
    d = rel.parts[0] if rel.parts else ""
    return d, PKG_OF_DIR.get(d)


# ---------- S1 目录与包齐备 ----------

def check_layout(root: Path):
    out = []
    if not root.exists():
        return [_v("S1", root, "apps/ 目录尚未建立（骨架落地前不计违规）", "info")]
    for d in APP_DIRS:
        if not (root / d).is_dir():
            out.append(_v("S1", root / d, "缺少应用目录"))
    for d, pkg in PKG_OF_DIR.items():
        base = root / d
        if not base.is_dir():
            continue
        if not (base / "src" / pkg / "__init__.py").is_file():
            out.append(_v("S1", base, f"缺少 python 包入口 src/{pkg}/__init__.py"))
        if not (base / "requirements.txt").is_file():
            out.append(_v("S1", base, "缺少 requirements.txt（依赖锁定清单）"))
        if not (base / "pyproject.toml").is_file():
            out.append(_v("S1", base, "缺少 pyproject.toml"))
        if not (base / "README.md").is_file():
            out.append(_v("S1", base, "缺少 README.md"))
    if (root / "web").is_dir() and not (root / "web" / "package.json").is_file():
        out.append(_v("S1", root / "web", "缺少 package.json"))
    return out


# ---------- S6 配置分环境 ----------

def check_configs(root: Path):
    out = []
    for d in PKG_OF_DIR:
        base = root / d / "config"
        if not base.is_dir():
            continue
        for env in REQUIRED_ENVS:
            p = base / env
            if not p.is_dir():
                out.append(_v("S6", p, f"缺少 {env} 环境配置目录"))
            elif not any(x.is_file() for x in p.iterdir()):
                out.append(_v("S6", p, f"{env} 配置目录为空"))
    for p in python_files(root):
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in DEFAULT_FALLBACK.finditer(text):
            line = text[: m.start()].count("\n") + 1
            out.append(_v("S6", f"{p}:{line}", "环境变量带默认值兜底：环境缺失必须启动失败，不得回落默认值"))
    return out


# ---------- S5 密钥与连接串 ----------

def check_secrets(root: Path):
    out = []
    for p in text_files(root):
        text = p.read_text(encoding="utf-8", errors="replace")
        for pattern, desc in SECRET_PATTERNS:
            m = pattern.search(text)
            if m:
                line = text[: m.start()].count("\n") + 1
                out.append(_v("S5", f"{p}:{line}", f"{desc}：{m.group(0)[:40]}…"))
    return out


# ---------- S2/S3/S4 层间依赖 ----------

def check_dependencies(root: Path):
    out = []
    for p in python_files(root):
        d, pkg = _importer(root, p)
        if pkg is None:
            continue
        mods, err = parse_imports(p)
        if err:
            out.append(_v("S2", p, err))
            continue
        allowed = ALLOWED.get(pkg, set())
        for full in mods:
            top = full.split(".")[0]
            if top in ALL_PKGS:
                if "." in full and top != pkg:
                    out.append(_v("S4", p, f"跨包导入只允许顶层包名：{full}"))
                if top == pkg:
                    continue
                if top not in allowed:
                    out.append(_v("S2", p, f"{pkg} 不得依赖 {top}（允许：{sorted(allowed) or '无'}）"))
                    for rule, (desc, (owner, targets)) in FORBIDDEN.items():
                        if owner == pkg and top in targets:
                            out.append(_v(rule, p, desc))
            elif top in {"web", "trader_web"}:
                out.append(_v("S3-6-no-frontend-import", p, "任何包不得导入前端代码"))
            elif top.startswith("trader_"):
                out.append(_v("S2", p, f"未知内部包名：{top}"))
    return out


# ---------- S7 AI 控制边界 ----------

AI_RULES_EFF = []
for _rule, _desc, _dirs, _pat in AI_RULES:
    _src = _pat.pattern
    if _rule == "S7-AI-6-no-raw-sensitive":
        _src = r"\b(raw_user_load|raw_meter|user_detail_rows|meter_point_detail|raw_contract_rows)\b"
    AI_RULES_EFF.append((_rule, _desc, _dirs, re.compile(_src, re.MULTILINE)))


def check_ai_boundary(root: Path):
    out = []
    for p in python_files(root):
        d, pkg = _importer(root, p)
        text = p.read_text(encoding="utf-8", errors="replace")
        for rule, desc, dirs, pattern in AI_RULES_EFF:
            if d not in dirs:
                continue
            for m in pattern.finditer(text):
                line = text[: m.start()].count("\n") + 1
                out.append(_v(rule, f"{p}:{line}", desc))
        if d in NO_HTTP_CLIENT_PKGS:
            mods, err = parse_imports(p)
            if not err:
                for full in mods:
                    top = full.split(".")[0]
                    if top in HTTP_CLIENT_LIBS:
                        out.append(_v(
                            "S7-AI-8-no-http-client-in-core", p,
                            f"{pkg} 不得直接持有 HTTP 客户端（{full}）：模型调用只许经 trader_agents → trader_model / trader_gateway",
                        ))
        if d not in {"gateway", "model"}:
            for m in EGRESS.finditer(text):
                line = text[: m.start()].count("\n") + 1
                out.append(_v("S7-AI-5-egress-single-exit", f"{p}:{line}", "出域调用只允许出现在 gateway 与 model 包"))
        if d == "gateway":
            for m in PURPOSE_SIG.finditer(text):
                sig = m.group(1)
                if "purpose" not in sig or "grant_id" not in sig:
                    out.append(_v("S7-AI-9-purpose-grant", p, "网关出域函数签名必须带 purpose 与 grant_id"))
    return out


# ---------- S8 依赖清单一致性 ----------

PKG_ALIAS = {"yaml": "pyyaml", "sklearn": "scikit-learn", "pil": "pillow", "dateutil": "python-dateutil"}


def check_manifests(root: Path):
    out = []
    for d, pkg in PKG_OF_DIR.items():
        base = root / d
        if not base.is_dir():
            continue
        manifest = base / "requirements.txt"
        declared = set()
        if manifest.is_file():
            for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.split("#")[0].strip()
                if not line:
                    continue
                name = re.split(r"[<>=!~\[\s;]", line)[0].strip().lower()
                if name:
                    declared.add(PKG_ALIAS.get(name, name))
        used = set()
        for p in python_files(base):
            mods, err = parse_imports(p)
            if err:
                continue
            for full in mods:
                top = full.split(".")[0]
                if top in PY_STDLIB or top in ALL_PKGS or top.startswith("trader_"):
                    continue
                used.add(PKG_ALIAS.get(top.lower(), top.lower()))
        for u in sorted(used - declared):
            out.append(_v("S8", base, f"导入未在 requirements.txt 声明的第三方包：{u}"))
        for u in sorted(used - ALLOWED_THIRD_PARTY):
            out.append(_v("S9", base, f"导入未在 §8 技术栈登记表内的第三方包：{u}（先登记再改白名单）"))
    return out


# ---------- S10 禁 ORM 推 DDL ----------


def check_orm_ddl(root: Path):
    out = []
    for p in python_files(root):
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in ORM_DDL.finditer(text):
            line = text[: m.start()].count(chr(10)) + 1
            out.append(_v("S10", f"{p}:{line}", "DDL 权威唯一 = storage.schema.json，任何包都不得由 ORM 推表结构"))
    return out


ALL_CHECKS = (
    ("S1", check_layout),
    ("S2", check_dependencies),
    ("S3", check_dependencies),
    ("S4", check_dependencies),
    ("S5", check_secrets),
    ("S6", check_configs),
    ("S7", check_ai_boundary),
    ("S8", check_manifests),
    ("S9", check_manifests),
    ("S10", check_orm_ddl),
)


def scan(root: Path):
    findings = []
    for _, fn in ALL_CHECKS:
        findings.extend(fn(root))
    return findings

# ---------- 自检 E1-E10 ----------

AI_RULE_IDS = (
    "S7-AI-1-no-trading-write",
    "S7-AI-2-audit-append-only",
    "S7-AI-3-no-hardcoded-limit",
    "S7-AI-4-no-future-data",
    "S7-AI-5-egress-single-exit",
    "S7-AI-6-no-raw-sensitive",
    "S7-AI-7-no-suggestion-as-value",
    "S7-AI-8-no-http-client-in-core",
    "S7-AI-9-purpose-grant",
)


def _hit_rules(findings):
    return {f["rule"] for f in findings if f.get("level") == "error"}


def run_checks():
    """用 samples/apps（合规）与 samples/apps_bad（故意违规）验证 S1-S10 真的能发现违规。

    返回 [(检查项名, 是否通过, 说明), ...]
    """
    ok_root = SAMPLES_DIR / "apps"
    bad_root = SAMPLES_DIR / "apps_bad"
    results = []

    ok_errors = [f for f in scan(ok_root) if f.get("level") == "error"]
    results.append((
        "E1 合规骨架零违规（S1-S10 全部通过）",
        not ok_errors,
        "合规样例 apps/ 被报 error 级问题 %d 条" % len(ok_errors),
    ))

    bad_findings = scan(bad_root)
    hits = _hit_rules(bad_findings)

    def check(name, rules, note):
        missing = [r for r in rules if r not in hits]
        results.append((name, not missing, note if not missing else ("未触发：" + ", ".join(missing))))

    check("E2 违规骨架触发 S2（层间依赖矩阵）", ("S2",), "core 导入 model 层被矩阵拦下")
    check("E3 违规骨架触发 S3-1（具名禁止项）", ("S3-1-core-no-model-layer",), "trader_core 不得依赖模型层")
    check("E4 违规骨架触发 S4（跨包导入须用顶层包名）", ("S4",), "from trader_core.engine.settlement import ...")
    check("E5 违规骨架触发 S5（明文凭据）", ("S5",), "password = ... 被扫出")
    check("E6 违规骨架触发 S6（配置分环境）", ("S6",), "环境变量默认值兜底（APP_MODE 缺省回落 dev）")
    check("E7 违规骨架触发 S7 全部 9 条 AI 控制边界", AI_RULE_IDS, "写交易/改历史/硬阈值/取未来数据/外呼/直引高敏/建议混数值/核心层直持 HTTP 客户端/出域无授权")
    check("E8 违规骨架触发 S8（依赖清单一致性）", ("S8",), "导入 numpy/requests 未在 requirements.txt 声明")
    check("E9 违规骨架触发 S9（第三方依赖白名单）", ("S9",), "导入 sklearn 未在 §8 技术栈登记表内")
    check("E10 违规骨架触发 S10（禁 ORM 推 DDL）", ("S10",), "data 侧 Base.metadata.create_all 与 storage.schema.json 争权威")

    return results


def write_verification(results):
    lines = [
        "工程结构静态检查自检（票 #32）",
        "时间基准: 2026-11-06",
        "契约文件: docs/spec/engineering/SPEC.md",
        "检查器: docs/spec/engineering/reference_structure.py",
        "样例: docs/spec/engineering/samples/apps（合规） 与 docs/spec/engineering/samples/apps_bad（故意违规）",
        "",
    ]
    passed = 0
    for name, ok, note in results:
        passed += 1 if ok else 0
        lines.append(("%s: %s" % (name, "PASS" if ok else "FAIL")) + ("" if ok else "  <- " + note))
    lines.append("")
    lines.append("结果: %d/%d PASS" % (passed, len(results)))
    (SPEC_DIR / "verification.txt").write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
    return passed, len(results)


def main(argv=None):
    ap = argparse.ArgumentParser(description="工程结构与运行约定静态检查器（票 #32）")
    ap.add_argument("--self", action="store_true", help="跑 E1-E8 自检并写 verification.txt")
    ap.add_argument("--root", default=None, help="检查指定目录（默认仓库 apps/）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出发现项")
    args = ap.parse_args(argv)

    if args.self:
        results = run_checks()
        passed, total = write_verification(results)
        for name, ok, note in results:
            print(("[PASS] " if ok else "[FAIL] ") + name + ("" if ok else "  <- " + note))
        print("结果: %d/%d PASS" % (passed, total))
        return 0 if passed == total else 1

    root = Path(args.root).resolve() if args.root else APPS_DIR
    findings = scan(root)
    if args.json:
        print(json.dumps(findings, ensure_ascii=False, indent=2))
    else:
        for f in findings:
            print("[%s] %s %s: %s" % (f.get("level", "error"), f["rule"], f["where"], f["message"]))
        errs = len([f for f in findings if f.get("level") == "error"])
        print("发现 %d 项（其中 error %d 项）" % (len(findings), errs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
