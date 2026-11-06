"""智能体层：编排与模型调用入口。"""
from trader_contracts import envelopes
from trader_core import engine
from trader_gateway import egress
from trader_model import client


def plan(dataset):
    egress({"dataset": dataset}, purpose="回测报告", grant_id="g-1")
    client.LinkAIClient().complete("x", "model-a")
    return envelopes.ok({"value": engine.settle(dataset, "2026-01-01")})
