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
    "现场核查证据": "on_site_evidence",
    "常见不符合项": "typical_nc",
}
VALID_PHOTO = ("allowed", "conditional", "not_applicable")
VALID_DIFFICULTY = ("high", "medium", "low")
VALID_COVERS_MODE = ("subclauses_named", "content_blocks", "single_clause", "not_in_clause_tree")
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
    s = (text or "").strip()
    if "低" in s:
        return "low"
    if "中" in s:
        return "medium"
    if "高" in s:
        return "high"
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


# ---------------------------------------------------------------- 解析基线

def parse_difficulty(lines):
    """从第 8.3 节「取证难度分级」表按条款号取难度，取不到留 None（不猜）。"""
    table = {}
    inside = False
    for line in lines:
        if re.match(r"^###\s*8\.3", line):
            inside = True
            continue
        if inside and re.match(r"^#{2,3}\s", line):
            break
        if not inside:
            continue
        m = re.match(r"^\|(.+)\|\s*$", line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if len(cells) != 4:
            continue
        level = norm_difficulty(cells[0])
        if level is None:
            continue
        for token in re.findall(r"\d+(?:\.\d+)*", cells[1]):
            if len(token) == 4 and token[:2] in ("19", "20"):
                continue  # 年份不是条款号
            table[token] = level
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
    for i, line in enumerate(lines):
        if start is None and re.match(r"^##\s*5[.\s]", line):
            start = i
        elif start is not None and re.match(r"^##\s*6[.\s]", line):
            end = i
            break
    if start is None:
        raise SystemExit(f"未在 {md_path} 找到第 5 节「条款级审核点基线」")
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

def build_points(clause, difficulty):
    f = clause["_fields"]
    question = f.get("audit_question", "").strip()
    written = split_items(f.get("written_evidence", ""))
    on_site = split_items(f.get("on_site_evidence", ""))
    typical = split_items(f.get("typical_nc", ""))
    if not (question and written and on_site and typical):
        return []
    items = {
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
    return [{
        "point_key": clause["clause_no"],
        "audit_question": question,
        "written_evidence": written,
        "on_site_evidence": on_site,
        "typical_nc": typical,
        "evidence_items": items,
        "difficulty": difficulty.get(clause["clause_no"]),
        "apg_ref": [],
    }]


def cmd_parse(args):
    registry = read_yaml(LIB / "registry.yaml")
    entry = next((p for p in registry["profiles"] if p["id"] == args.profile_id), None)
    if entry is None:
        raise SystemExit(f"注册表中没有画像 {args.profile_id}")
    if args.domain and entry.get("domain") != args.domain:
        raise SystemExit(f"{args.profile_id} 的域是 {entry.get('domain')}，与 --domain {args.domain} 不符")

    md = ROOT / args.baseline if not Path(args.baseline).is_absolute() else Path(args.baseline)
    clauses, difficulty = parse_baseline(md)

    aliases_cfg = (read_yaml(LIB / "shared/aliases.yaml").get("profiles") or {}).get(args.profile_id, {})
    alias_map = {a_["clause_no"]: a_ for a_ in aliases_cfg.get("aliases", [])}
    blocks_cfg = read_yaml(LIB / "shared/blocks.yaml")
    block_of_clause = {}
    for b in blocks_cfg.get("blocks", []):
        for cc in b.get("clauses", []):
            block_of_clause.setdefault(cc["clause_no"], []).append(b["id"])

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
    for c in clauses:
        pts = build_points(c, difficulty)
        if not pts:
            skipped.append(c["clause_no"])
            continue
        c["points"] = pts
        c["blocks"] = block_of_clause.get(c["clause_no"], [])
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

def validate():
    registry = read_yaml(LIB / "registry.yaml")
    aliases_cfg = read_yaml(LIB / "shared/aliases.yaml").get("profiles") or {}
    blocks_cfg = read_yaml(LIB / "shared/blocks.yaml")
    block_clause = {}
    block_ids_of = {}
    for b in blocks_cfg.get("blocks", []):
        bid = b.get("id")
        for cc in b.get("clauses", []):
            block_clause.setdefault(cc["clause_no"], set()).add(bid)
            block_ids_of.setdefault(bid, set()).add(cc["clause_no"])

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
        fp = LIB / entry.get("file", "")
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

        acfg = aliases_cfg.get(pid, {})
        invalid = {x["number"] for x in acfg.get("invalid_numbers", [])}
        known_alias = set(invalid)
        for a_ in acfg.get("aliases") or []:
            known_alias.add(a_["clause_no"])
            known_alias.update(a_.get("aliases") or [])
        tree_index = {}
        tree_ref = entry.get("clause_tree_ref")
        if tree_ref:
            tp = LIB / tree_ref
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
                    if c.get("block_count") is not None and c["block_count"] != len(tkids):
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
                for f in ("audit_question", "written_evidence", "on_site_evidence", "typical_nc"):
                    if not pt.get(f):
                        errors.append(f"{pid}:{cn}:{pk}: 缺字段 {f}（V3）")
                if pt.get("difficulty") not in VALID_DIFFICULTY + (None,):
                    errors.append(f"{pid}:{cn}:{pk}: difficulty 非法（V4）")
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
        sid = p.get("version_meta", {}).get("snapshot_id")
        if not sid:
            errors.append(f"{pid}: 缺 version_meta.snapshot_id")
        elif sid != snapshot_id(prof.get("clauses", [])):
            errors.append(f"{pid}: snapshot_id 与当前内容不符（内容被手改或过期）")
        npts = sum(len(c.get("points", [])) for c in prof.get("clauses", []))
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
    vv.set_defaults(func=lambda a: validate())
    args = ap.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
