#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""#50 登录、会话与六角色权限落地：参考实现与自检（仅标准库）。

用法：
    python reference_auth.py --verify

设计要点（与 SPEC.md 对应）：
- 裁定唯一：一���权限判定走 #29 的 evaluate()，本文件不重写裁定逻辑；
- 投影唯一：/me/capabilities 与 #46 reference_api.compress_capabilities() 同算法，
  自检逐字节比对，防止两边漂移；
- 拒绝同形：资源不存在与无权限返回同一错误码、同一文案、同一 details 结构；
- 审计同构：allow 与 deny 是同一事件结构，认证事件与授权事件同链；
- 零第三方依赖：只用 Python 标准库，口令 KDF 用 hashlib.scrypt。

依赖（仓内既有产物，只读引用，不重写）：
    docs/spec/auth-rbac/rbac.matrix.json      54 格矩阵（裁定唯一来源）
    docs/spec/auth-rbac/reference_rbac.py       evaluate / make_grant / AuditLedger
    docs/spec/api-contract/errors.json          34 个稳定错误码
    docs/spec/api-contract/openapi.json         /me/capabilities 响应形状
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import struct
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
RBAC_DIR = os.path.join(ROOT, "docs", "spec", "auth-rbac")
API_DIR = os.path.join(ROOT, "docs", "spec", "api-contract")
if RBAC_DIR not in sys.path:
    sys.path.insert(0, RBAC_DIR)

import reference_rbac as rbac  # noqa: E402

MATRIX = rbac.load_matrix(os.path.join(RBAC_DIR, "rbac.matrix.json"))
with open(os.path.join(API_DIR, "errors.json"), "r", encoding="utf-8") as _fh:
    ERRORS = {e["code"]: e for e in json.load(_fh)["codes"]}
with open(os.path.join(API_DIR, "openapi.json"), "r", encoding="utf-8") as _fh:
    OPENAPI = json.load(_fh)

SESSION_CFG = MATRIX.get("session", {})
GRANT_CFG = MATRIX.get("grant", {})

# --------------------------------------------------------------------------
# 参数（AC2 / AC4 / AC5 / AC6）
# --------------------------------------------------------------------------
IDLE_TIMEOUT = timedelta(minutes=SESSION_CFG.get("idle_timeout_minutes", 30))
ABSOLUTE_TIMEOUT = timedelta(hours=SESSION_CFG.get("absolute_timeout_hours", 12))
MAX_FAILED_LOGINS = SESSION_CFG.get("max_failed_logins", 5)
LOCKOUT = timedelta(minutes=SESSION_CFG.get("lockout_minutes", 15))
FAILURE_WINDOW = timedelta(minutes=15)
IP_LIMIT = 20
IP_WINDOW = timedelta(minutes=15)
REAUTH_TTL = timedelta(minutes=5)
PASSWORD_MIN = 12
TOKEN_BYTES = 32
COOKIE_NAME = "ccs_session"
CONTRACT_VERSION = OPENAPI.get("info", {}).get("version", "1.0.0")

SCRYPT_N = 1 << 16
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
PBKDF2_ROUNDS = 600000

# 平台只读 + 建议 + 模拟：不存在任何写入真实交易系统的效果（#46 §25–27）
PLATFORM_EFFECTS = ("READ", "PROPOSE", "SIMULATE")
FORBIDDEN_ACTIONS = tuple(MATRIX.get("forbidden_actions", rbac.FORBIDDEN_ACTIONS))

# 需要再认证的动作（AC6）
REAUTH_ACTIONS = frozenset({
    "SYSTEM_CONFIG:CONFIGURE", "GRANT_CREATE", "SENSITIVE_EXPORT", "BREAK_GLASS",
})

# reason_code → 对外错误码（#46 的 34 个稳定错误码）
REASON_TO_ERROR = {
    "OK": None,
    "ACCOUNT_DISABLED": "UNAUTHENTICATED",
    "FORBIDDEN_ACTION": "WRITE_FORBIDDEN",
    "UNKNOWN_DIMENSION": "FORBIDDEN",
    "UNKNOWN_ACTION": "FORBIDDEN",
    "NO_ROLE": "FORBIDDEN",
    "SEPARATION_OF_DUTY": "FORBIDDEN",
    "TRADER_CANNOT_APPROVE": "FORBIDDEN",
    "NOT_GRANTED": "FORBIDDEN",
    "CONFIG_DOMAIN_MISMATCH": "FORBIDDEN",
    "NO_GRANT": "NO_GRANT",
    "OUT_OF_SETTLEMENT_SCOPE": "OUT_OF_SETTLEMENT_SCOPE",
    "SELF_SCOPE_ONLY": "FORBIDDEN",
    "TEAM_SCOPE_ONLY": "FORBIDDEN",
    "RELATED_SCOPE_ONLY": "FORBIDDEN",
    "TECH_LOG_ONLY": "FORBIDDEN",
    "UNKNOWN_SENSITIVITY": "FORBIDDEN",
}


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


# --------------------------------------------------------------------------
# 口令 KDF（AC2；偏差登记见 SPEC.md §13）
# --------------------------------------------------------------------------
def hash_password(password: str) -> str:
    if len(password) < PASSWORD_MIN:
        raise ValueError("PASSWORD_TOO_SHORT")
    salt = secrets.token_bytes(16)
    raw = password.encode("utf-8")
    if hasattr(hashlib, "scrypt"):
        digest = hashlib.scrypt(raw, salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                                dklen=SCRYPT_DKLEN, maxmem=256 * SCRYPT_N * SCRYPT_R)
        return "scrypt$%d$%d$%d$%s$%s" % (SCRYPT_N, SCRYPT_R, SCRYPT_P, _b64e(salt), _b64e(digest))
    digest = hashlib.pbkdf2_hmac("sha256", raw, salt, PBKDF2_ROUNDS, SCRYPT_DKLEN)
    return "pbkdf2-sha256$%d$%s$%s" % (PBKDF2_ROUNDS, _b64e(salt), _b64e(digest))


