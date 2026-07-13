import os
import time
import json
from typing import TypedDict, Annotated, List, Literal, Optional
from datetime import datetime
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langgraph.graph.message import add_messages
import ollama
import requests  # 新增：用于搜索API

# ================== 工具定义（Function Calling） ==================
# 1. Ollama 原生格式（JSON Schema）
SEARCH_TOOL_OLLAMA = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": "当用户询问实时信息，如新闻、天气、最新动态、或你无法确定的问题时，使用此工具进行联网搜索。注意：如果用户提到相对时间（如'今天'），请结合上下文中的当前日期来确定具体搜索日期。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，应该简洁明确，例如'北京天气'、'2026年奥斯卡获奖名单'"
                }
            },
            "required": ["query"]
        }
    }
}

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


# ================== 定义 Agent 状态 ==================
class AgentState(TypedDict):
    """Agent 状态，包含消息历史和当前步骤"""
    messages: Annotated[List[dict], add_messages]
    user_id: str
    iteration: int  # 防止无限循环
    image: Optional[str]  
    query_type: Optional[str]


# ================== 搜索 API ==================
def search_baidu_api(query: str, max_results: int = 3) -> str:
    """使用百度AI搜索官方API（每天100次免费）"""
    api_key = os.getenv("BAIDU_API_KEY")
    if not api_key:
        return "百度API Key未配置，请检查.env文件"
    
    url = "https://qianfan.baidubce.com/v2/ai_search/web_search"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "messages": [{"content": query, "role": "user"}],
        "search_source": "baidu_search_v2",
        "resource_type_filter": [{"type": "web", "top_k": max_results}]
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        data = resp.json()
        if data.get("error_code"):
            return f"搜索失败：{data.get('error_msg')}"
        results = data.get("references", [])
        if not results:
            return "没有搜到相关信息。"
        output = [f"{r.get('title', '无标题')}: {r.get('content', '')}" for r in results[:max_results]]
        return "\n".join(output)
    except Exception as e:
        return f"搜索失败：{e}"


# ================== 定义工具（复用现有搜索） ==================
@tool
def search_web(query: str) -> str:
    """当用户询问实时信息、新闻、天气、最新动态时，使用此工具进行联网搜索。
    Args:
        query: 搜索关键词
    """
    return search_baidu_api(query)  # 直接调用，不再从 web_app 导入


