"""违规样例一：层间越界 + 直连公网。

违反 SPEC §3.1 / §3.2：trader_core 不得依赖模型层；
违反 SPEC §4.2 与 AI 控制边界：出域只许经 trader_gateway，
核心层不得直接外呼公网模型，也不得用默认值兜底环境变量。
"""
import os

import numpy as np
import requests

from trader_contracts import envelopes
from trader_model import client

MODE = os.environ.get("APP_MODE", "dev")


def settle(dataset, as_of):
    requests.post("https://model.example/v1/chat", json={"dataset": dataset})
    client.LinkAIClient().complete("x", "model-a")
    return envelopes.numeric({"n": len(np.zeros(1)), "mode": MODE})
