#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审核点库构建与校验工具 —— 对应 GitHub Issue #2（规格 S1）。

人读的是 docs/research/*.md 的条款级基线；机器读的是 audit-point-library/**.yaml。
本工具负责两者之间的确定性转换，以及库的结构与规则校验。

第一性原理约束：
  1. 覆盖范围（covers）只能来自条款树（客观结构），不能从表格标题的自然语言括注里猜。
  2. 库里不放现场结论。库是静态基线层；判定、锚点、不符合项是运行时底稿层。
  3. 转换必须确定性可复现：同一输入两次运行产出同一字节（除生成时间外无随机量）。

子命令
  parse     Markdown 条款级基线 -> YAML 画像
  validate  校验库结构、别名路由、证据项编号、条款树一致性、快照哈希

用法
  python tools/build_library.py parse --baseline docs/research/iso9001-clause-audit-baseline.md \\
      --profile-id qms-gbt19001-2016
  python tools/build_library.py validate
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("需要 PyYAML：pip install pyyaml", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "audit-point-library"

FIELD_MAP = {
    "审核问题": "audit_question",
    "需索取的书面证据": "written_evidence",
    "需索取书面证据": "written_evidence",  # 14001/45001 基线的写法（无「的」）
    "现场核查证据": "on_site_evidence",
    "常见不符合项": "typical_nc",
}
VALID_PHOTO = ("allowed", "conditional", "not_applicable")
VALID_DIFFICULTY = ("high", "medium_high", "medium", "low")
DIFF_ORDER = {"low": 0, "medium": 1, "medium_high": 2, "high": 3}
VALID_COVERS_MODE = ("subclauses_named", "content_blocks", "single_clause", "not_in_clause_tree")
POINT_KEY_RE = re.compile(r"^\d+(?:\.\d+)*#\d+$")
CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


# ---------------------------------------------------------------- 基础工具

def read_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def dump_yaml(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# 由 tools/build_library.py parse 自动生成，请勿手工编辑。\n"
        "# 源基线：见 profile.version_meta.generated_from\n"
        "# 修改审核点请改基线 Markdown 后重新生成，或由 #5 I8 人工核定后回填。\n"
    )
    path.write_text(
        header + yaml.safe_dump(obj, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )


def snapshot_id(clauses) -> str:
    payload = json.dumps({"clauses": clauses}, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def split_items(text: str):
    """证据项拆分：按中文/英文分号切分，句内逗号不切。"""
    if not text:
        return []
    return [p.strip() for p in re.split(r"[；;]", text) if p.strip()]


def norm_difficulty(text: str):
    """把基线里的中文难度词归一到枚举。

    「中高」必须先判：它同时含「中」和「高」，先判任何一边都会把一档丢掉。
    """
    s = (text or "").strip()
    if not s:
        return None
    low = s.lower()
    if "中高" in s or "medium-high" in low or "medium_high" in low:
        return "medium_high"
    if "高" in s:
        return "high"
    if "中" in s:
        return "medium"
    if "低" in s:
        return "low"
    return None


# ---------------------------------------------------------------- 条款树

def load_tree(path: Path):
    """条款树 -> 索引：clause_no -> {title, children:[clause_no]}"""
    cfg = read_yaml(path)
    index = {}

    def walk(node):
        cn = node.get("clause_no")
        if not cn:
            return
        kids = [c.get("clause_no") for c in (node.get("children") or []) if c.get("clause_no")]
        index[cn] = {"title": node.get("title"), "children": kids}
        for c in node.get("children") or []:
            walk(c)

    for n in cfg.get("clause_tree") or []:
        walk(n)
    return cfg, index


def detect_mentioned(clause_no, scope_note, rest):
    """从标题括注里抽取「提到的编号」——只作线索，不作覆盖事实。

    自然语言括注是描述，不是结构化事实。从括注猜覆盖范围会产生错追溯：
    实测 8.6 的括注把 5.5.1（2008 版残留）当成子条款、6.2 的括注用的是 2026 版编号。
    真正的覆盖范围只能来自条款树。
    """
    mentioned = []
    if scope_note:
        mentioned = [c for c in re.findall(r"\d+(?:\.\d+)+", scope_note) if c != clause_no]
    mentioned = list(dict.fromkeys(mentioned))
    blob = f"{scope_note or ''} {rest}"
    block_count = None
    mb = re.search(r"([一二三四五六七八九十两\d]+)\s*(?:个)?(?:内容)?块", blob)
    if mb:
        tok = mb.group(1)
        block_count = CN_NUM.get(tok)
        if block_count is None and tok.isdigit():
            block_count = int(tok)
    return mentioned, block_count


def resolve_baseline(arg: str) -> Path:
    """基线路径按多种基准解析：库根、当前工作目录、仓库根。

    只认一种基准会把命令写死在某个目录下，调用方换个地方跑就找不到文件。
    """
    p = Path(arg)
    cands = [p] if p.is_absolute() else [ROOT / p, Path.cwd() / p, ROOT.parent.parent / p]
    for c in cands:
        if c.exists():
            return c
    raise SystemExit(f"找不到基线文件 {arg}；试过：{', '.join(str(c) for c in cands)}")


# ---------------------------------------------------------------- 解析基线

def _is_year(token: str) -> bool:
    return len(token) == 4 and token[:2] in ("19", "20")


def expand_clause_ref(text: str):
    """从难度表的条款格里取出条款号列表。

    基线有三种写法，转换器必须都认：
      1. 单编号：4.1
      2. 带标题：4.1 理解组织及其所处的环境（45001 基线）
      3. 区间与并列：4.1～4.3、8.6 产品的放行 / 8.1 运行策划和控制（9001 基线）
    区间只在「前缀相同、末段递增」时展开；展开不了就保留两个端点，不猜中间。
    """
    out = []
    for frag in re.split(r"[、，,/；;]", text or ""):
        frag = frag.strip()
        if not frag:
            continue
        rng = re.search(r"(\d+(?:\.\d+)*)\s*[~～至\-—]\s*(\d+(?:\.\d+)*)", frag)
        if rng:
            a, b = rng.group(1), rng.group(2)
            pa, pb = a.split("."), b.split(".")
            expanded = None
            if len(pa) == len(pb) and pa[:-1] == pb[:-1]:
                try:
                    sa, sb = int(pa[-1]), int(pb[-1])
                except ValueError:
                    sa = sb = 0
                if 0 < sa <= sb and sb - sa <= 64:
                    expanded = [".".join(pa[:-1] + [str(x)]) for x in range(sa, sb + 1)]
            out.extend(expanded if expanded else [a, b])
            continue
        for token in re.findall(r"\d+(?:\.\d+)*", frag):
            if _is_year(token):
                continue
            out.append(token)
    return out


def parse_difficulty(lines):
    """从「取证难度分级」表按条款号取难度，取不到留 None（不猜）。

    定位靠标题含「取证难度分级」，不写死节号（9001 在 8.3 节，14001/45001 是独立章）。
    列序靠表头自适应：9001 表头是「难度|条款|…」，14001/45001 是「条款号|难度|理由」。
    同一编号出现在多行时取最高档，不取最后一行。
    返回 {clause_no: {"level": 枚举或 None, "reason": 原文或 None, "basis": "baseline"|"estimated"}}。
    reason 与 basis 必须带出来：难度是人工判断，丢了理由就无法复核、补齐或推翻。
    """
    table = {}
    start = None
    for i, line in enumerate(lines):
        m = re.match(r"^#{1,6}\s*(.*)$", line)
        if not m:
            continue
        if "取证难度分级" in m.group(1):
            start = i
            break
    if start is None:
        return table
    header = None
    for line in lines[start + 1:]:
        if re.match(r"^#{1,6}\s", line):
            break
        m = re.match(r"^\|(.+)\|\s*$", line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if len(cells) < 2:
            continue
        if any("难度" in c for c in cells) and any("条款" in c for c in cells):
            header = cells
            break
    if header is None:
        return table
    level_idx = next(i for i, c in enumerate(header) if "难度" in c)
    clause_idx = next(i for i, c in enumerate(header) if "条款" in c)
    reason_idx = None
    for i, c in enumerate(header):
        if "理由" in c or "难点" in c:
            reason_idx = i
            break
    for line in lines[start + 1:]:
        if re.match(r"^#{1,6}\s", line):
            break
        m = re.match(r"^\|(.+)\|\s*$", line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if len(cells) != len(header):
            continue
        lv = norm_difficulty(cells[level_idx])
        if lv is None:
            continue
        reason = None
        if reason_idx is not None and reason_idx < len(cells):
            reason = cells[reason_idx].strip() or None
        basis = "estimated" if (reason and reason.startswith("类比推定")) else "baseline"
        for token in expand_clause_ref(cells[clause_idx]):
            prev = table.get(token)
            if prev is None or DIFF_ORDER[lv] > DIFF_ORDER[prev["level"]]:
                table[token] = {"level": lv, "reason": reason, "basis": basis}
    return table


def new_clause(text: str):
    m = re.match(r"^(\d+(?:\.\d+)*)\s*(.*)$", text.strip())
    if not m:
        return None
    clause_no, rest = m.group(1), m.group(2).strip()
    title, scope_note = rest, None
    i = rest.find("（")
    if i > 0:
        title = rest[:i].strip()
        scope_note = rest[i + 1:].rstrip("）").strip() or None
    mentioned, block_count = detect_mentioned(clause_no, scope_note, rest)
    return {
        "clause_no": clause_no,
        "title": title,
        "scope_note": scope_note,
        "covers": [],
        "covers_mode": None,
        "covers_source": None,
        "block_count": block_count,
        "mentioned_units": mentioned,
        "blocks": [],
        "photo_evidence": "not_applicable",
        "photo_note": None,
        "aliases": [],
        "points": [],
        "_fields": {},
    }


def parse_baseline(md_path: Path):
    lines = md_path.read_text(encoding="utf-8").splitlines()
    start = end = None
    start_level = None
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s*(.*)$", line)
        if not m:
            continue
        if "条款级审核点基线" in m.group(2):
            start = i
            start_level = len(m.group(1))
            break
    if start is None:
        raise SystemExit(f"未在 {md_path} 找到「条款级审核点基线」章节")
    for j in range(start + 1, len(lines)):
        m = re.match(r"^(#{1,6})\s", lines[j])
        if m and len(m.group(1)) <= start_level:
            end = j
            break
    if end is None:
        end = len(lines)

    difficulty = parse_difficulty(lines)
    clauses = []
    cur = None
    for line in lines[start:end]:
        m = re.match(r"^####\s+(.*)$", line)
        if m:
            cur = new_clause(m.group(1))
            if cur is not None:
                clauses.append(cur)
            continue
        if re.match(r"^#####\s+", line):
            cur = None  # 口径说明块（如 5.5.1）不生成审核点
            continue
        if cur is None:
            continue
        tm = re.match(r"^\|(.+)\|\s*$", line)
        if not tm:
            continue
        cells = [c.strip() for c in tm.group(1).split("|")]
        if len(cells) != 2:
            continue
        key = FIELD_MAP.get(cells[0])
        if key:
            cur["_fields"][key] = cells[1]
    return clauses, difficulty


# ---------------------------------------------------------------- 组装画像

def resolve_difficulty(clause_no, covers, table):
    """审核点难度 = 该卡自身难度与其覆盖子条款难度中的最高档。

    基线按子条款分档（如 8.5.2 标识和可追溯性为中高），而审核点按条款卡组织；
    不向上聚合，这批子条款的难度会整段丢失。
    返回 {"level","reason","basis"}，取不到返回 None（不猜）。
    """
    def as_entry(v):
        if not v:
            return None
        if isinstance(v, str):
            return {"level": v, "reason": None, "basis": "baseline"}
        return v

    best = as_entry(table.get(clause_no))
    for cv in covers or ():
        cur = as_entry(table.get(cv))
        if cur is None:
            continue
        if best is None or DIFF_ORDER[cur["level"]] > DIFF_ORDER[best["level"]]:
            best = cur
    return best


def norm_items(value):
    """基线里证据是一段分号分隔文本，共享块里已经是列表；统一成列表。"""
    if value is None:
        return []
    if isinstance(value, list):
        out = []
        for v in value:
            if isinstance(v, str):
                out.extend(split_items(v))
            elif v:
                out.append(str(v).strip())
        return [x for x in out if x]
    return split_items(value)


def make_evidence_items(written, on_site):
    """证据项编号必须连续（w1.. / o1..），判定要能回指到具体某一条。"""
    return {
        "written": [
            {"id": f"w{i}", "kind": "written", "text": t,
             "priority": "core" if i == 1 else "supporting"}
            for i, t in enumerate(written, 1)
        ],
        "on_site": [
            {"id": f"o{i}", "kind": "on_site", "text": t,
             "priority": "core" if i == 1 else "supporting"}
            for i, t in enumerate(on_site, 1)
        ],
    }


def build_points(clause, difficulty, covers=()):
    f = clause["_fields"]
    question = f.get("audit_question", "").strip()
    written = split_items(f.get("written_evidence", ""))
    on_site = split_items(f.get("on_site_evidence", ""))
    typical = split_items(f.get("typical_nc", ""))
    if not (question and written and on_site and typical):
        return []
    items = make_evidence_items(written, on_site)
    diff = resolve_difficulty(clause["clause_no"], covers, difficulty)
    return [{
        "point_key": f"{clause['clause_no']}#1",
        "point_source": "baseline",
        "audit_question": question,
        "written_evidence": written,
        "on_site_evidence": on_site,
        "typical_nc": typical,
        "evidence_items": items,
        "difficulty": diff["level"] if diff else None,
        "difficulty_reason": diff.get("reason") if diff else None,
        "difficulty_basis": diff.get("basis") if diff else None,
        "apg_ref": [],
    }]


def merge_block_points(clause, block_points, difficulty, covers):
    """把共享块内的审核点并入条款卡（规格 §4.4）。

    合并规则（规格 §4.4，inheritance=extend）：画像内已有的基线审核点内容优先，
    绝不覆盖；共享块提供三体系通用的补充审核点，按卡内序号追加。
    块点身份 = (块 id, 块内 point_key)，与卡内 point_key 命名空间相互独立，
    因此块点不会与基线点误判为同一条；已合并过的块点不重复追加。
    每个块点带 block_ref / block_point_key，用于三体系漂移检测。
    """
    if not block_points:
        return 0
    added = 0
    diff = resolve_difficulty(clause["clause_no"], covers, difficulty)
    for bp in block_points:
        bkey = bp.get("point_key")
        bid = bp.get("block_id")
        question = (bp.get("audit_question") or "").strip()
        written = norm_items(bp.get("written_evidence"))
        on_site = norm_items(bp.get("on_site_evidence"))
        typical = norm_items(bp.get("typical_nc"))
        if not (question and written and on_site and typical):
            continue
        hit = next(
            (
                p
                for p in clause["points"]
                if bid in (p.get("block_ref") or []) and p.get("block_point_key") == bkey
            ),
            None,
        )
        if hit is not None:
            continue
        seq = len(clause["points"]) + added + 1
        clause["points"].append({
            "point_key": f"{clause['clause_no']}#{seq}",
            "point_source": "block",
            "audit_question": question,
            "written_evidence": written,
            "on_site_evidence": on_site,
            "typical_nc": typical,
            "evidence_items": make_evidence_items(written, on_site),
            "difficulty": diff["level"] if diff else None,
            "difficulty_reason": diff.get("reason") if diff else None,
            "difficulty_basis": diff.get("basis") if diff else None,
            "apg_ref": [],
            "block_ref": [bid] if bid else [],
            "block_point_key": bkey,
        })
        added += 1
    return added


def cmd_parse(args):
    registry = read_yaml(LIB / "registry.yaml")
    entry = next((p for p in registry["profiles"] if p["id"] == args.profile_id), None)
    if entry is None:
        raise SystemExit(f"注册表中没有画像 {args.profile_id}")
    if args.domain and entry.get("domain") != args.domain:
        raise SystemExit(f"{args.profile_id} 的域是 {entry.get('domain')}，与 --domain {args.domain} 不符")

    md = resolve_baseline(args.baseline)
    clauses, difficulty = parse_baseline(md)

    aliases_cfg = (read_yaml(LIB / "shared/aliases.yaml").get("profiles") or {}).get(args.profile_id, {})
    alias_map = {a_["clause_no"]: a_ for a_ in aliases_cfg.get("aliases", [])}
    blocks_cfg = read_yaml(LIB / "shared/blocks.yaml")
    block_of_clause = {}
    block_points_of_clause = {}
    for b in blocks_cfg.get("blocks", []):
        applies = b.get("applies_to")
        if applies and entry.get("domain") and entry["domain"] not in applies:
            continue
        for cc in b.get("clauses", []):
            block_of_clause.setdefault(cc["clause_no"], []).append(b["id"])
            for bp in cc.get("points", []) or []:
                item = dict(bp)
                item["block_id"] = b["id"]
                block_points_of_clause.setdefault(cc["clause_no"], []).append(item)

    tree_ref = entry.get("clause_tree_ref")
    tree_index = {}
    if tree_ref:
        tp = LIB / tree_ref
        if not tp.exists():
            raise SystemExit(f"注册表指向的条款树不存在：{tree_ref}")
        _, tree_index = load_tree(tp)

    known_nums = set()
    for a_ in aliases_cfg.get("aliases", []):
        known_nums.add(a_["clause_no"])
        known_nums.update(a_.get("aliases") or [])
    known_nums.update(x["number"] for x in aliases_cfg.get("invalid_numbers", []))

    out_clauses = []
    skipped = []
    conflicts = []
    if tree_index:
        for tok in sorted(difficulty.keys()):
            if tok not in tree_index:
                conflicts.append(f"难度表引用了条款树中不存在的编号 {tok}，该难度标注将落空（版本口径疑点）")
    for c in clauses:
        tinfo = tree_index.get(c["clause_no"]) if tree_index else None
        if tree_index and tinfo is None:
            c["covers_mode"] = "not_in_clause_tree"
            conflicts.append(f'{c["clause_no"]} 不在条款树中，需补条款树或改基线')
        elif tinfo is not None:
            c["covers"] = list(tinfo["children"])
            c["covers_source"] = "clause_tree"
            if c["covers"]:
                c["covers_mode"] = "subclauses_named"
            elif c.get("block_count"):
                c["covers_mode"] = "content_blocks"
            else:
                c["covers_mode"] = "single_clause"
        else:
            c["covers_mode"] = "single_clause"
        pts = build_points(c, difficulty, c.get("covers") or [])
        if not pts:
            skipped.append(c["clause_no"])
            continue
        c["points"] = pts
        c["blocks"] = block_of_clause.get(c["clause_no"], [])
        merge_block_points(
            c,
            block_points_of_clause.get(c["clause_no"], []),
            difficulty,
            c.get("covers") or [],
        )
        alias_entry = alias_map.get(c["clause_no"])
        c["aliases"] = list(alias_entry.get("aliases", [])) if alias_entry else []
        if alias_entry and alias_entry.get("note"):
            c["alias_note"] = alias_entry["note"]
        if tree_index:
            tkids = set((tree_index.get(c["clause_no"]) or {}).get("children") or [])
            for u in c.get("mentioned_units") or []:
                if u in tkids or u in known_nums or u in tree_index:
                    continue
                conflicts.append(f'{c["clause_no"]} 括注提到 {u}，条款树与别名表中均无此编号（版本口径疑点）')
            if c.get("block_count") and tkids and c["block_count"] != len(tkids):
                conflicts.append(f'{c["clause_no"]} 基线称 {c["block_count"]} 个内容块，条款树为 {len(tkids)} 个子条款（版本口径疑点）')
        c.pop("_fields", None)
        out_clauses.append(c)

    profile = {
        "profile": {
            "id": entry["id"],
            "domain": entry["domain"],
            "standard": entry["standard"],
            "scope": entry.get("scope", {}),
            "version_meta": dict(entry.get("version_meta", {})),
        },
        "clauses": out_clauses,
    }
    profile["profile"]["version_meta"]["generated_from"] = Path(args.baseline).as_posix()
    profile["profile"]["version_meta"]["clause_tree_ref"] = tree_ref
    profile["profile"]["version_meta"]["snapshot_id"] = snapshot_id(out_clauses)
    if conflicts:
        profile["profile"]["version_meta"]["consistency_warnings"] = conflicts

    out_path = LIB / entry["file"]
    dump_yaml(profile, out_path)
    print(f"已生成 {entry['file']}")
    print(f"  条款卡 {len(out_clauses)} 张，审核点 {sum(len(c['points']) for c in out_clauses)} 条")
    print(f"  难度已标注 {sum(1 for c in out_clauses for p in c['points'] if p['difficulty'])} 条")
    units = set()
    for c in out_clauses:
        units.add(c["clause_no"])
        units.update(c.get("covers", []))
    print(f"  按条款树推导覆盖的条款/子条款单元 {len(units)} 个")
    if conflicts:
        print(f"  版本口径冲突线索 {len(conflicts)} 条（写入 version_meta.consistency_warnings）:")
        for x in conflicts:
            print("    - " + x)
    if skipped:
        print(f"  跳过（四字段不齐或非条款级）: {', '.join(skipped)}")


# ---------------------------------------------------------------- 校验

def validate(lib_dir=None):
    lib = Path(lib_dir) if lib_dir else LIB
    registry = read_yaml(lib / "registry.yaml")
    aliases_cfg = read_yaml(lib / "shared/aliases.yaml").get("profiles") or {}
    blocks_cfg = read_yaml(lib / "shared/blocks.yaml")
    block_clause = {}
    block_ids_of = {}
    block_points_of_clause = {}
    for b in blocks_cfg.get("blocks", []):
        bid = b.get("id")
        for cc in b.get("clauses", []):
            block_clause.setdefault(cc["clause_no"], set()).add(bid)
            block_ids_of.setdefault(bid, set()).add(cc["clause_no"])
            for bp in cc.get("points", []) or []:
                block_points_of_clause.setdefault(cc["clause_no"], []).append((bid, bp.get("point_key")))

    errors, warns, infos = [], [], []
    seen_ids = set()
    for entry in registry.get("profiles", []):
        pid = entry.get("id", "<缺 id>")
        if pid in seen_ids:
            errors.append(f"{pid}: 注册表 id 重复")
        seen_ids.add(pid)
        if entry.get("status") == "pending":
            infos.append(f"{pid}: 画像尚未产出（status=pending，预期文件 {entry.get('file')}），跳过内容校验")
            continue
        fp = lib / entry.get("file", "")
        if not fp.exists():
            errors.append(f"{pid}: 画像文件缺失 {entry.get('file')}")
            continue
        prof = read_yaml(fp)
        p = prof.get("profile", {})
        if p.get("id") != pid:
            errors.append(f"{pid}: 画像文件内 id={p.get('id')} 与注册表不一致")
        if p.get("domain") != entry.get("domain"):
            errors.append(f"{pid}: domain 与注册表不一致")
        if p.get("standard", {}).get("designation") != entry.get("standard", {}).get("designation"):
            errors.append(f"{pid}: standard.designation 与注册表不一致")
        sources = entry.get("standard", {}).get("official_sources") or []
        if not sources:
            errors.append(f"{pid}: 注册表缺 official_sources（V8）")
        for s in sources:
            if not s.get("url") or not s.get("accessed"):
                errors.append(f"{pid}: official_sources 条目缺 url/accessed（V8）")

        domain = entry.get("domain")
        apg_empty = 0
        acfg = aliases_cfg.get(pid, {})
        invalid = {x["number"] for x in acfg.get("invalid_numbers", [])}
        known_alias = set(invalid)
        for a_ in acfg.get("aliases") or []:
            known_alias.add(a_["clause_no"])
            known_alias.update(a_.get("aliases") or [])
        tree_index = {}
        tree_ref = entry.get("clause_tree_ref")
        if tree_ref:
            tp = lib / tree_ref
            if not tp.exists():
                errors.append(f"{pid}: 条款树文件缺失 {tree_ref}（V10）")
            else:
                _, tree_index = load_tree(tp)

        clause_nos = set()
        for c in prof.get("clauses", []):
            cn = c.get("clause_no")
            if not cn:
                errors.append(f"{pid}: 有条款卡缺 clause_no")
                continue
            if cn in clause_nos:
                errors.append(f"{pid}: 条款号重复 {cn}")
            clause_nos.add(cn)
            if cn in invalid:
                errors.append(f"{pid}: 条款号 {cn} 出现在 invalid_numbers（V5）")
            if c.get("photo_evidence") not in VALID_PHOTO:
                errors.append(f"{pid}:{cn}: photo_evidence 非法 {c.get('photo_evidence')}（V4）")
            for al in c.get("aliases", []):
                if al in clause_nos:
                    errors.append(f"{pid}: 别名 {al} 与真实条款号冲突（V5）")
            if tree_index:
                if cn not in tree_index:
                    errors.append(f"{pid}:{cn}: 条款号不在条款树中（V10）")
                else:
                    tkids = set(tree_index[cn]["children"])
                    for cv in c.get("covers", []):
                        if cv not in tkids:
                            errors.append(f"{pid}:{cn}: covers 含条款树中不存在的 {cv}（V10）")
                    for u in c.get("mentioned_units", []) or []:
                        if u in tkids:
                            continue
                        if u in invalid:
                            warns.append(f"{pid}:{cn}: 基线提到 {u}，该编号在当前标准版本下无效（invalid_numbers），疑似引用了另一版本口径（V11）")
                            continue
                        if u in known_alias or u in tree_index:
                            continue
                        warns.append(f"{pid}:{cn}: 基线提到 {u}，条款树与别名表中均无（V11 版本口径冲突，待 #16 R4 核定）")
                    if c.get("block_count") is not None and tkids and c["block_count"] != len(tkids):
                        warns.append(f"{pid}:{cn}: 基线称 {c['block_count']} 个内容块，条款树为 {len(tkids)} 个子条款（V11）")
            mode = c.get("covers_mode")
            if mode not in VALID_COVERS_MODE:
                errors.append(f"{pid}:{cn}: covers_mode 非法 {mode}（V9）")
            for cv in c.get("covers", []):
                if cv in invalid:
                    errors.append(f"{pid}:{cn}: covers 含无效编号 {cv}（V5）")
            if mode == "subclauses_named" and not c.get("covers"):
                errors.append(f"{pid}:{cn}: covers_mode=subclauses_named 但 covers 为空（V9）")
            if mode == "content_blocks" and not c.get("block_count"):
                warns.append(f"{pid}:{cn}: 内容块模式但缺 block_count，需人工核定（V9）")
            for bid in c.get("blocks", []):
                if cn not in block_ids_of.get(bid, set()):
                    errors.append(f"{pid}:{cn}: 引用了不含该条款的块 {bid}（V6）")
                if bid not in block_clause.get(cn, set()):
                    errors.append(f"{pid}:{cn}: 块 {bid} 未登记该条款（V6）")
            pkeys = set()
            for pt in c.get("points", []):
                pk = pt.get("point_key")
                if pk in pkeys:
                    errors.append(f"{pid}:{cn}: point_key 重复 {pk}（V2）")
                pkeys.add(pk)
                if not POINT_KEY_RE.match(str(pk or "")):
                    errors.append(f"{pid}:{cn}:{pk}: point_key 形态非法，应为 <条款号>#<序号>（V12）")
                elif not str(pk).startswith(str(cn) + "#"):
                    errors.append(f"{pid}:{cn}:{pk}: point_key 前缀与所在条款号不一致（V12）")
                for f in ("audit_question", "written_evidence", "on_site_evidence", "typical_nc"):
                    if not pt.get(f):
                        errors.append(f"{pid}:{cn}:{pk}: 缺字段 {f}（V3）")
                if pt.get("difficulty") not in VALID_DIFFICULTY + (None,):
                    errors.append(f"{pid}:{cn}:{pk}: difficulty 非法（V4）")
                if pt.get("difficulty") is None:
                    warns.append(f"{pid}:{cn}:{pk}: 缺难度标注（V13，取不到就留空，不猜）")
                elif pt.get("difficulty_basis") == "estimated":
                    warns.append(f"{pid}:{cn}:{pk}: 难度为类比推定（{pt.get('difficulty')}），待 #16 R4 复核（V13）")
                if not pt.get("apg_ref"):
                    apg_empty += 1
                items = pt.get("evidence_items", {})
                for kind, prefix in (("written", "w"), ("on_site", "o")):
                    seq = items.get(kind, [])
                    for i, it in enumerate(seq, 1):
                        if it.get("id") != f"{prefix}{i}":
                            errors.append(f"{pid}:{cn}:{pk}: 证据项编号错序 {it.get('id')}（V3）")
                        if it.get("priority") not in ("core", "supporting"):
                            errors.append(f"{pid}:{cn}:{pk}: 证据项 priority 非法（V4）")
                    if len(seq) != len(pt.get(f"{kind}_evidence", [])):
                        errors.append(f"{pid}:{cn}:{pk}: evidence_items 与 {kind}_evidence 数量不一致（V3）")
            seqs = sorted(
                int(str(pk).split("#")[1])
                for pk in pkeys
                if POINT_KEY_RE.match(str(pk or ""))
            )
            if seqs and seqs != list(range(1, len(seqs) + 1)):
                errors.append(f"{pid}:{cn}: point_key 序号不连续 {seqs}（V12）")
            merged_block_points = {
                (bid, p.get("block_point_key"))
                for p in c.get("points", [])
                for bid in (p.get("block_ref") or [])
            }
            for bid, bpkey in block_points_of_clause.get(cn, []):
                if bid not in (c.get("blocks") or []):
                    errors.append(f"{pid}:{cn}: 块 {bid} 登记了该条款但本卡未引用（V15）")
                elif (bid, bpkey) not in merged_block_points:
                    errors.append(f"{pid}:{cn}: 块 {bid} 的审核点 {bpkey} 未合并进本卡（V15）")
        sid = p.get("version_meta", {}).get("snapshot_id")
        if not sid:
            errors.append(f"{pid}: 缺 version_meta.snapshot_id")
        elif sid != snapshot_id(prof.get("clauses", [])):
            errors.append(f"{pid}: snapshot_id 与当前内容不符（内容被手改或过期）")
        npts = sum(len(c.get("points", [])) for c in prof.get("clauses", []))
        if apg_empty:
            warns.append(f"{pid}: {apg_empty} 条审核点 apg_ref 为空，待 #16 R4 回填（V14）")
        infos.append(f"{pid}: {len(prof.get('clauses', []))} 张条款卡 / {npts} 条审核点 / 快照 {str(sid)[7:19]}")

    print("== 校验报告 ==")
    for i in infos:
        print("  [信息]", i)
    for w in warns:
        print("  [警告]", w)
    for e in errors:
        print("  [错误]", e)
    print(f"\n合计：{len(errors)} 个错误，{len(warns)} 个警告")
    return 1 if errors else 0


def main():
    ap = argparse.ArgumentParser(description="审核点库构建与校验工具")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("parse", help="Markdown 基线 -> YAML 画像")
    pp.add_argument("--baseline", required=True, help="基线 Markdown 路径")
    pp.add_argument("--profile-id", required=True, help="画像 id")
    pp.add_argument("--domain", default=None, help="与注册表核对用")
    pp.set_defaults(func=cmd_parse)
    vv = sub.add_parser("validate", help="校验库")
    vv.add_argument("--lib-dir", default=None, help="库目录（默认 audit-point-library/），供负例测试指定")
    vv.set_defaults(func=lambda a: validate(a.lib_dir))
    args = ap.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
