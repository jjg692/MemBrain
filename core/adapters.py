# core/adapters.py
import json
import ollama
from openai import OpenAI
import re


# ================== LLM 适配器（仅用于工具调用模型） ==================
class LLMAdapter:
    def chat_with_tools(self, messages, tools, **kwargs):
        raise NotImplementedError
    
    def chat(self, messages, **kwargs):
        """纯文本对话（无工具调用），用于主模型"""
        return self.chat_with_tools(messages, tools=None, **kwargs)


class OllamaAdapter(LLMAdapter):
    def __init__(self, model: str):
        self.model = model

    def chat(self, messages, **kwargs):
        """主模型纯文本对话（无工具调用）"""
        params = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False
        }
        # 处理图片
        if "images" in kwargs and kwargs["images"]:
            params["images"] = kwargs["images"]
        
        response = ollama.chat(**params)
        return {"content": response.get("message", {}).get("content", "")}


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
        try:
            response = ollama.chat(**params)
        except Exception as e:
            print(f"[OllamaAdapter] 调用失败: {e}")
            return {"content": "", "tool_calls": []}
        msg = response.get("message", {})
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls", [])
        
        # ========== MiniCPM-V 4.6 特殊处理：从 content 里解析 tool_call ==========
        # 如果模型没有返回标准的 tool_calls，但 content 里包含 <tool_call> 标签，手动解析
        if not tool_calls and "<tool_call>" in content:
            parsed = self._parse_minicpm_tool_calls(content)
            if parsed:
                tool_calls = parsed
                # 移除 content 里的 tool_call 块，只保留自然语言部分
                content = re.sub(r'\s*<tool_call>.*?</tool_call>', '', content, flags=re.DOTALL).strip()
        
        return {
            "content": content,
            "tool_calls": tool_calls
        }
    
    def _parse_minicpm_tool_calls(self, content: str) -> list:
        """解析 MiniCPM-V 4.6 的 <tool_call> 格式"""
        pattern = r'<tool_call>\s*<function=(.*?)>\s*(?:<parameter=(.*?)>(.*?)</parameter>)?\s*</tool_call>'
        matches = re.findall(pattern, content, re.DOTALL)
        if not matches:
            return []
        
        tool_calls = []
        for match in matches:
            func_name = match[0].strip()
            if len(match) >= 3 and match[1]:
                args = {match[1].strip(): match[2].strip()}
            else:
                args = {}
            tool_calls.append({
                "function": {
                    "name": func_name,
                    "arguments": args
                }
            })
        return tool_calls


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