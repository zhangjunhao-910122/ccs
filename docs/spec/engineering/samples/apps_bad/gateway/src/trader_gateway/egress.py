"""违规样例：出域函数缺用途授权参数。"""
from trader_contracts import envelopes


def egress(payload, purpose):
    return envelopes.ok({"purpose": purpose})
