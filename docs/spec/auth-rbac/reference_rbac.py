#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""#29 认证与六角色权限矩阵：参考实现与自检（仅标准库）。

用法：
    python reference_rbac.py --verify

设计要点（与 SPEC.md 对应）：
- AU3 矩阵 54 格加载即校验，缺格/多格/未声明动作一律报错；
- AU4 裁定顺序固定，纯函数，返回 Decision + 审计事件；
- AU7 职责分离：同一账号不能对自己生成的建议 APPROVE；
- AU12 掩码由「矩阵 mask + 资源敏感级别 + 授权」三者共同决定；
- AU16 授权有期限、不可自授、HIGHLY 需双人会签；
- AU18–AU20 审计事件同构、哈希链只追加。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
MATRIX_PATH = os.path.join(HERE, "rbac.matrix.json")

ROLES = ("TRADER", "SUPERVISOR", "RISK", "SETTLEMENT", "ADMIN", "AUDITOR")
SCOPES = ("ALL", "GRANT", "SETTLEMENT", "SELF", "TEAM", "RELATED", "TECH", "NONE")
FORBIDDEN_ACTIONS = ("TRADE_SUBMIT_REAL", "TRADE_CANCEL_REAL", "TRADE_ACCOUNT_WRITE")
SCOPE_RANK = {"NONE": 0, "SELF": 1, "GRANT": 2, "TECH": 3, "RELATED": 3, "SETTLEMENT": 4, "TEAM": 3, "ALL": 5}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# 矩阵加载与校验（AU3）
# --------------------------------------------------------------------------
def load_matrix(path: str = MATRIX_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def validate_matrix(m: dict) -> list[str]:
    errors: list[str] = []
    dims = m.get("dimensions", {})
    roles = m.get("roles", {})
    if set(roles) != set(ROLES):
        errors.append("角色集合不等于六角色：%s" % sorted(roles))
    if len(dims) != 9:
        errors.append("维度数量不是 9：%d" % len(dims))
    seen = set()
    for cell in m.get("cells", []):
        role, dim = cell.get("role"), cell.get("dimension")
        if role not in ROLES:
            errors.append("未知角色 %r" % role)
            continue
        if dim not in dims:
            errors.append("未知维度 %r" % dim)
            continue
        key = (role, dim)
        if key in seen:
            errors.append("重复格 %s/%s" % key)
        seen.add(key)
        acts = cell.get("actions", [])
        allowed = dims[dim]["actions"]
        extra = [a for a in acts if a not in allowed]
        if extra:
            errors.append("%s/%s 出现未声明动作 %s" % (role, dim, extra))
        if cell.get("scope") not in SCOPES:
            errors.append("%s/%s 范围非法 %r" % (role, dim, cell.get("scope")))
        if cell.get("mask") not in ("NONE", "MASKED"):
            errors.append("%s/%s 掩码非法 %r" % (role, dim, cell.get("mask")))
        if dim == "SYSTEM_CONFIG" and cell.get("actions") and not cell.get("domain"):
            errors.append("%s/SYSTEM_CONFIG 缺 domain" % role)
        if dim != "SYSTEM_CONFIG" and cell.get("domain"):
            errors.append("%s/%s 不应有 domain" % (role, dim))
        if dim == "SYSTEM_CONFIG" and cell.get("domain") not in (None, "ALL", "RISK", "SETTLEMENT"):
            errors.append("%s/SYSTEM_CONFIG domain 非法" % role)
        for a in acts:
            if a in FORBIDDEN_ACTIONS:
                errors.append("%s/%s 含真实交易写动作 %s" % (role, dim, a))
    want = {(r, d) for r in ROLES for d in dims}
    missing = want - seen
    if missing:
        errors.append("缺格：%s" % sorted(missing))
    return errors


# --------------------------------------------------------------------------
# 审计（AU18–AU20）
# --------------------------------------------------------------------------
class AuditLedger:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self._seq = 0

    @staticmethod
    def _canon(e: dict) -> str:
        return json.dumps(e, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def append(self, **fields) -> dict:
        self._seq += 1
        prev = self.events[-1]["hash"] if self.events else "GENESIS"
        event = dict(fields)
        event.update({"event_id": "E%06d" % self._seq, "prev_hash": prev})
        event["hash"] = hashlib.sha256((prev + self._canon(event)).encode("utf-8")).hexdigest()
        self.events.append(event)
        return event

    def verify(self) -> bool:
        prev = "GENESIS"
        for e in self.events:
            body = dict(e)
            body.pop("hash", None)
            expect = hashlib.sha256((prev + self._canon(body)).encode("utf-8")).hexdigest()
            if e.get("hash") != expect or e.get("prev_hash") != prev:
                return False
            prev = e["hash"]
        return True


# --------------------------------------------------------------------------
# 授权（AU15–AU17）
# --------------------------------------------------------------------------
def active_grant(grants, subject_id: str, dimension: str, now: datetime):
    for g in grants or ():
        if g.get("status") != "ACTIVE":
            continue
        if g.get("subject") != subject_id or g.get("dimension") != dimension:
            continue
        if g.get("created_at") and datetime.fromisoformat(g["created_at"]) > now:
            continue
        if g.get("expires_at") and datetime.fromisoformat(g["expires_at"]) <= now:
            continue
        return g
    return None


def make_grant(*, grant_id, subject, dimension, scope, mask, grantor, reason, created_at,
               ttl_days, expose="NONE", approvals=()) -> dict:
    if subject == grantor:
        raise ValueError("SELF_GRANT_FORBIDDEN")
    if len(reason.strip()) < 10:
        raise ValueError("REASON_TOO_SHORT")
    if ttl_days < 1 or ttl_days > 90:
        raise ValueError("TTL_OUT_OF_RANGE")
    if expose == "HIGHLY" and len(set(approvals)) < 2:
        raise ValueError("HIGHLY_REQUIRES_TWO_APPROVERS")
    return {
        "grant_id": grant_id, "subject": subject, "dimension": dimension, "scope": scope,
        "mask": mask, "expose": expose, "grantor": grantor, "reason": reason.strip(),
        "created_at": created_at.isoformat(),
        "expires_at": (created_at + timedelta(days=ttl_days)).isoformat(),
        "approvals": list(approvals), "status": "ACTIVE",
    }


# --------------------------------------------------------------------------
# 掩码（AU13）
# --------------------------------------------------------------------------
def mask_value(value) -> str:
    return "MASK-H" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:4]


# --------------------------------------------------------------------------
# 裁定（AU4）
# --------------------------------------------------------------------------
def _union_cells(matrix, roles, dimension, action):
    cells = [c for c in matrix["cells"] if c["role"] in roles and c["dimension"] == dimension and action in c["actions"]]
    cells.sort(key=lambda c: SCOPE_RANK.get(c["scope"], 0), reverse=True)
    return cells


def evaluate(matrix, subject, dimension, action, resource=None, grants=(), ledger=None,
             now=None, generated_by=None, session_id="S-0001", request_id="R-0001") -> dict:
    now = now or utcnow()
    resource = resource or {}
    reasons = []
    allowed = False
    scope = "NONE"
    masked_fields: list[str] = []
    reason_code = "OK"

    def deny(code: str):
        nonlocal allowed, reason_code
        allowed = False
        reason_code = code
        return Decision(matrix, subject, dimension, action, resource, False, "NONE", [], code, ledger, now, session_id, request_id)

    if subject.get("status") != "ACTIVE":
        return deny("ACCOUNT_DISABLED")
    if action in matrix.get("forbidden_actions", FORBIDDEN_ACTIONS):
        return deny("FORBIDDEN_ACTION")
    dims = matrix["dimensions"]
    if dimension not in dims:
        return deny("UNKNOWN_DIMENSION")
    if action not in dims[dimension]["actions"]:
        return deny("UNKNOWN_ACTION")
    roles = [r for r in subject.get("roles", []) if r in matrix["roles"]]
    if not roles:
        return deny("NO_ROLE")
    if dimension == "APPROVAL" and action == "APPROVE":
        if generated_by is not None and generated_by == subject.get("account_id"):
            return deny("SEPARATION_OF_DUTY")
        if "TRADER" in roles:
            return deny("TRADER_CANNOT_APPROVE")

    cells = _union_cells(matrix, roles, dimension, action)
    if not cells:
        return deny("NOT_GRANTED")
    scope = cells[0]["scope"]
    mask = "NONE" if any(c["mask"] == "NONE" for c in cells) else "MASKED"
    if dimension == "SYSTEM_CONFIG":
        need = cells[0].get("domain")
        want = resource.get("config_domain")
        if need != "ALL" and want != need:
            return deny("CONFIG_DOMAIN_MISMATCH")

    # 范围解析
    if scope == "ALL":
        pass
    elif scope == "GRANT":
        g = active_grant(grants, subject["account_id"], dimension, now)
        if g is None:
            return deny("NO_GRANT")
        scope = g["scope"]
        if g["mask"] == "NONE":
            mask = "NONE"
        resource = dict(resource)
        resource["_grant_id"] = g["grant_id"]
        if g.get("expose") == "HIGHLY":
            mask = "NONE"
    elif scope == "SETTLEMENT":
        if resource.get("dataset") not in ("D06", "D09", "D10"):
            return deny("OUT_OF_SETTLEMENT_SCOPE")
        em = resource.get("energy_month")
        if not em:
            return deny("OUT_OF_SETTLEMENT_SCOPE")
        months = (now.year - int(em[:4])) * 12 + (now.month - int(em[5:7]))
        if months < -1 or months > 12:
            return deny("OUT_OF_SETTLEMENT_SCOPE")
    elif scope == "SELF":
        if resource.get("owner") != subject.get("account_id"):
            return deny("SELF_SCOPE_ONLY")
    elif scope == "TEAM":
        if resource.get("team") != subject.get("team"):
            return deny("TEAM_SCOPE_ONLY")
    elif scope == "RELATED":
        rel = subject.get("account_id") in (resource.get("related_accounts") or ()) or resource.get("team") == subject.get("team")
        if not rel:
            return deny("RELATED_SCOPE_ONLY")
    elif scope == "TECH":
        if resource.get("dataset") != "D16":
            return deny("TECH_LOG_ONLY")
    else:
        return deny("NOT_GRANTED")

    # 敏感数据掩码（AU12）
    if dimension == "SENSITIVE_DATA":
        sens = resource.get("sensitivity", "SENSITIVE")
        fields = list(resource.get("sensitive_fields") or [])
        if sens == "PUBLIC":
            pass
        elif sens == "SENSITIVE":
            if mask == "MASKED":
                masked_fields = fields
        elif sens == "HIGHLY":
            grant_id = resource.get("_grant_id")
            g = active_grant(grants, subject["account_id"], dimension, now) if grant_id else None
            if g is not None and g.get("expose") == "HIGHLY":
                masked_fields = []
            else:
                masked_fields = fields
                mask = "MASKED"
        else:
            return deny("UNKNOWN_SENSITIVITY")

    allowed = True
    reason_code = "OK"
    return Decision(matrix, subject, dimension, action, resource, True, scope, masked_fields, reason_code,
                    ledger, now, session_id, request_id)


class Decision(dict):
    """裁定结果；dict 便于断言与 JSON 序列化，同时自动写审计事件。"""

    def __init__(self, matrix, subject, dimension, action, resource, allowed, scope,
                 masked_fields, reason_code, ledger, now, session_id, request_id):
        super().__init__({
            "allowed": allowed,
            "scope": scope,
            "masked_fields": masked_fields,
            "reason_code": reason_code,
        })
        self.event = None
        if ledger is not None:
            self.event = ledger.append(
                at=now.isoformat(), actor=subject.get("account_id"), roles=list(subject.get("roles", [])),
                session_id=session_id, dimension=dimension, action=action,
                resource_type=resource.get("dataset") or resource.get("type") or "NONE",
                resource_id=str(resource.get("id") or ""), verdict="ALLOW" if allowed else "DENY",
                reason_code=reason_code, scope=scope, masked_fields=masked_fields,
                grant_id=resource.get("_grant_id"), request_id=request_id,
            )


# --------------------------------------------------------------------------
# 会话（AC4–AC8）
# --------------------------------------------------------------------------
class SessionStore:
    def __init__(self, idle_minutes=30, absolute_hours=12, max_failures=5, lock_minutes=15):
        self.idle = timedelta(minutes=idle_minutes)
        self.absolute = timedelta(hours=absolute_hours)
        self.max_failures = max_failures
        self.lock = timedelta(minutes=lock_minutes)
        self.sessions: dict[str, dict] = {}
        self.failures: dict[str, list] = {}
        self.locks: dict[str, datetime] = {}

    def login(self, account_id, password_ok, now, token):
        if now < self.locks.get(account_id, now - timedelta(seconds=1)):
            return {"ok": False, "reason": "LOCKED"}
        if not password_ok:
            fails = [t for t in self.failures.get(account_id, []) if now - t <= timedelta(minutes=15)]
            fails.append(now)
            self.failures[account_id] = fails
            if len(fails) >= self.max_failures:
                self.locks[account_id] = now + self.lock
                return {"ok": False, "reason": "LOCKED"}
            return {"ok": False, "reason": "BAD_CREDENTIALS"}
        self.failures.pop(account_id, None)
        self.sessions[token] = {"created": now, "seen": now}
        return {"ok": True, "token": token, "expires": (now + self.absolute).isoformat()}

    def touch(self, token, now):
        s = self.sessions.get(token)
        if s is None:
            return False
        if now - s["seen"] > self.idle or now - s["created"] > self.absolute:
            del self.sessions[token]
            return False
        s["seen"] = now
        return True


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------
def _subject(account="u001", roles=("TRADER",), team="T1", status="ACTIVE"):
    return {"account_id": account, "roles": list(roles), "team": team, "status": status}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args(argv)
    m = load_matrix()
    ledger = AuditLedger()
    out = []
    total = 0
    passed = 0

    def check(name, cond, detail=""):
        nonlocal total, passed
        total += 1
        if cond:
            passed += 1
            out.append("[PASS] %s%s" % (name, ("：" + detail) if detail else ""))
        else:
            out.append("[FAIL] %s%s" % (name, ("：" + detail) if detail else ""))

    # E1 矩阵完整性
    errs = validate_matrix(m)
    check("E1 矩阵完整性", not errs and len(m["cells"]) == 54,
          "54/54 格、6 角色、9 维度、%d 个校验错误" % len(errs))
    if errs:
        out.extend("  - " + e for e in errs)

    # E2 默认拒绝
    r_unknown_role = evaluate(m, _subject(roles=("GHOST",)), "RULE_LEARNING", "VIEW", {}, ledger=ledger)
    r_unknown_dim = evaluate(m, _subject(), "GHOST_DIM", "VIEW", {}, ledger=ledger)
    r_unknown_action = evaluate(m, _subject(), "RULE_LEARNING", "DELETE", {}, ledger=ledger)
    check("E2 默认拒绝", all(x["allowed"] is False for x in (r_unknown_role, r_unknown_dim, r_unknown_action))
          and r_unknown_role["reason_code"] == "NO_ROLE"
          and r_unknown_dim["reason_code"] == "UNKNOWN_DIMENSION"
          and r_unknown_action["reason_code"] == "UNKNOWN_ACTION",
          "未知角色 NO_ROLE / 未知维度 UNKNOWN_DIMENSION / 未声明动作 UNKNOWN_ACTION")

    # E3 越权探针（A09）
    probes = [
        (_subject(roles=("ADMIN",)), "SENSITIVE_DATA", "VIEW", {}, "ADMIN_VS_SENSITIVE"),
        (_subject(roles=("TRADER",)), "DATA_IMPORT", "IMPORT", {}, "TRADER_VS_IMPORT"),
        (_subject(roles=("SETTLEMENT",)), "SIM_TRADING", "SUBMIT", {}, "SETTLEMENT_VS_SUBMIT"),
        (_subject(roles=("AUDITOR",)), "SYSTEM_CONFIG", "CONFIGURE", {"config_domain": "ALL"}, "AUDITOR_VS_CONFIG"),
        (_subject(roles=("TRADER",)), "APPROVAL", "APPROVE", {}, "TRADER_VS_APPROVE"),
        (_subject(roles=("RISK",)), "SETTLEMENT_REVIEW", "REVIEW", {}, "RISK_VS_REVIEW"),
    ]
    ok3 = True
    detail3 = []
    for subj, dim, act, res, label in probes:
        d = evaluate(m, subj, dim, act, res, ledger=ledger)
        detail3.append("%s=%s(%s)" % (label, d["allowed"], d["reason_code"]))
        ok3 = ok3 and d["allowed"] is False
    check("E3 越权探针全拒绝", ok3, "；".join(detail3))

    # E4 掩码
    highly = {"dataset": "D04", "sensitivity": "HIGHLY", "sensitive_fields": ["电量", "用户ID"], "id": "D04:1"}
    public = {"dataset": "D11", "sensitivity": "PUBLIC", "sensitive_fields": [], "id": "D11:1"}
    grant = make_grant(grant_id="G001", subject="u009", dimension="SENSITIVE_DATA", scope="ALL",
                       mask="MASKED", grantor="g001", reason="审计抽查某月负荷曲线", created_at=utcnow(),
                       ttl_days=30)
    d4a = evaluate(m, _subject("u009", ("AUDITOR",)), "SENSITIVE_DATA", "VIEW", highly, grants=[grant], ledger=ledger)
    d4b = evaluate(m, _subject("u009", ("AUDITOR",)), "SENSITIVE_DATA", "VIEW", public, grants=[grant], ledger=ledger)
    m1, m2 = mask_value("100.5"), mask_value("100.5")
    check("E4 掩码确定性", d4a["allowed"] and d4a["masked_fields"] == ["电量", "用户ID"]
          and d4b["masked_fields"] == [] and m1 == m2 and m1.startswith("MASK-H") and "100.5" not in m1,
          "HIGHLY 掩码 2 字段、PUBLIC 不掩码、同值掩码稳定 %s" % m1)

    # E5 无真实交易写权限（A12）
    bad = [c for c in m["cells"] if set(c["actions"]) & set(FORBIDDEN_ACTIONS)]
    ev5 = [evaluate(m, _subject(roles=(r,)), "SIM_TRADING", "TRADE_SUBMIT_REAL", {}, ledger=ledger) for r in ROLES]
    check("E5 真实交易写被禁止", not bad and all(x["allowed"] is False and x["reason_code"] == "FORBIDDEN_ACTION" for x in ev5),
          "54 格无禁止动作；六角色 TRADE_SUBMIT_REAL 均 FORBIDDEN_ACTION")

    # E6 职责分离
    s = _subject("sup1", ("SUPERVISOR",))
    d6a = evaluate(m, s, "APPROVAL", "APPROVE", {}, generated_by="sup1", ledger=ledger)
    d6b = evaluate(m, s, "APPROVAL", "APPROVE", {}, generated_by="trader1", ledger=ledger)
    r6 = evaluate(m, _subject("risk1", ("RISK",)), "APPROVAL", "VETO", {}, ledger=ledger)
    check("E6 职责分离", d6a["reason_code"] == "SEPARATION_OF_DUTY" and d6b["allowed"] is True
          and r6["allowed"] is True,
          "自提自批 DENY、批他人 ALLOW、风控否决 ALLOW")

    # E7 授权：不可自授、可过期
    d7_no = evaluate(m, _subject("trader9", ("TRADER",)), "SENSITIVE_DATA", "VIEW", highly, ledger=ledger)
    expired = dict(grant)
    expired.update({"subject": "trader9", "grant_id": "G002", "status": "ACTIVE",
                    "expires_at": (utcnow() - timedelta(days=1)).isoformat(),
                    "created_at": (utcnow() - timedelta(days=31)).isoformat()})
    d7_exp = evaluate(m, _subject("trader9", ("TRADER",)), "SENSITIVE_DATA", "VIEW", highly, grants=[expired], ledger=ledger)
    self_grant_err = None
    try:
        make_grant(grant_id="G003", subject="trader9", dimension="SENSITIVE_DATA", scope="ALL", mask="MASKED",
                   grantor="trader9", reason="想看看自己的曲线", created_at=utcnow(), ttl_days=30)
    except ValueError as exc:
        self_grant_err = str(exc)
    highly_err = None
    try:
        make_grant(grant_id="G004", subject="trader9", dimension="SENSITIVE_DATA", scope="ALL", mask="NONE",
                   grantor="g001", reason="测试高度敏感数据的双人会签授权", created_at=utcnow(), ttl_days=30,
                   expose="HIGHLY", approvals=("g001",))
    except ValueError as exc:
        highly_err = str(exc)
    check("E7 授权边界", d7_no["reason_code"] == "NO_GRANT" and d7_exp["reason_code"] == "NO_GRANT"
          and self_grant_err == "SELF_GRANT_FORBIDDEN" and highly_err == "HIGHLY_REQUIRES_TWO_APPROVERS",
          "无授权/过期均 NO_GRANT；自授与单人会签被拒")

    # E8 审计链 + 会话
    chain_ok = ledger.verify()
    tampered = AuditLedger()
    tampered.events = [dict(e) for e in ledger.events]
    tampered.events[1]["verdict"] = "ALLOW"
    check("E8 审计链与会话", chain_ok and not tampered.verify()
          and all(e.get("reason_code") for e in ledger.events)
          and ledger.events[0]["prev_hash"] == "GENESIS",
          "链长 %d 校验通过、篡改即断链、拒绝事件均有 reason_code" % len(ledger.events))

    store = SessionStore()
    now = utcnow()
    login = store.login("u001", True, now, "tok-1")
    alive = store.touch("tok-1", now + timedelta(minutes=29))
    dead_idle = store.touch("tok-1", now + timedelta(minutes=29 + 31))
    lock = None
    for i in range(5):
        lock = store.login("u002", False, now + timedelta(seconds=i), "tok-x")
    check("E8b 会话超时与锁定", login["ok"] and alive and not dead_idle and lock["reason"] == "LOCKED",
          "空闲 30 分钟失效、5 次失败锁定")

    print("\n".join(out))
    print("\nSummary: %d/%d PASS" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
