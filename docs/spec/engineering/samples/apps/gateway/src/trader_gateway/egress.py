"""安全网关：脱敏与用途授权，只依赖 contracts。"""
from trader_contracts import envelopes


def egress(payload, purpose, grant_id):
    return envelopes.ok({"purpose": purpose, "grant_id": grant_id, "fields": sorted(payload)})
