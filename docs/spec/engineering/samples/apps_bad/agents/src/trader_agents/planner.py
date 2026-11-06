"""违规样例五：改写历史数据 + 跨包深层导入。

违反 AI 控制边界：不得改写或删除历史数据与审计日志；
违反 SPEC §3.3 S4：跨包导入只允许顶层包名。
"""
from trader_contracts import envelopes
from trader_core.engine.settlement import settle


def run(dataset):
    delete_history(dataset)
    return envelopes.ok(settle(dataset, "2026-01-01"))
