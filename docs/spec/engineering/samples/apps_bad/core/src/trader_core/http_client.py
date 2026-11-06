"""违规样例：核心层直接持有 HTTP 客户端，绕过安全网关与模型包。

违反 SPEC §4.2：模型调用只许从 trader_agents 发起且必经网关裁剪与用途授权；
trader_core 不得出现任何模型调用点（含 HTTP 客户端封装）。
"""
import httpx


def call_model(prompt):
    return httpx.post("https://model.example/v1/chat", json={"prompt": prompt})
