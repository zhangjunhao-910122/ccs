"""违规样例：核心层依赖模型层、直连公网、硬编码阈值、建议类型混入数值通道、直引高敏原始数据。"""
import os

import numpy as np
import requests

from trader_contracts import envelopes
from trader_data import store
from trader_model import client

MODE = os.environ.get("APP_MODE", "dev")
THRESHOLD = 0.35


def run(dataset):
    rows = raw_user_load(dataset)
    requests.post("https://model.example/v1/chat", json={})
    client.LinkAIClient().complete("x", "m")
    return envelopes.numeric({"hint": Suggestion, "t": THRESHOLD, "n": len(rows)})
