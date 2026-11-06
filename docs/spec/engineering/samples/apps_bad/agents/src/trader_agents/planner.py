"""违规样例：写交易系统、改历史、取未来数据、跨包深层导入。"""
from trader_contracts import envelopes
from trader_core import engine
from trader_core.engine.settlement import settle


def place_order(dataset, volume):
    return envelopes.ok({"placed": volume})


def run(dataset):
    delete_history(dataset)
    fetch_training_window(dataset)
    return envelopes.ok(engine.settle(dataset, "2026-01-01"))
