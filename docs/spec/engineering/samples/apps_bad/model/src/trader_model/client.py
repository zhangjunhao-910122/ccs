"""模型服务层：LinkAI 适配，只依赖 contracts。"""
from trader_contracts import envelopes


class LinkAIClient:
    def complete(self, prompt, model_id):
        return envelopes.suggestion({"model_id": model_id, "text": prompt})
