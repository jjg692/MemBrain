# core/adapters.py
import json
import ollama
from openai import OpenAI


# ================== LLM 适配器（仅用于工具调用模型） ==================
class LLMAdapter:
    def chat_with_tools(self, messages, tools, **kwargs):
        raise NotImplementedError


class OllamaAdapter(LLMAdapter):
    def __init__(self, model: str):
        self.model = model

    def chat_with_tools(self, messages, tools, **kwargs):
        # 如果 tools 为空，不传 tools 参数
        params = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False
        }
        if tools:
            params["tools"] = tools
        # 处理图片（如果存在）
        if "images" in kwargs and kwargs["images"]:
            # Ollama 的图片放在最后一条 user 消息的 images 字段中
            # 这里假设调用者已处理好，直接透传
            params["images"] = kwargs["images"]
        response = ollama.chat(**params)
        msg = response.get("message", {})
        return {
            "content": msg.get("content", ""),
            "tool_calls": msg.get("tool_calls", [])
        }


class DeepSeekAdapter(LLMAdapter):
    def __init__(self, api_key: str, model: str = "deepseek-chat", base_url: str = "https://api.deepseek.com/v1"):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def chat_with_tools(self, messages, tools, **kwargs):
        openai_tools = self._convert_tools(tools) if tools else None
        # 注意：DeepSeek 多模态暂时不处理，忽略 images
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=openai_tools if openai_tools else None,
            tool_choice="auto" if openai_tools else None
        )
        msg = response.choices[0].message
        return {
            "content": msg.content or "",
            "tool_calls": [
                {
                    "function": {"name": tc.function.name, "arguments": json.loads(tc.function.arguments)},
                    "id": tc.id
                }
                for tc in (msg.tool_calls or [])
            ]
        }

    def _convert_tools(self, ollama_tools):
        if not ollama_tools:
            return None
        return [{
            "type": "function",
            "function": {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "parameters": t["function"].get("parameters", {})
            }
        } for t in ollama_tools]