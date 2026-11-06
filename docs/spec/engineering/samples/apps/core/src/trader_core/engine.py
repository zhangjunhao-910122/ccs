"""专业能力层：确定性数值，依赖 contracts 与 data，不依赖模型层。"""
import numpy as np

from trader_contracts import envelopes
from trader_data import store


def settle(dataset, as_of):
    rows = store.fetch(dataset, as_of)
    return envelopes.numeric({"n": len(rows)})