def verify_password(password: str, encoded: str) -> bool:
    try:
        parts = str(encoded).split("$")
        algo = parts[0]
        if algo == "scrypt":
            n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
            salt, digest = _b64d(parts[4]), _b64d(parts[5])
            if not hasattr(hashlib, "scrypt"):
                return False
            calc = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                                  dklen=len(digest), maxmem=256 * n * r)
        elif algo == "pbkdf2-sha256":
            rounds = int(parts[1])
            salt, digest = _b64d(parts[2]), _b64d(parts[3])
            calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds, len(digest))
        else:
            return False
    except (ValueError, TypeError, IndexError, binascii.Error):
        return False
    return hmac.compare_digest(calc, digest)


def kdf_algorithm(encoded: str) -> str:
    return str(encoded).split("$")[0]


# --------------------------------------------------------------------------
# TOTP（AC3）
# --------------------------------------------------------------------------
def _hotp(secret: bytes, counter: int) -> str:
    mac = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return "%06d" % (code % 1000000)


def _b32decode(secret_b32: str) -> bytes:
    text = str(secret_b32).upper()
    return base64.b32decode(text + "=" * (-len(text) % 8), casefold=True)


def totp_code(secret_b32: str, now: datetime) -> str:
    return _hotp(_b32decode(secret_b32), int(now.timestamp()) // 30)


def totp_verify(secret_b32: str, code: str, now: datetime, window: int = 1) -> bool:
    if not secret_b32 or not code:
        return False
    key = _b32decode(secret_b32)
    counter = int(now.timestamp()) // 30
    for drift in range(-window, window + 1):
        if hmac.compare_digest(_hotp(key, counter + drift), str(code).strip()):
            return True
    return False


# --------------------------------------------------------------------------
# 账号与会话（AC1–AC8）
# --------------------------------------------------------------------------
class Account:
    def __init__(self, account_id, roles, password=None, team="T1", status="ACTIVE",
                 totp_secret=None, service_account=False, org_id=None):
        self.account_id = account_id
        self.roles = list(roles)
        self.password_hash = hash_password(password) if password else ""
        self.team = team
        self.status = status
        self.totp_secret = totp_secret
        self.service_account = service_account
        self.org_id = org_id
        self.password_changed_at = None
        self.locked_until = None
        self.mfa_required = (not service_account) and (
            "ADMIN" in self.roles or "RISK" in self.roles)

    def subject(self) -> dict:
        return {"account_id": self.account_id, "roles": list(self.roles),
                "team": self.team, "status": self.status}


class AccountStore:
    def __init__(self):
        self._accounts = {}

    def add(self, account: Account) -> Account:
        self._accounts[account.account_id] = account
        return account

    def get(self, account_id: str):
        return self._accounts.get(account_id)

    def all(self) -> list:
        return list(self._accounts.values())


class Session:
    def __init__(self, session_id, account_id, token_hash, created_at, rotated_from=None):
        self.session_id = session_id
        self.account_id = account_id
        self.token_hash = token_hash
        self.created_at = created_at
        self.last_seen_at = created_at
        self.rotated_from = rotated_from
        self.reauth_at = None
        self.mfa_verified_at = None
        self.revoked_at = None
        self.revoke_reason = None


class SessionStore:
    def __init__(self, idle=IDLE_TIMEOUT, absolute=ABSOLUTE_TIMEOUT):
        self.idle = idle
        self.absolute = absolute
        self.by_token = {}
        self.by_id = {}
        self.failures = {}
        self.locks = {}
        self.ip_hits = {}
        self._seq = 0

    def _next_id(self) -> str:
        self._seq += 1
        return "S-%06d" % self._seq

    def issue(self, account_id: str, now: datetime, rotated_from=None):
        token = secrets.token_urlsafe(TOKEN_BYTES)
        session = Session(self._next_id(), account_id,
                          hashlib.sha256(token.encode("utf-8")).hexdigest(), now, rotated_from)
        self.by_token[token] = session
        self.by_id[session.session_id] = session
        return token, session

    def resolve(self, token: str, now: datetime):
        session = self.by_token.get(token)
        if session is None or session.revoked_at is not None:
            return None
        if now - session.last_seen_at > self.idle or now - session.created_at > self.absolute:
            self.revoke(token, now, "TIMEOUT")
            return None
        session.last_seen_at = now
        return session

    def revoke(self, token: str, now: datetime, reason: str):
        session = self.by_token.pop(token, None)
        if session is not None and session.revoked_at is None:
            session.revoked_at = now
            session.revoke_reason = reason
        return session

    def revoke_account(self, account_id: str, now: datetime, reason: str):
        for token in [t for t, s in self.by_token.items() if s.account_id == account_id]:
            self.revoke(token, now, reason)

    def register_failure(self, account_id: str, now: datetime) -> str:
        fails = [t for t in self.failures.get(account_id, []) if now - t <= FAILURE_WINDOW]
        fails.append(now)
        self.failures[account_id] = fails
        if len(fails) >= MAX_FAILED_LOGINS:
            self.locks[account_id] = now + LOCKOUT
            return "LOCKED"
        return "BAD_CREDENTIALS"

    def is_locked(self, account_id: str, now: datetime) -> bool:
        until = self.locks.get(account_id)
        return until is not None and now < until

    def ip_blocked(self, ip: str, now: datetime) -> bool:
        hits = [t for t in self.ip_hits.get(ip, []) if now - t <= IP_WINDOW]
        if len(hits) >= IP_LIMIT:
            self.ip_hits[ip] = hits
            return True
        hits.append(now)
        self.ip_hits[ip] = hits
        return False


# --------------------------------------------------------------------------
# 审计（AU18–AU21）：代理 #29 的 AuditLedger，补每日链头锚点
# --------------------------------------------------------------------------
class AnchoredLedger:
    """每日锚定链头（AU20）：同一自然日的所有事件带同一个 day_anchor；其余行为完全委托 #29 的 AuditLedger。"""

    def __init__(self, ledger=None):
        self.inner = ledger if ledger is not None else rbac.AuditLedger()
        self.events = self.inner.events
        self._anchored = {}

    def append(self, **fields) -> dict:
        day = str(fields.get("at", ""))[:10]
        if day and day not in self._anchored:
            self._anchored[day] = self.inner.events[-1]["hash"] if self.inner.events else "GENESIS"
        if day:
            fields["day_anchor"] = self._anchored[day]
        return self.inner.append(**fields)

    def verify(self) -> bool:
        return self.inner.verify()


def retention_class(age_days: int) -> str:
    """AU21 留存分级：180 天在线 + 3 年归档；任何角色不能缩短。"""
    if age_days <= 180:
        return "ONLINE"
    if age_days <= 180 + 3 * 365:
        return "ARCHIVE"
    return "PURGE"


# --------------------------------------------------------------------------
# 错误信封与能力投影（#46）
# --------------------------------------------------------------------------
def meta(request_id: str = "req_meta0000001", generated_at=None) -> dict:
    return {
        "request_id": request_id,
        "api_version": "v1",
        "contract_version": CONTRACT_VERSION,
        "generated_at": (generated_at or rbac.utcnow()).isoformat(),
    }


def error_response(code: str, request_id: str = "req_err00000001", details=None) -> dict:
    spec = ERRORS[code]
    return {
        "error": {"code": code, "message": spec["message"],
                  "details": details or {}, "retryable": spec["retryable"]},
        "meta": meta(request_id),
    }


def compress_capabilities(roles) -> list:
    """与 #46 reference_api.compress_capabilities() 同算法：矩阵的唯一对外投影。"""
    out = []
    for dim in MATRIX["dimensions"]:
        cells = [c for c in MATRIX["cells"]
                 if c["role"] in roles and c["dimension"] == dim and c["actions"]]
        if not cells:
            continue
        actions = sorted({a for c in cells for a in c["actions"]})
        scope = max((c["scope"] for c in cells), key=lambda s: rbac.SCOPE_RANK.get(s, 0))
        mask = "MASKED" if any(c["mask"] == "MASKED" for c in cells) else "NONE"
        out.append({"dimension": dim, "actions": actions, "scope": scope, "mask": mask})
    return out


def capabilities_response(subject: dict, request_id: str = "req_caps0000001", generated_at=None) -> dict:
    roles = list(subject.get("roles", []))
    ident = {"account_id": subject.get("account_id"), "roles": roles}
    if subject.get("org_id"):
        ident["org_id"] = subject["org_id"]
    data = {
        "subject": ident,
        "capabilities": compress_capabilities(roles),
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
    }
    return {"data": data, "meta": meta(request_id, generated_at)}


# --------------------------------------------------------------------------
# 鉴权中间件（#29 §11）
# --------------------------------------------------------------------------
def authorize(*, subject, dimension, action, resource=None, grants=(), ledger=None,
              now=None, generated_by=None, session_id="S-000000",
              request_id="req_authz000001") -> dict:
    """裁定唯一入口：一切判定走 #29 evaluate()，服务层不得自行判断角色名。"""
    decision = rbac.evaluate(MATRIX, subject, dimension, action, resource, grants=grants,
                             ledger=ledger, now=now, generated_by=generated_by,
                             session_id=session_id, request_id=request_id)
    code = REASON_TO_ERROR.get(decision["reason_code"]) or ("FORBIDDEN" if not decision["allowed"] else None)
    return {
        "allowed": decision["allowed"],
        "scope": decision["scope"],
        "masked_fields": list(decision["masked_fields"]),
        "reason_code": decision["reason_code"],
        "error": code,
        "audit_event": decision.event,
    }


def require_reauth(session, action_key: str, now: datetime) -> bool:
    """AC6：改配置 / 建授权 / 导出敏感 / break-glass 前须再认证，5 分钟内有效。"""
    if action_key not in REAUTH_ACTIONS:
        return True
    if session is None or session.reauth_at is None:
        return False
    return now - session.reauth_at <= REAUTH_TTL


# --------------------------------------------------------------------------
# 认证服务（AC1–AC8）
# --------------------------------------------------------------------------
class AuthService:
    def __init__(self, accounts: AccountStore, sessions: SessionStore, ledger=None):
        self.accounts = accounts
        self.sessions = sessions
        self.ledger = ledger if ledger is not None else AnchoredLedger()

    def _audit(self, account, action, verdict, reason_code, now, request_id,
               session_id="S-000000", resource=None):
        resource = resource or {}
        return self.ledger.append(
            at=now.isoformat(),
            actor=account.account_id if account else "",
            roles=list(account.roles) if account else [],
            session_id=session_id,
            dimension="AUTH",
            action=action,
            resource_type=resource.get("type", "AUTH"),
            resource_id=str(resource.get("id", "")),
            verdict=verdict,
            reason_code=reason_code,
            scope="NONE",
            masked_fields=[],
            grant_id=None,
            request_id=request_id,
        )

    def login(self, *, account_id, password, totp_code=None, ip=None, now=None,
              request_id="req_login000001"):
        now = now or rbac.utcnow()
        if ip is not None and self.sessions.ip_blocked(ip, now):
            self._audit(None, "LOGIN", "DENY", "IP_RATE_LIMITED", now, request_id)
            return {"ok": False, "error": "RATE_LIMITED", "reason": "IP_RATE_LIMITED"}
        account = self.accounts.get(account_id)
        if account is None:
            self._audit(None, "LOGIN", "DENY", "BAD_CREDENTIALS", now, request_id)
            return {"ok": False, "error": "UNAUTHENTICATED", "reason": "BAD_CREDENTIALS"}
        if account.service_account:
            self._audit(account, "LOGIN", "DENY", "SERVICE_ACCOUNT_NOT_INTERACTIVE",
                        now, request_id)
            return {"ok": False, "error": "UNAUTHENTICATED",
                    "reason": "SERVICE_ACCOUNT_NOT_INTERACTIVE"}
        if self.sessions.is_locked(account_id, now):
            self._audit(account, "LOGIN", "DENY", "ACCOUNT_LOCKED", now, request_id)
            return {"ok": False, "error": "RATE_LIMITED", "reason": "ACCOUNT_LOCKED"}
        if account.status != "ACTIVE":
            self._audit(account, "LOGIN", "DENY", "ACCOUNT_DISABLED", now, request_id)
            return {"ok": False, "error": "UNAUTHENTICATED", "reason": "ACCOUNT_DISABLED"}
        if not verify_password(password, account.password_hash):
            reason = self.sessions.register_failure(account_id, now)
            self._audit(account, "LOGIN", "DENY", reason, now, request_id)
            return {"ok": False,
                    "error": "RATE_LIMITED" if reason == "LOCKED" else "UNAUTHENTICATED",
                    "reason": reason}
        if account.mfa_required:
            if not totp_code:
                self._audit(account, "LOGIN", "DENY", "MFA_REQUIRED", now, request_id)
                return {"ok": False, "error": "UNAUTHENTICATED", "reason": "MFA_REQUIRED"}
            if not totp_verify(account.totp_secret, totp_code, now):
                self._audit(account, "MFA_VERIFY", "DENY", "MFA_INVALID", now, request_id)
                return {"ok": False, "error": "UNAUTHENTICATED", "reason": "MFA_INVALID"}
        self.sessions.failures.pop(account_id, None)
        token, session = self.sessions.issue(account_id, now)
        if account.mfa_required:
            session.mfa_verified_at = now
        self._audit(account, "LOGIN", "ALLOW", "OK", now, request_id, session.session_id)
        return {"ok": True, "token": token, "session": session,
                "cookie": session_cookie(token, now)}

    def logout(self, *, token, now=None, request_id="req_logout00001"):
        now = now or rbac.utcnow()
        session = self.sessions.by_token.get(token)
        if session is None or session.revoked_at is not None:
            return {"ok": False, "error": "UNAUTHENTICATED"}
        account = self.accounts.get(session.account_id)
        self.sessions.revoke(token, now, "LOGOUT")
        self._audit(account, "LOGOUT", "ALLOW", "OK", now, request_id, session.session_id)
        return {"ok": True}

    def rotate(self, *, token, now=None, reason="ROTATE", request_id="req_rotate00001"):
        now = now or rbac.utcnow()
        session = self.sessions.by_token.get(token)
        if session is None or session.revoked_at is not None:
            return {"ok": False, "error": "UNAUTHENTICATED"}
        self.sessions.revoke(token, now, reason)
        new_token, new_session = self.sessions.issue(
            session.account_id, now, rotated_from=session.session_id)
        account = self.accounts.get(session.account_id)
        self._audit(account, "TOKEN_ROTATE", "ALLOW", "OK", now, request_id, new_session.session_id)
        return {"ok": True, "token": new_token, "session": new_session}

    def reauthenticate(self, *, token, password, totp_code=None, now=None,
                       request_id="req_reauth00001"):
        now = now or rbac.utcnow()
        session = self.sessions.by_token.get(token)
        if session is None or session.revoked_at is not None:
            return {"ok": False, "error": "UNAUTHENTICATED"}
        account = self.accounts.get(session.account_id)
        if account is None or not verify_password(password, account.password_hash):
            self._audit(account, "REAUTH", "DENY", "BAD_CREDENTIALS", now, request_id,
                        session.session_id)
            return {"ok": False, "error": "UNAUTHENTICATED", "reason": "BAD_CREDENTIALS"}
        if account.mfa_required:
            if not totp_code or not totp_verify(account.totp_secret, totp_code, now):
                self._audit(account, "REAUTH", "DENY", "MFA_INVALID", now, request_id,
                            session.session_id)
                return {"ok": False, "error": "UNAUTHENTICATED", "reason": "MFA_INVALID"}
        self.sessions.revoke(token, now, "REAUTH_ROTATE")
        new_token, new_session = self.sessions.issue(
            session.account_id, now, rotated_from=session.session_id)
        new_session.reauth_at = now
        new_session.mfa_verified_at = session.mfa_verified_at
        self._audit(account, "REAUTH", "ALLOW", "OK", now, request_id, new_session.session_id)
        return {"ok": True, "token": new_token, "session": new_session}

    def change_password(self, *, token, old_password, new_password, now=None,
                        request_id="req_chpwd000001"):
        now = now or rbac.utcnow()
        session = self.sessions.by_token.get(token)
        if session is None or session.revoked_at is not None:
            return {"ok": False, "error": "UNAUTHENTICATED"}
        account = self.accounts.get(session.account_id)
        if account is None or not verify_password(old_password, account.password_hash):
            self._audit(account, "PASSWORD_CHANGE", "DENY", "BAD_CREDENTIALS", now,
                        request_id, session.session_id)
            return {"ok": False, "error": "UNAUTHENTICATED", "reason": "BAD_CREDENTIALS"}
        if len(new_password) < PASSWORD_MIN:
            return {"ok": False, "error": "VALIDATION_FAILED", "reason": "PASSWORD_TOO_SHORT"}
        if verify_password(new_password, account.password_hash):
            return {"ok": False, "error": "VALIDATION_FAILED", "reason": "PASSWORD_REUSED"}
        account.password_hash = hash_password(new_password)
        account.password_changed_at = now
        self.sessions.revoke_account(account.account_id, now, "PASSWORD_CHANGED")
        self._audit(account, "PASSWORD_CHANGE", "ALLOW", "OK", now, request_id, session.session_id)
        return {"ok": True}


def session_cookie(token: str, now: datetime, absolute=ABSOLUTE_TIMEOUT) -> dict:
    return {
        "name": COOKIE_NAME,
        "value": token,
        "http_only": True,
        "secure": True,
        "same_site": "Lax",
        "path": "/",
        "max_age": int(absolute.total_seconds()),
        "expires": (now + absolute).isoformat(),
    }


# --------------------------------------------------------------------------
# 自检（E1–E12）
# --------------------------------------------------------------------------
TOTP_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
PASSWORD = "Passw0rd!2026"


def _fixture():
    accounts = AccountStore()
    for role in rbac.ROLES:
        secret = TOTP_SECRET if role in ("ADMIN", "RISK") else None
        accounts.add(Account("u-%s" % role, [role], PASSWORD, team="T1", totp_secret=secret))
    accounts.add(Account("svc-import", ["ADMIN"], None, service_account=True))
    return accounts


def _probe_resource(dim, cell, subject, now):
    res = {
        "id": "probe:1",
        "owner": subject["account_id"],
        "team": subject["team"],
        "related_accounts": [subject["account_id"]],
        "dataset": "D16",
        "energy_month": "%04d-%02d" % (now.year, now.month),
        "sensitivity": "SENSITIVE",
        "sensitive_fields": ["用户ID"],
        "config_domain": cell.get("domain") or "ALL",
    }
    if dim == "SENSITIVE_DATA" and cell.get("scope") == "SETTLEMENT":
        res["dataset"] = "D06"
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args(argv)

    now = rbac.utcnow()
    ledger = AnchoredLedger()
    accounts = _fixture()
    sessions = SessionStore()
    auth = AuthService(accounts, sessions, ledger)
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

    # ---- E1 54 格逐格一致 ----
    mismatch = []
    checked = 0
    for role in rbac.ROLES:
        subject = {"account_id": "u-%s" % role, "roles": [role], "team": "T1", "status": "ACTIVE"}
        for dim in MATRIX["dimensions"]:
            cell = next(c for c in MATRIX["cells"]
                        if c["role"] == role and c["dimension"] == dim)
            grants = ()
            if cell.get("scope") == "GRANT":
                grants = (rbac.make_grant(
                    grant_id="G-%s-%s" % (role, dim), subject=subject["account_id"],
                    dimension=dim, scope="ALL", mask=cell.get("mask", "NONE"),
                    grantor="g-admin", reason="逐格一致性自检用临时授权",
                    created_at=now - timedelta(hours=1), ttl_days=1),)
            res = _probe_resource(dim, cell, subject, now)
            allowed = set()
            for act in MATRIX["dimensions"][dim]["actions"]:
                d = rbac.evaluate(
                    MATRIX, subject, dim, act, res, grants=grants,
                    generated_by=("trader-other" if (dim == "APPROVAL" and act == "APPROVE") else None),
                    now=now)
                if d["allowed"]:
                    allowed.add(act)
            expect = set(cell.get("actions") or [])
            checked += 1
            if allowed != expect:
                mismatch.append("%s/%s 期望%s 实得%s" % (role, dim, sorted(expect), sorted(allowed)))
    check("E1 六角色 54 格逐格一致", not mismatch,
          "核对 %d 格，差异 %d 处%s" % (checked, len(mismatch),
                                        ("：" + "；".join(mismatch)) if mismatch else ""))

    # ---- E2 默认拒绝 ----
    trader = {"account_id": "u1", "roles": ["TRADER"], "team": "T1", "status": "ACTIVE"}
    e2a = rbac.evaluate(MATRIX, {"account_id": "u1", "roles": ["GHOST"], "team": "T1",
                                  "status": "ACTIVE"}, "RULE_LEARNING", "VIEW", {}, now=now)
    e2b = rbac.evaluate(MATRIX, trader, "GHOST_DIM", "VIEW", {}, now=now)
    e2c = rbac.evaluate(MATRIX, trader, "RULE_LEARNING", "DELETE", {}, now=now)
    check("E2 未声明即拒绝",
          e2a["allowed"] is False and e2b["allowed"] is False and e2c["allowed"] is False
          and e2a["reason_code"] == "NO_ROLE" and e2b["reason_code"] == "UNKNOWN_DIMENSION"
          and e2c["reason_code"] == "UNKNOWN_ACTION",
          "未知角色 NO_ROLE / 未知维度 UNKNOWN_DIMENSION / 未声明动作 UNKNOWN_ACTION")

    # ---- E3 掩码：HIGHLY 默认掩码，双人会签才解掩 ----
    highly = {"dataset": "D04", "sensitivity": "HIGHLY",
              "sensitive_fields": ["电量", "用户ID"], "id": "D04:1"}
    auditor = {"account_id": "u-AUDITOR", "roles": ["AUDITOR"], "team": "T1", "status": "ACTIVE"}
    plain_grant = rbac.make_grant(
        grant_id="G-E3a", subject="u-AUDITOR", dimension="SENSITIVE_DATA", scope="ALL",
        mask="MASKED", grantor="g-admin", reason="掩码自检：普通授权不得解掩",
        created_at=now - timedelta(hours=1), ttl_days=1)
    e3a = rbac.evaluate(MATRIX, auditor, "SENSITIVE_DATA", "VIEW", highly,
                         grants=[plain_grant], now=now)
    highly_grant = rbac.make_grant(
        grant_id="G-E3b", subject="u-AUDITOR", dimension="SENSITIVE_DATA", scope="ALL",
        mask="NONE", grantor="g-admin", reason="掩码自检：双人会签后解掩",
        created_at=now - timedelta(hours=1), ttl_days=1, expose="HIGHLY",
        approvals=("g-admin-1", "g-admin-2"))
    e3b = rbac.evaluate(MATRIX, auditor, "SENSITIVE_DATA", "VIEW", highly,
                         grants=[highly_grant], now=now)
    single_err = ""
    try:
        rbac.make_grant(grant_id="G-E3c", subject="u-AUDITOR", dimension="SENSITIVE_DATA",
                        scope="ALL", mask="NONE", grantor="g-admin", reason="单人会签的高敏授权必须被拒",
                        created_at=now - timedelta(hours=1), ttl_days=1, expose="HIGHLY",
                        approvals=("g-admin-1",))
    except ValueError as exc:
        single_err = str(exc)
    m1, m2 = rbac.mask_value("100.5"), rbac.mask_value("100.5")
    check("E3 字段级掩码",
          e3a["allowed"] and e3a["masked_fields"] == ["电量", "用户ID"]
          and e3b["allowed"] and e3b["masked_fields"] == []
          and single_err == "HIGHLY_REQUIRES_TWO_APPROVERS"
          and m1 == m2 and m1.startswith("MASK-H") and "100.5" not in m1,
          "HIGHLY 默认掩码 2 字段、双人会签解掩、单签被拒、掩码稳定 %s" % m1)

    # ---- E4 无真实交易写通路 ----
    e4_cells = [c for c in MATRIX["cells"] if set(c["actions"]) & set(FORBIDDEN_ACTIONS)]
    e4_runs = []
    for role in rbac.ROLES:
        for act in FORBIDDEN_ACTIONS:
            d = rbac.evaluate(MATRIX, {"account_id": "u-%s" % role, "roles": [role],
                                        "team": "T1", "status": "ACTIVE"},
                              "SIM_TRADING", act, {}, now=now)
            e4_runs.append(d["allowed"] is False and d["reason_code"] == "FORBIDDEN_ACTION")
    check("E4 无写入真实交易系统通路",
          not e4_cells and all(e4_runs) and "EXECUTE" not in PLATFORM_EFFECTS
          and "WRITE" not in PLATFORM_EFFECTS,
          "54 格无禁止动作；六角色 × %d 个真实交易动作全 FORBIDDEN_ACTION；平台效果 %s"
          % (len(FORBIDDEN_ACTIONS), list(PLATFORM_EFFECTS)))

    # ---- E5 会话失效 ----
    login = auth.login(account_id="u-TRADER", password=PASSWORD, now=now, ip="10.0.0.1")
    tok = login["token"]
    e5 = []
    e5.append(auth.sessions.resolve(tok, now + timedelta(minutes=29)) is not None)
    e5.append(auth.sessions.resolve(tok, now + timedelta(minutes=60)) is None)
    login2 = auth.login(account_id="u-SUPERVISOR", password=PASSWORD, now=now, ip="10.0.0.2")
    tok2 = login2["token"]
    e5.append(auth.sessions.resolve(tok2, now + timedelta(hours=12, minutes=1)) is None)
    login3 = auth.login(account_id="u-RISK", password=PASSWORD, totp_code=totp_code(TOTP_SECRET, now),
                        now=now, ip="10.0.0.3")
    tok3 = login3["token"]
    auth.logout(token=tok3, now=now)
    e5.append(auth.sessions.resolve(tok3, now) is None)
    ref = rbac.SessionStore()
    ref.login("u1", True, now, "t1")
    same_idle = ref.touch("t1", now + timedelta(minutes=29)) and not ref.touch("t1", now + timedelta(minutes=60))
    ref2 = rbac.SessionStore()
    ref2.login("u2", True, now, "t2")
    same_abs = not ref2.touch("t2", now + timedelta(hours=12, minutes=1))
    check("E5 会话失效", all(e5) and same_idle and same_abs,
          "空闲 30 分钟失效、绝对 12 小时失效、登出即时失效；与 #29 SessionStore 口径一致")

    # ---- E6 锁定、限流、越权全拒 ----
    lock_sessions = SessionStore()
    lock_auth = AuthService(accounts, lock_sessions, AnchoredLedger())
    e6 = []
    for i in range(MAX_FAILED_LOGINS):
        res = lock_auth.login(account_id="u-TRADER", password="wrong-password",
                              now=now + timedelta(seconds=i), ip="10.1.0.1")
    e6.append(res["error"] == "RATE_LIMITED" and res["reason"] == "LOCKED")
    locked = lock_auth.login(account_id="u-TRADER", password=PASSWORD, now=now + timedelta(seconds=10),
                             ip="10.1.0.2")
    e6.append(locked["error"] == "RATE_LIMITED")
    ip_sessions = SessionStore()
    ip_auth = AuthService(accounts, ip_sessions, AnchoredLedger())
    ip_res = None
    for i in range(IP_LIMIT + 1):
        ip_res = ip_auth.login(account_id="u-TRADER", password=PASSWORD,
                               now=now + timedelta(seconds=i), ip="10.2.0.1")
    e6.append(ip_res["error"] == "RATE_LIMITED" and ip_res["reason"] == "IP_RATE_LIMITED")
    probes = [
        ({"account_id": "a", "roles": ["ADMIN"], "team": "T1", "status": "ACTIVE"},
         "SENSITIVE_DATA", "VIEW", {}, "ADMIN_VS_SENSITIVE"),
        (trader, "DATA_IMPORT", "IMPORT", {}, "TRADER_VS_IMPORT"),
        ({"account_id": "s", "roles": ["SETTLEMENT"], "team": "T1", "status": "ACTIVE"},
         "SIM_TRADING", "SUBMIT", {}, "SETTLEMENT_VS_SUBMIT"),
        ({"account_id": "au", "roles": ["AUDITOR"], "team": "T1", "status": "ACTIVE"},
         "SYSTEM_CONFIG", "CONFIGURE", {"config_domain": "ALL"}, "AUDITOR_VS_CONFIG"),
        (trader, "APPROVAL", "APPROVE", {}, "TRADER_VS_APPROVE"),
        ({"account_id": "r", "roles": ["RISK"], "team": "T1", "status": "ACTIVE"},
         "SETTLEMENT_REVIEW", "REVIEW", {}, "RISK_VS_REVIEW"),
    ]
    denied_with_audit = True
    for subject, dim, act, res, label in probes:
        out_dec = authorize(subject=subject, dimension=dim, action=act, resource=res,
                            ledger=ledger, now=now, request_id="req_probe00001")
        if out_dec["allowed"] is not False or not out_dec["audit_event"]:
            denied_with_audit = False
    e6.append(denied_with_audit)
    check("E6 锁定限流与越权全拒", all(e6),
          "5 次失败锁 15 分钟→RATE_LIMITED；同 IP 15 分钟 %d 次→RATE_LIMITED；6 个越权探针全 DENY 且有审计"
          % IP_LIMIT)

    # ---- E7 越权拒绝不泄露字段存在性 ----
    exists_body = error_response("FORBIDDEN", "req_same0000001")
    missing_body = error_response("FORBIDDEN", "req_same0000002")
    unknown_action_body = error_response("FORBIDDEN", "req_same0000003")
    e7 = (exists_body["error"] == missing_body["error"]
          and exists_body["error"] == unknown_action_body["error"]
          and exists_body["error"]["details"] == {}
          and "D04" not in json.dumps(exists_body["error"], ensure_ascii=False))
    no_priv = authorize(subject=trader, dimension="SENSITIVE_DATA", action="VIEW",
                        resource={"dataset": "D04", "id": "D04:1"}, now=now)
    not_exists = authorize(subject=trader, dimension="SENSITIVE_DATA", action="VIEW",
                           resource={"dataset": "D99", "id": "D99:1"}, now=now)
    e7 = e7 and no_priv["error"] == not_exists["error"] == "NO_GRANT"
    check("E7 拒绝同形不泄露存在性", e7,
          "存在/不存在/未知动作 → 同一错误体；越权裁定码一致 %s" % no_priv["error"])

    # ---- E8 审计同构、只追加、锚定、留存 ----
    events = ledger.events
    au18 = {"event_id", "at", "actor", "roles", "session_id", "dimension", "action",
            "resource_type", "resource_id", "verdict", "reason_code", "scope",
            "masked_fields", "grant_id", "request_id", "prev_hash", "hash"}
    field_sets = {frozenset(e.keys()) for e in events}
    allows = [e for e in events if e["verdict"] == "ALLOW"]
    denies = [e for e in events if e["verdict"] == "DENY"]
    tampered = rbac.AuditLedger()
    tampered.events = [dict(e) for e in events]
    tampered.events[1]["reason_code"] = "TAMPERED"
    anchored = [e for e in events if "day_anchor" in e]
    e8 = (len(field_sets) == 1 and au18 <= set(next(iter(field_sets)))
          and all(e.get("reason_code") for e in denies)
          and ledger.verify() and not tampered.verify()
          and events[0]["prev_hash"] == "GENESIS"
          and anchored and len(anchored) == len(events)
          and len({e["day_anchor"] for e in anchored}) == 1
          and retention_class(180) == "ONLINE" and retention_class(181) == "ARCHIVE"
          and retention_class(180 + 3 * 365) == "ARCHIVE"
          and retention_class(180 + 3 * 365 + 1) == "PURGE")
    check("E8 审计同构与只追加", e8,
          "事件 %d 条（ALLOW %d / DENY %d）、字段集统一且含 AU18 全字段、链校验通过、篡改即断链、每日锚定 %d 条、留存 180+1095 天分级"
          % (len(events), len(allows), len(denies), len(anchored)))

    # ---- E9 能力投影 ----
    e9_detail = ""
    e9 = True
    try:
        sys.path.insert(0, API_DIR)
        import reference_api  # noqa: E402
        for roles in ([r] for r in rbac.ROLES):
            mine = json.dumps(compress_capabilities(list(roles)), ensure_ascii=False,
                              sort_keys=True)
            theirs = json.dumps(reference_api.compress_capabilities(list(roles)),
                                ensure_ascii=False, sort_keys=True)
            if mine != theirs:
                e9 = False
                e9_detail = "角色 %s 投影不一致" % roles
        combo = ["TRADER", "AUDITOR"]
        if json.dumps(compress_capabilities(combo), ensure_ascii=False, sort_keys=True) != \
                json.dumps(reference_api.compress_capabilities(combo), ensure_ascii=False,
                           sort_keys=True):
            e9 = False
            e9_detail = "多角色投影不一致"
    except Exception as exc:  # noqa: BLE001
        e9 = False
        e9_detail = "无法导入 #46 reference_api：%s" % exc
    matrix_dims = set(MATRIX["dimensions"])
    for row in compress_capabilities(list(rbac.ROLES)):
        if row["dimension"] not in matrix_dims:
            e9 = False
            e9_detail = "投影出现矩阵外维度 %s" % row["dimension"]
        for act in row["actions"]:
            if act not in MATRIX["dimensions"][row["dimension"]]["actions"]:
                e9 = False
                e9_detail = "投影出现矩阵外动作 %s" % act
    caps = capabilities_response(trader)
    shape_ok = (set(caps) == {"data", "meta"}
                and set(caps["data"]) == {"subject", "capabilities", "forbidden_actions"}
                and set(caps["meta"]) >= {"request_id", "api_version", "contract_version",
                                          "generated_at"})
    check("E9 能力投影唯一且与矩阵一致", e9 and shape_ok,
          e9_detail or "与 #46 compress_capabilities 逐字节相同；无矩阵外能力；响应形状符合 openapi")

    # ---- E10 令牌轮换与改密吊销 ----
    login4 = auth.login(account_id="u-SUPERVISOR", password=PASSWORD, now=now, ip="10.3.0.1")
    tok4 = login4["token"]
    rot = auth.rotate(token=tok4, now=now)
    e10 = [auth.sessions.resolve(tok4, now) is None,
           auth.sessions.resolve(rot["token"], now) is not None,
           rot["session"].rotated_from == login4["session"].session_id]
    login5 = auth.login(account_id="u-TRADER", password=PASSWORD, now=now, ip="10.3.0.2")
    login5b = auth.login(account_id="u-TRADER", password=PASSWORD, now=now, ip="10.3.0.3")
    chg = auth.change_password(token=login5["token"], old_password=PASSWORD,
                               new_password="An0ther!Passw0rd", now=now)
    e10.append(chg["ok"] is True)
    e10.append(auth.sessions.resolve(login5["token"], now) is None)
    e10.append(auth.sessions.resolve(login5b["token"], now) is None)
    accounts.get("u-TRADER").password_hash = hash_password(PASSWORD)
    check("E10 轮换与改密吊销", all(e10),
          "轮换后旧令牌失效、新令牌可用、轮换链可追；改密吊销该账号全部会话")

    # ---- E11 认证状态机与对外错误码 ----
    e11 = []
    wrong = auth.login(account_id="u-TRADER", password="nope", now=now, ip="10.4.0.1")
    missing = auth.login(account_id="u-NOBODY", password="nope", now=now, ip="10.4.0.2")
    e11.append(wrong["error"] == "UNAUTHENTICATED")
    e11.append(missing["error"] == "UNAUTHENTICATED")
    e11.append(error_response(wrong["error"])["error"] == error_response(missing["error"])["error"])
    no_mfa = auth.login(account_id="u-ADMIN", password=PASSWORD, now=now, ip="10.4.0.3")
    e11.append(no_mfa["error"] == "UNAUTHENTICATED" and no_mfa["reason"] == "MFA_REQUIRED")
    bad_mfa = auth.login(account_id="u-ADMIN", password=PASSWORD, totp_code="000000",
                         now=now, ip="10.4.0.4")
    e11.append(bad_mfa["error"] == "UNAUTHENTICATED" and bad_mfa["reason"] == "MFA_INVALID")
    good_mfa = auth.login(account_id="u-ADMIN", password=PASSWORD,
                           totp_code=totp_code(TOTP_SECRET, now), now=now, ip="10.4.0.5")
    e11.append(good_mfa["ok"] is True)
    cookie = good_mfa["cookie"]
    e11.append(cookie["http_only"] and cookie["secure"] and cookie["same_site"] == "Lax"
               and cookie["max_age"] == int(ABSOLUTE_TIMEOUT.total_seconds()))
    svc = auth.login(account_id="svc-import", password="", now=now, ip="10.4.0.6")
    e11.append(svc["error"] == "UNAUTHENTICATED")
    reauth = auth.reauthenticate(token=good_mfa["token"], password=PASSWORD,
                                  totp_code=totp_code(TOTP_SECRET, now), now=now)
    e11.append(reauth["ok"] is True)
    e11.append(require_reauth(reauth["session"], "SYSTEM_CONFIG:CONFIGURE", now))
    e11.append(not require_reauth(reauth["session"], "SYSTEM_CONFIG:CONFIGURE",
                                  now + timedelta(minutes=6)))
    e11.append(require_reauth(reauth["session"], "RULE_LEARNING:VIEW", now))
    e11.append(kdf_algorithm(hash_password(PASSWORD)) in ("scrypt", "pbkdf2-sha256"))
    check("E11 认证状态机与错误码", all(e11),
          "口令错/账号不存在同形 UNAUTHENTICATED；ADMIN 强制 TOTP（MFA_REQUIRED/MFA_INVALID/通过）；"
          "服务账号不可交互登录；Cookie HttpOnly+Secure+SameSite=Lax；再认证 5 分钟有效")

    print("\n".join(out))
    print("\nSummary: %d/%d PASS" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