# ================== 构建 LangGraph Agent ==================
class LangGraphMemoryAgent:
    """
    基于 LangGraph 的 ReAct Agent，带记忆系统
    双模型分工：
    - tool_llm_model (7B, 默认 qwen2.5:7b)：负责判断是否需要调用工具（轻量决策）
    - llm_model (9B, 默认 qwen3.5:9b)：负责最终回复生成（高质量输出）
    """
    def __init__(self, memory, llm_model: str = "qwen3.5:9b", tool_llm_model: str = "qwen2.5:7b", system_prompt=None, tool_adapter=None, main_adapter=None):
        self.memory = memory
        self.llm_model = llm_model                 # 主模型（9B），用于最终回复
        self.tool_llm_model = tool_llm_model       # 工具模型（7B），用于判断是否搜索
        self.system_prompt = system_prompt         # 角色人设提示词
        # 工具模型适配器
        if tool_adapter is None:
            self.tool_adapter = OllamaAdapter(model=tool_llm_model)
        else:
            self.tool_adapter = tool_adapter
        # 主模型适配器
        if main_adapter is None:
            self.main_adapter = OllamaAdapter(model=llm_model)
        else:
            self.main_adapter = main_adapter
        self.graph = self._build_graph()
        # 短期对话历史：从向量库中加载最近 N 轮对话（持久化短期记忆）
        # 这样即使服务重启，也能恢复最近对话
        self.conversation_history = self._load_short_term_memory()
        # 如果加载失败或为空，则初始化为空列表
        if self.conversation_history is None:
            self.conversation_history = []
        # =========================================================

    def _load_short_term_memory(self, user_id: str = None, n: int = 10) -> List[dict]:
        """
        从向量库加载用户的短期记忆（最近 N 轮对话）
        如果未指定 user_id，则返回空列表（实际调用时会传入 user_id）
        """
        # 注意：在 chat 方法中会传入具体的 user_id，所以这里不直接使用
        # 此方法仅用于初始化，实际加载在 chat 方法中进行
        return []

    def _build_graph(self):
        """构建 LangGraph 图"""
        workflow = StateGraph(AgentState)
        workflow.add_node("agent", self._agent_node)
        workflow.add_node("tools", ToolNode([search_web]))
        workflow.set_entry_point("agent")
        workflow.add_node("handle_result", self._handle_result_node)
        workflow.add_conditional_edges(
            "agent",
            self._should_continue,
            {"tools": "tools", "end": END}
        )
        # tools 执行完后，直接进入 handle_result 节点（生成最终回复）
        workflow.add_edge("tools", "handle_result")
        
        # handle_result 完成后结束
        workflow.add_edge("handle_result", END)
        return workflow.compile()

    # ==================== 路由器方法 ====================
    def _classify_query(self, user_message: str) -> str:
        """
        调用工具模型判断问题类型。
        返回: "PERSONAL" | "REALTIME" | "HYBRID"
        """
        ROUTER_PROMPT = """
        你是一个智能分类器。判断用户问题属于哪一类。

        【分类规则】
        1. **PERSONAL**：用户的问题涉及"我"（用户）或"你-我"之间的关系。包括：
        - 打招呼、闲聊、情感表达（如"你好"、"今天心情不错"、"你好可爱啊"）
        - 个人偏好、习惯、历史对话内容（如"我喜欢吃辣"、"你记得我说过什么吗"）
        - 特征：不涉及客观事实查询，不需要联网搜索。社交性语言和个人记忆都属于这一类。

        2. **REALTIME**：用户询问的是客观的实时信息、事实、推荐。
        - 关键词："今天天气"、"推荐"、"评分"、"有什么景点"、"现在人多吗"
        - 示例："今天天气怎么样？"、"推荐几个好玩的餐馆"、"北京有什么景点？"
        - 特征：不涉及个人历史，只需要联网搜索就能回答

        3. **HYBRID**：用户的问题中包含了指代词（如"那个"、"上次说的"），需要先查记忆确定具体指代对象，然后再搜索。
        - 示例："我上次说想去的那个地方，现在人多吗？"、"之前提到的那家店，现在评价怎么样？"
        - 特征：问题里有一个"未知实体"，需要通过记忆找出来，然后再结合实时信息搜索
        - 判断技巧：如果问题里同时出现"我/之前/上次"和"现在/今天/推荐"等词，大概率是 HYBRID

        【输出格式】
        只输出分类名称：PERSONAL / REALTIME / HYBRID
        不要输出其他内容。

        用户问题：{user_message}
        """
        messages = [{"role": "system", "content": ROUTER_PROMPT.format(user_message=user_message)}]
        try:
            # 复用工具模型进行分类，但不传递 tools（不需要工具调用）
            result = self.tool_adapter.chat_with_tools(messages=messages, tools=None)
            content = result.get("content", "").strip().upper()
            # if "CHAT" in content:
            #     print(f"[路由器] 分类结果: CHAT")
            #     return "CHAT"
            if "PERSONAL" in content:
                print(f"[路由器] 分类结果: PERSONAL")
                return "PERSONAL"
            elif "HYBRID" in content:
                print(f"[路由器] 分类结果: HYBRID")
                return "HYBRID"
            else:
                print(f"[路由器] 分类结果: REALTIME")
                return "REALTIME"
        except Exception as e:
            print(f"[路由器] 调用失败，默认 REALTIME: {e}")
            return "REALTIME"

    # ==================== 强制搜索（用于 REALTIME 和 HYBRID） ====================
    def _force_search(self, query: str, state: AgentState) -> dict:
        """直接触发搜索，不经过工具模型判断"""
        iteration = state.get("iteration", 0)
        forced_tool_call = {
            "function": {
                "name": "search_web",
                "arguments": {"query": query}
            },
            "id": f"call_{int(time.time())}"
        }
        response_dict = {
            "role": "assistant",
            "content": "",
            "tool_calls": [forced_tool_call]
        }
        return {
            "messages": [response_dict],
            "iteration": iteration + 1,
            "query_type": state.get("query_type", "REALTIME")
        }

    # ==================== 只走记忆（PERSONAL） ====================
    def _handle_personal(self, user_message: str, state: AgentState) -> dict:
        """只走记忆，但如果记忆为空，让主模型自主决策是否搜索"""
        image = state.get("image", None)
        user_id = state.get("user_id", "default_user")
        iteration = state.get("iteration", 0)

        # ========== 混合检索（短期+长期） ==========
        # 1. 检索短期记忆（最近对话原文）
        short_term_results = self.memory.search(
            query=user_message,
            user_id=user_id,
            threshold=0.4,        # 阈值稍低，召回更多
            n_results=3,
            filter={"type": "short_term"}  # 只检索短期记忆
        )
        # 2. 检索长期记忆（摘要）
        long_term_results = self.memory.search(
            query=user_message,
            user_id=user_id,
            threshold=0.5,
            n_results=2,
            filter={"type": "long_term"}   # 只检索长期记忆
        )
        # 3. 合并结果（去重，优先短期记忆）
        all_memories = []
        # 短期记忆优先
        for item in short_term_results.get("results", []):
            all_memories.append(item["document"])
        # 添加长期记忆（避免重复）
        for item in long_term_results.get("results", []):
            if item["document"] not in all_memories:
                all_memories.append(item["document"])
        # 构建 memory_context（与原有逻辑兼容）
        memory_parts = []
        if all_memories:
            memory_parts.append("【相关记忆】\n" + "\n".join(f"- {t}" for t in all_memories))
        # 也获取最近3条（保留原有逻辑）
        recent = self.memory.get_recent(user_id=user_id, n=3)
        if recent:
            memory_parts.append("【最近对话】\n" + "\n".join(f"- {d}" for d in recent))
        memory_context = "\n\n".join(memory_parts) if memory_parts else None
        # ====================================================

        current_date = datetime.now().strftime("%Y年%m月%d日")

        # ========== 情况1：记忆库有信息，直接生成回复（不走搜索） ==========
        if memory_context:
            full_system_prompt = f"""{self.system_prompt}

            【当前日期】{current_date}

            {memory_context}

            用户最新消息：{user_message}
            """
            chat_messages = [{"role": "system", "content": full_system_prompt}]
            for msg in self.conversation_history[-10:]:
                chat_messages.append({"role": msg["role"], "content": msg["content"]})
            chat_messages.append({"role": "user", "content": user_message})

            final_reply = self._generate_with_main_model(chat_messages, image, tools=None)

            self.conversation_history.append({"role": "user", "content": user_message})
            self.conversation_history.append({"role": "assistant", "content": final_reply})

            return {
                "messages": [{"role": "assistant", "content": final_reply}],
                "iteration": iteration + 1,
                "image": image,
                "query_type": "PERSONAL"
            }

        # ========== 情况2：记忆库为空，让主模型自主决策是否搜索 ==========
        print(f"[PERSONAL] 记忆库为空，主模型自主决策是否搜索")

        full_system_prompt = f"""{self.system_prompt}

        【当前日期】{current_date}

        用户问了一个涉及个人历史的问题，但记忆库中没有相关信息。
        如果你知道答案，可以直接回答。
        如果你不确定，可以调用 search_web 工具搜索相关信息。

        用户最新消息：{user_message}
        """

        chat_messages = [{"role": "system", "content": full_system_prompt}]
        for msg in self.conversation_history[-10:]:
            chat_messages.append({"role": msg["role"], "content": msg["content"]})
        chat_messages.append({"role": "user", "content": user_message})

        result = self.main_adapter.chat_with_tools(
            messages=chat_messages,
            tools=[SEARCH_TOOL_OLLAMA]
        )

        tool_calls = result.get("tool_calls", [])
        content = result.get("content", "")

        if tool_calls:
            # 模型决定搜索
            search_query = tool_calls[0].get("function", {}).get("arguments", {}).get("query", user_message)
            print(f"[PERSONAL] 主模型决定搜索: {search_query}")
            result_search = self._force_search(search_query, state)
            result_search["query_type"] = "PERSONAL"
            return result_search
        else:
            # 模型认为自己知道，直接回答
            print(f"[PERSONAL] 主模型决定直接回答")
            final_reply = content or "啊咧？香澄还没想好怎么回答呢…让我们再试一次吧！"
            self.conversation_history.append({"role": "user", "content": user_message})
            self.conversation_history.append({"role": "assistant", "content": final_reply})
            return {
                "messages": [{"role": "assistant", "content": final_reply}],
                "iteration": iteration + 1,
                "image": image,
                "query_type": "PERSONAL"
            }

    # ==================== 指代消解处理（HYBRID） ====================
    def _handle_hybrid(self, user_message: str, state: AgentState) -> dict:
        """
        HYBRID 场景：处理包含指代词（如"那个"、"这款"、"刚才说的"）的问题。
        流程：
        1. 获取最近对话作为上下文。
        2. 调用工具模型进行指代消解，返回所有可能的候选实体。
        3. 根据候选数量决定：
           - 无候选：询问用户具体指什么。
           - 单候选：直接使用该实体，让主模型自主决定是否搜索。
           - 多候选：列出候选让用户选择。
        """
        user_id = state.get("user_id", "default_user")
        iteration = state.get("iteration", 0)
        image = state.get("image", None)

        # ========== 指代消解的上下文来自短期记忆（优先）和长期记忆（辅助） ==========
        # 1. 从向量数据库获取短期记忆（最近5轮对话原文）
        short_term_results = self.memory.search(
            query=user_message,
            user_id=user_id,
            threshold=0.4,
            n_results=5,
            where={"type": "short_term"}
        )
        short_term_texts = [item["document"] for item in short_term_results.get("results", [])]
        # 2. 从向量数据库获取相关长期记忆（摘要）
        long_term_results = self.memory.search(
            query=user_message,
            user_id=user_id,
            threshold=0.5,
            n_results=3,
            where={"type": "long_term"}
        )
        long_term_texts = [item["document"] for item in long_term_results.get("results", [])]
        # 3. 合并上下文：优先短期记忆，补充长期记忆
        context_parts = []
        if short_term_texts:
            context_parts.append("【最近对话】\n" + "\n".join(short_term_texts))
        if long_term_texts:
            context_parts.append("【相关历史摘要】\n" + "\n".join(long_term_texts))
        if not context_parts:
            # 如果向量库无结果，降级使用内存中的 conversation_history
            recent_history = self.conversation_history[-5:] if self.conversation_history else []
            if recent_history:
                context_parts.append("【最近对话】\n" + "\n".join([f"{msg['role']}: {msg['content']}" for msg in recent_history]))
        recent_context = "\n\n".join(context_parts) if context_parts else "（无近期对话历史）"
        # ================================================================

        # 2. 指代消解：调用工具模型（不绑定工具）获取所有可能的候选实体
        resolve_prompt = f"""
        你是一个指代消解器。请根据上下文，找出用户问题中的指代词（如"那个"、"这款"、"刚才说的"）可能指向的所有候选实体。

        【上下文】
        {recent_context}

        【用户问题】
        {user_message}

        【任务】
        1. 找出用户问题中的指代词可能指向的所有候选实体。
        2. 每个候选实体给出一个简短的理由，说明为什么它可能是指代对象。
        3. 如果只有一个明确的候选，输出：单候选：[候选实体]
        4. 如果有多个候选，输出：多候选：[候选实体1]、[候选实体2]、[候选实体3]
        5. 如果找不到候选，输出：无候选

        只输出以上格式，不要输出其他内容。
        """
        resolve_result = self.tool_adapter.chat_with_tools(
            messages=[{"role": "system", "content": resolve_prompt}],
            tools=None
        )
        resolve_text = resolve_result.get("content", "")
        print(f"[HYBRID] 指代消解结果: {resolve_text}")

        # 3. 解析候选实体
        import re
        candidates = []

        if "单候选" in resolve_text:
            match = re.search(r"单候选：(.+)", resolve_text)
            if match:
                candidates = [match.group(1).strip()]
        elif "多候选" in resolve_text:
            match = re.search(r"多候选：(.+)", resolve_text)
            if match:
                raw = match.group(1).strip()
                # 按中文顿号、逗号或空格分割
                candidates = re.split(r'[、，,、\s]+', raw)
                candidates = [c.strip() for c in candidates if c.strip()]
        elif "无候选" in resolve_text:
            candidates = []
        else:
            # 如果解析失败，降级处理：尝试从上下文中提取名词（这里简单置空，触发询问）
            print(f"[HYBRID] 指代结果格式异常，将请求用户澄清")
            candidates = []

        print(f"[HYBRID] 候选实体: {candidates}")

        # 4. 根据候选数量处理
        current_date = datetime.now().strftime("%Y年%m月%d日")

        if len(candidates) == 0:
            # 无候选：询问用户具体指什么
            print(f"[HYBRID] 无候选，请求用户澄清")
            ask_prompt = f"""
            你发现用户使用了"那个"、"这款"、"刚才说的"等指代词，但你不确定具体指什么。

            请生成一段友好的、符合角色人设的回复，向用户询问：
            1. 指出你听到了指代词，但不清楚具体指什么。
            2. 请用户描述一下具体指什么。

            用户的问题：{user_message}
            """
            ask_result = self.tool_adapter.chat_with_tools(
                messages=[{"role": "system", "content": ask_prompt}],
                tools=None
            )
            reply = ask_result.get("content", "呐呐～你刚才说的'那个'具体是指什么呢？告诉小香澄名字吧！")

            self.conversation_history.append({"role": "user", "content": user_message})
            self.conversation_history.append({"role": "assistant", "content": reply})
            return {
                "messages": [{"role": "assistant", "content": reply}],
                "iteration": iteration + 1,
                "image": image,
                "query_type": "PERSONAL"  # 标记为 PERSONAL，便于存储关系记忆
            }

        elif len(candidates) == 1:
            # 单候选：直接使用该实体，让主模型自主决策是否搜索
            referred_entity = candidates[0]
            print(f"[HYBRID] 单选: {referred_entity}")

            # 构造提示词，包含实体信息，并绑定工具让主模型自主决定
            system_context = f"""
            用户提到了一个具体的实体：{referred_entity}
            用户的问题：{user_message}

            请根据你的知识回答。如果你对这个实体了解不充分，或者需要实时信息，可以调用 search_web 工具。
            """
            full_system_prompt = f"""{self.system_prompt}

            【当前日期】{current_date}

            {system_context}
            """
            chat_messages = [{"role": "system", "content": full_system_prompt}]
            for msg in self.conversation_history[-10:]:
                chat_messages.append({"role": msg["role"], "content": msg["content"]})
            chat_messages.append({"role": "user", "content": user_message})

            # 调用主模型，绑定工具（主模型自己决定是否搜索）
            result = self.main_adapter.chat_with_tools(
                messages=chat_messages,
                tools=[SEARCH_TOOL_OLLAMA]
            )

            tool_calls = result.get("tool_calls", [])
            content = result.get("content", "")

            if tool_calls:
                # 主模型决定搜索
                search_query = tool_calls[0].get("function", {}).get("arguments", {}).get("query", user_message)
                print(f"[HYBRID] 主模型决定搜索: {search_query}")
                result_search = self._force_search(search_query, state)
                result_search["query_type"] = "HYBRID"
                return result_search
            else:
                # 主模型认为自己知道，直接回答
                final_reply = content or "啊咧？香澄还没想好怎么回答呢…让我们再试一次吧！"
                self.conversation_history.append({"role": "user", "content": user_message})
                self.conversation_history.append({"role": "assistant", "content": final_reply})
                return {
                    "messages": [{"role": "assistant", "content": final_reply}],
                    "iteration": iteration + 1,
                    "image": image,
                    "query_type": "HYBRID"
                }

        else:
            # 多候选：列出候选让用户选择
            candidates_text = "、".join(candidates)
            print(f"[HYBRID] 多选: {candidates}，请求用户确认")
            ask_prompt = f"""
            用户使用了指代词，你可能指向以下候选实体：{candidates_text}

            请生成一段友好的、符合角色人设的回复，向用户确认具体是指哪一个：
            1. 列出所有候选实体。
            2. 请用户从候选中选择。

            用户的问题：{user_message}
            """
            ask_result = self.tool_adapter.chat_with_tools(
                messages=[{"role": "system", "content": ask_prompt}],
                tools=None
            )
            reply = ask_result.get("content", f"呐呐～你说的'那个'是指 {candidates_text} 中的哪一个呢？告诉小香澄吧！")

            self.conversation_history.append({"role": "user", "content": user_message})
            self.conversation_history.append({"role": "assistant", "content": reply})
            return {
                "messages": [{"role": "assistant", "content": reply}],
                "iteration": iteration + 1,
                "image": image,
                "query_type": "PERSONAL"
            }

    # ==================== Agent 节点（路由） ====================
    def _agent_node(self, state: AgentState, config: RunnableConfig):
        # ==================== 路由分类 ====================
        messages = state["messages"]
        last_msg = messages[-1] if messages else None
        user_message = last_msg.content if last_msg and hasattr(last_msg, 'content') else ""


        # ========== 新增：检测“联网搜索”关键词 ==========
        if "联网搜索" in user_message:
            print(f"[路由] 检测到'联网搜索'关键词，强制走搜索")
            # 直接强制搜索，不经过路由器分类
            result = self._force_search(user_message, state)
            result["query_type"] = "REALTIME"
            return result
        # ================================================

        # 调用路由器分类
        query_type = self._classify_query(user_message)
        print(f"[路由] 问题类型: {query_type}")

        # 根据类型分流
        if query_type == "REALTIME":
            print(f"[路由] 实时信息，直接走搜索")
            result = self._force_search(user_message, state)
            result["query_type"] = "REALTIME"  
            return result
        elif query_type == "PERSONAL":
            print(f"[路由] 个人闲聊，只走记忆")
            result = self._handle_personal(user_message, state)
            result["query_type"] = "PERSONAL"  
            return result
        elif query_type == "HYBRID":
            print(f"[路由] 混合类型，记忆补全后搜索")
            result = self._handle_hybrid(user_message, state)
            result["query_type"] = "HYBRID"  
            return result
        else:
            # 默认走 REALTIME
            print(f"[路由] 无类型，直接走搜索")
            result = self._force_search(user_message, state)
            result["query_type"] = "REALTIME"  
            return result
    
    # ==================== 处理搜索结果 ====================
    def _handle_result_node(self, state: AgentState) -> dict:
        """处理搜索结果，生成最终回复（不经过路由）"""
        image = state.get("image", None)
        user_id = state.get("user_id", "default_user")
        iteration = state.get("iteration", 0)
        
        # 提取用户原始消息
        messages = state["messages"]
        user_message = ""
        for msg in reversed(messages):
            if hasattr(msg, 'type') and msg.type == 'human':
                user_message = msg.content
                break
        
        # 提取搜索结果
        search_result = "暂无搜索结果"
        for msg in reversed(messages):
            if hasattr(msg, 'type') and msg.type == 'tool':
                search_result = msg.content
                break
        
        current_date = datetime.now().strftime("%Y年%m月%d日")

         # ========== 新增：提取搜索词（用于调试和上下文） ==========
        search_query = state.get("search_query", "未知")
        # ========================================================

        full_system_prompt = f"""{self.system_prompt}

        【当前日期】{current_date}

        【搜索词】{search_query}

        【搜索结果】（来自联网搜索，可信度高）
        {search_result}

        【回答规则】
        1. 如果搜索结果中包含了用户提到的实体（如游戏名、人名），请基于搜索结果回答。
        2. 如果搜索结果中没有找到相关信息，请如实告知用户，不要编造。
        3. 如果搜索结果不完整，可以告知用户信息有限。

        用户最新消息：{user_message}
        """

        chat_messages = [{"role": "system", "content": full_system_prompt}]
        for msg in self.conversation_history[-10:]:
            chat_messages.append({"role": msg["role"], "content": msg["content"]})
        chat_messages.append({"role": "user", "content": user_message})

        final_reply = self._generate_with_main_model(chat_messages, image)

        self.conversation_history.append({"role": "user", "content": user_message})
        self.conversation_history.append({"role": "assistant", "content": final_reply})

        return {
            "messages": [{"role": "assistant", "content": final_reply}],
            "iteration": iteration + 1,
            "image": image,
            "query_type": state.get("query_type", "REALTIME") 
        }

    def _generate_with_main_model(self, messages, image: Optional[str] = None):
        """用主模型（9B，即 qwen3.5:9b）生成回复，支持图片输入"""
        import re
        # 深拷贝消息，避免修改原始列表
        msgs = messages.copy()
        
        if image:
            image_data = re.sub(r'^data:image/.+;base64,', '', image)
            # 图片数据直接添加到消息的 images 字段（适配器会识别）
            for msg in reversed(msgs):
                if msg.get('role') == 'user':
                    msg['images'] = [image_data]
                    break
        try:
            result = self.main_adapter.chat_with_tools(
                messages=msgs,
                tools=None
            )
            return result.get("content", "啊咧？香澄还没想好怎么回答呢…让我们再试一次吧！")
        except Exception as e:
            return f"❌ 主模型生成回复失败：{e}"


    def _should_continue(self, state: AgentState) -> Literal["tools", "end"]:
        """判断是否继续调用工具"""
        messages = state["messages"]
        iteration = state.get("iteration", 0)
        # 放宽限制，避免正常流程被中断
        if iteration > 10:
            return "end"
        if not messages:
            return "end"
        last_message = messages[-1]
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            return "tools"
        return "end"

    def _format_history(self) -> str:
        """格式化短期对话历史"""
        recent = self.conversation_history[-10:] if len(self.conversation_history) > 10 else self.conversation_history
        return "\n".join(f"{msg['role']}: {msg['content']}" for msg in recent)

    def chat(self, user_id: str, user_message: str, image: Optional[str] = None) -> str:
        """与 Agent 对话（同步接口，兼容原有代码）"""
        start_time = time.time()
        print(f"[时间] 开始处理消息，时间：{datetime.now().strftime('%H:%M:%S.%f')[:-3]}")

        # ========== 加载该用户的短期记忆（持久化的最近对话） ==========
        # 如果当前 conversation_history 为空（首次会话或重启后），从向量库加载
        if not self.conversation_history:
            # 从向量库中获取该用户最近 N 条短期记忆（按时间倒序）
            short_term = self.memory.get_recent_conversations(user_id, n=10)
            if short_term:
                # 解析存储的对话内容，转换为列表格式
                for conv in short_term:
                    # conv 是字符串，格式为 "用户说：...\n助手回复：..."
                    lines = conv.split('\n')
                    if len(lines) >= 2:
                        user_part = lines[0].replace("用户说：", "").strip()
                        assistant_part = lines[1].replace("助手回复：", "").strip()
                        self.conversation_history.append({"role": "user", "content": user_part})
                        self.conversation_history.append({"role": "assistant", "content": assistant_part})
                print(f"[短期记忆] 加载了 {len(self.conversation_history)//2} 轮历史对话")
            else:
                print("[短期记忆] 无历史对话")
        # ================================================================

        initial_state: AgentState = {
            "messages": [{"role": "user", "content": user_message}],
            "user_id": user_id,
            "iteration": 0,
            "image": image,
            "query_type": "UNKNOWN"
        }

        try:
            final_state = self.graph.invoke(initial_state)

            # 获取 query_type（从 state 中读取）
            query_type = final_state.get("query_type", "UNKNOWN")

            messages = final_state.get("messages", [])
            if messages:
                last = messages[-1]
                if hasattr(last, 'content'):
                    reply = last.content
                else:
                    reply = "啊咧？香澄还没想好怎么回答呢…让我们再试一次吧！"
            else:
                reply = "啊咧？香澄还没想好怎么回答呢…让我们再试一次吧！"

            # 异步存储记忆
            import threading
            threading.Thread(
                target=self._save_memory,
                args=(user_id, user_message, reply),
                daemon=True
            ).start()

            print(f"[时间] 前台流程结束，总耗时：{(time.time() - start_time)*1000:.2f}ms")
            return reply

        except Exception as e:
            print(f"[Agent] 运行失败：{e}")
            return f"❌ Agent 运行失败：{e}"

    def _save_memory(self, user_id: str, user_message: str, reply: str, query_type: str = None):
        """
        后台存储记忆（优化版）
        1. 存储短期记忆：原始对话，带轮次和类型标记
        2. 异步生成长期记忆摘要（如果开启）
        """
        try:
            # ========== 存储短期记忆（原始对话） ==========
            # 每条对话作为独立条目，带 type="short_term" 和轮次
            # 使用当前时间戳作为排序依据
            short_term_doc = f"用户说：{user_message}\n助手回复：{reply}"
            short_term_meta = {
                "user_id": user_id,
                "type": "short_term",
                "timestamp": datetime.now().isoformat(),
                "query_type": query_type or "UNKNOWN",
                # 可以添加情绪标签（后续可扩展）
            }
            # 存储到向量库（使用 add_with_title 支持元数据）
            add_result = self.memory.add_with_title(
                title=f"{user_message[:15]}...",
                content=short_term_doc,
                user_id=user_id,
                meta=short_term_meta
            )
            print(f"[存储] 短期记忆存储完成：{add_result}")

            # ========== 异步生成长期记忆摘要 ==========
            # 为了避免阻塞，这里启动后台线程生成摘要并存储
            import threading
            threading.Thread(
                target=self._generate_long_term_memory,
                args=(user_id, user_message, reply, query_type),
                daemon=True
            ).start()

        except Exception as e:
            print(f"[存储] 存储失败：{e}")

    def _generate_long_term_memory(self, user_id: str, user_message: str, reply: str, query_type: str = None):
        """
        后台生成长期记忆摘要（提炼核心信息）
        使用工具模型（tool_adapter）生成摘要和情绪标签，然后存储到向量库，type="long_term"
        """
        try:
            # 调用工具模型生成摘要和情绪标签（也可以使用主模型，但为避免干扰，使用工具模型）
            summary_prompt = f"""
            请根据以下对话，提炼出核心信息摘要（一句话）并判断情绪标签。

            对话：
            用户：{user_message}
            助手：{reply}

            输出格式（JSON）：
            {{"summary": "摘要内容", "emotion": "高兴/生气/难过/惊讶/平静/其他"}}
            """
            result = self.tool_adapter.chat_with_tools(
                messages=[{"role": "system", "content": summary_prompt}],
                tools=None
            )
            content = result.get("content", "")
            # 尝试解析 JSON
            import json, re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                summary = data.get("summary", "")
                emotion = data.get("emotion", "平静")
            else:
                # 降级：直接截取前30字
                summary = content[:50] + ("..." if len(content) > 50 else "")
                emotion = "平静"

            # 存储长期记忆
            long_term_doc = f"【摘要】{summary}（情绪：{emotion}）"
            long_term_meta = {
                "user_id": user_id,
                "type": "long_term",
                "timestamp": datetime.now().isoformat(),
                "emotion": emotion,
                "query_type": query_type or "UNKNOWN",
                "summary": summary,
            }
            add_result = self.memory.add_with_title(
                title=summary[:15] + "...",
                content=long_term_doc,
                user_id=user_id,
                meta=long_term_meta
            )
            print(f"[存储] 长期记忆存储完成：{add_result}")
        except Exception as e:
            print(f"[存储] 长期记忆生成失败：{e}")