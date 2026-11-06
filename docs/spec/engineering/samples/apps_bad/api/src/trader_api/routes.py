"""访问层后端：HTTP 边界与鉴权中间件。"""
from trader_agents import planner
from trader_contracts import envelopes
from trader_core import engine
from trader_data import store
from trader_gateway import egress


def get_dataset():
    return envelopes.ok(store.fetch("d04", "2026-01-01"))
