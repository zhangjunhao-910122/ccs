"""数据层：只依赖 contracts。"""
from trader_contracts import envelopes


def fetch(dataset, as_of):
    return envelopes.ok({"dataset": dataset, "as_of": as_of})
