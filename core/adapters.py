"""
LLM 适配器
支持 Ollama（本地）与 OpenAI 兼容接口（DeepSeek 等）
统一暴露 chat / chat_with_tools 接口
"""
from typing import List, Optional

import ollama
from ollama import Client as OllamaClient
from openai import OpenAI


class LLMAdapter:
    """抽象基类"""

    def chat(self, messages: List[dict], **kwargs) -> str:
        """纯文本对话，返回字符串"""
        raise NotImplementedError

    def chat_with_tools(self, messages: List[dict], tools: Optional[List[dict]] = None, **kwargs) -> dict:
        """
        带工具调用的对话。
        返回: {"content": str, "tool_calls": [ {function:{name,arguments(dict)}} ]}
        """
        raise NotImplementedError


class OllamaAdapter(LLMAdapter):
    """Ollama 本地模型适配器"""

    def __init__(self, model: str, host: Optional[str] = None):
        self.model = model
        self._client = OllamaClient(host=host) if host else ollama
        self.temperature = None  # None=用 Ollama 默认; 由使用方按需设置

    def set_temperature(self, value: Optional[float]):
        self.temperature = value
        return self

    def _base_params(self, messages, images=None):
        params = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,
        }
        if self.temperature is not None:
            params["temperature"] = self.temperature
        if images:
            # Ollama 图片放在最后一条 user 消息的 images 字段
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    msg["images"] = images
                    break
        return params

    def chat(self, messages: List[dict], images: Optional[List[str]] = None, **kwargs) -> str:
        try:
            response = self._client.chat(**self._base_params(messages, images))
            return (response.get("message", {}) or {}).get("content", "")
        except Exception as e:
            return f"[Ollama 调用失败] {e}"

    def chat_with_tools(self, messages, tools=None, images=None, **kwargs) -> dict:
        params = self._base_params(messages, images)
        if tools:
            params["tools"] = tools
        try:
            response = self._client.chat(**params)
        except Exception as e:
            return {"content": f"[Ollama 调用失败] {e}", "tool_calls": []}
        msg = response.get("message", {}) or {}
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls", [])

        # MiniCPM-V 兼容：从 content 解析 <tool_call> 标签
        if not tool_calls and "<tool_call>" in content:
            parsed = self._parse_minicpm_tool_calls(content)
            if parsed:
                tool_calls = parsed
                import re
                content = re.sub(r"\s*<tool_call>.*?</tool_call>", "", content, flags=re.DOTALL).strip()

        return {"content": content, "tool_calls": tool_calls}

    @staticmethod
    def _parse_minicpm_tool_calls(content: str) -> list:
        import re
        pattern = r"<tool_call>\s*<function=(.*?)>\s*(?:<parameter=(.*?)>(.*?)</parameter>)?\s*</tool_call>"
        matches = re.findall(pattern, content, re.DOTALL)
        calls = []
        for m in matches:
            args = {m[1].strip(): m[2].strip()} if len(m) >= 3 and m[1] else {}
            calls.append({"function": {"name": m[0].strip(), "arguments": args}})
        return calls


class OpenAICompatAdapter(LLMAdapter):
    """OpenAI 兼容接口适配器（DeepSeek / 百度等）"""

    def __init__(self, api_key: str, model: str, base_url: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def chat(self, messages: List[dict], **kwargs) -> str:
        resp = self.client.chat.completions.create(model=self.model, messages=messages)
        return resp.choices[0].message.content or ""

    def chat_with_tools(self, messages, tools=None, **kwargs) -> dict:
        openai_tools = self._convert_tools(tools) if tools else None
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=openai_tools,
            tool_choice="auto" if openai_tools else None,
        )
        msg = resp.choices[0].message
        return {
            "content": msg.content or "",
            "tool_calls": [
                {
                    "function": {
                        "name": tc.function.name,
                        "arguments": self._safe_loads(tc.function.arguments),
                    },
                    "id": tc.id,
                }
                for tc in (msg.tool_calls or [])
            ],
        }

    @staticmethod
    def _safe_loads(text):
        import json
        try:
            return json.loads(text)
        except Exception:
            return {}

    @staticmethod
    def _convert_tools(tools):
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "parameters": t["function"].get("parameters", {}),
                },
            }
            for t in tools
        ]
