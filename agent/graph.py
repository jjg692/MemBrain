# agent/graph.py
"""
LangGraph Memory Agent - 核心类
负责构建 LangGraph 图、管理对话循环、协调各处理器
"""
import time
import threading
from typing import Optional, List, Literal
from datetime import datetime
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

from core.adapters import OllamaAdapter
from core.tools import search_web
from core.state import AgentState
from core.memory import MemoryManager
from agent.router import classify_query
from agent.handlers import handle_personal, force_search, handle_result_node
from core.logger import log_time, log_debug, log_router


class LangGraphMemoryAgent:
    """
    基于 LangGraph 的 ReAct Agent，带记忆系统

    双模型分工：
    - tool_llm_model (7B, 默认 qwen2.5:7b)：负责判断是否需要调用工具（轻量决策）
    - llm_model (9B, 默认 qwen3.5:9b)：负责最终回复生成（高质量输出）

    路由分流：
    - REALTIME：强制走搜索（需要联网查询的信息，天气、新闻、推荐等）
    - PERSONAL：走记忆（个人偏好、闲聊），记忆为空时主模型自主决策

    记忆系统：
    - 短期记忆：持久化到向量库，重启不丢失，FIFO 淘汰
    - 长期记忆：LLM 生成的摘要 + 情绪标签
    """

    def __init__(self, memory, llm_model: str = "qwen3.5:9b", tool_llm_model: str = "qwen2.5:7b", system_prompt=None, tool_adapter=None, main_adapter=None):
        """
        初始化 Agent

        Args:
            memory: SimpleMemory 实例
            llm_model: 主模型名称（负责最终回复生成）
            tool_llm_model: 工具模型名称（负责路由判断）
            system_prompt: 角色人设提示词
            tool_adapter: 工具模型适配器（默认 OllamaAdapter）
            main_adapter: 主模型适配器（默认 OllamaAdapter）
        """
        self.memory = memory
        self.llm_model = llm_model
        self.tool_llm_model = tool_llm_model
        self.system_prompt = system_prompt

        # ========== 初始化适配器 ==========
        if tool_adapter is None:
            self.tool_adapter = OllamaAdapter(model=tool_llm_model)
        else:
            self.tool_adapter = tool_adapter

        if main_adapter is None:
            self.main_adapter = OllamaAdapter(model=llm_model)
        else:
            self.main_adapter = main_adapter

        # ========== 初始化记忆管理器 ==========
        self.memory_manager = MemoryManager(memory, self.tool_adapter)

        # ========== 短期对话历史（当前会话） ==========
        self.conversation_history = []  # 格式: [{"role": "user", "content": "..."}, ...]

        # ========== 构建 LangGraph 图 ==========
        self.graph = self._build_graph()

    def _build_graph(self):
        """
        构建 LangGraph 图

        流程：
        agent（路由分类）→ REALTIME/PERSONAL → 处理
                    ↓
        tools（执行搜索）→ handle_result（生成回复）→ END
        """
        workflow = StateGraph(AgentState)

        # 添加节点
        workflow.add_node("agent", self._agent_node)
        workflow.add_node("tools", ToolNode([search_web]))
        workflow.add_node("handle_result", self._handle_result_node)

        # 设置入口
        workflow.set_entry_point("agent")

        # 条件边：agent 决定是否调用工具
        workflow.add_conditional_edges(
            "agent",
            self._should_continue,
            {"tools": "tools", "end": END}
        )

        # 工具执行完后，进入 handle_result 生成最终回复
        workflow.add_edge("tools", "handle_result")
        workflow.add_edge("handle_result", END)

        return workflow.compile()
    

    def _rewrite_query(self, user_message: str, state: AgentState) -> dict:
        """
        查询改写：利用上下文将模糊问题补全为完整问题。
        复用原有的指代消解上下文构建逻辑。
        返回: {"need_rewrite": True/False, "query": "改写后的完整问题"}
        """
        # 快速检查：如果消息中明显没有指代和省略，直接返回
        # 但为了更准确，依然交给模型判断
        import re
        has_abbrev = any(kw in user_message for kw in ["那个", "这款", "这个", "刚才", "上次", "之前", "它", "她", "他"])
        if not has_abbrev:
            # 即使没有指代词，也可能存在省略（如"那明天呢"），所以不直接返回，而是让模型判断
            pass
        
        user_id = state.get("user_id", "default_user")
        
        # ========== 复用原有的上下文构建逻辑 ==========
        # 1. 从短期记忆（向量库）获取最近对话
        short_term_results = self.memory.search(
            query=user_message,
            user_id=user_id,
            threshold=0.4,
            n_results=5,
            where={"type": "short_term"}
        )
        short_term_texts = [item["document"] for item in short_term_results.get("results", [])]
        
        # 2. 从长期记忆获取相关摘要
        long_term_results = self.memory.search(
            query=user_message,
            user_id=user_id,
            threshold=0.5,
            n_results=3,
            where={"type": "long_term"}
        )
        long_term_texts = [item["document"] for item in long_term_results.get("results", [])]
        
        # 3. 从对话历史（内存）获取最近上下文
        recent_history = self.conversation_history[-5:] if self.conversation_history else []
        
        # 4. 构建上下文
        context_parts = []
        if short_term_texts:
            context_parts.append("【存储的最近对话】\n" + "\n".join(short_term_texts))
        if long_term_texts:
            context_parts.append("【相关历史摘要】\n" + "\n".join(long_term_texts))
        if recent_history:
            context_parts.append("【最近对话】\n" + "\n".join([f"{msg['role']}: {msg['content']}" for msg in recent_history]))
        
        # 如果没有任何上下文，无法改写
        if not context_parts:
            return {"need_rewrite": False, "query": user_message, "reason": "无上下文"}
        
        context = "\n\n".join(context_parts)
        # ====================================================
        
        # ========== 调用模型进行查询改写 ==========
        # 复用原有的 tool_adapter，但改变 prompt 要求
        rewrite_prompt = f"""
        你是一个查询改写器。请根据对话上下文，将用户问题改写成**完整的、可独立理解的问题**。

        【上下文】
        {context}

        【用户原问题】
        {user_message}

        【任务】
        1. 如果原问题中有指代词（"那个"、"这款"、"刚才的"等），将其替换为具体的实体名称。
        2. 如果原问题中有省略（如"那明天呢"），补充缺失的信息（如"长沙明天天气怎么样"）。
        3. 如果原问题已经很完整，直接输出原问题。
        4. 输出格式为 JSON：{{"rewritten": "改写后的问题", "changed": true/false, "reason": "改动原因"}}

        【输出】
        只输出 JSON。
        """
        try:
            result = self.tool_adapter.chat_with_tools(
                messages=[{"role": "system", "content": rewrite_prompt}],
                tools=None
            )
            import json, re
            content = result.get("content", "")
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                rewritten = data.get("rewritten", user_message)
                changed = data.get("changed", False)
                if changed and rewritten != user_message:
                    print(f"[查询改写] {user_message} → {rewritten}")
                    return {"need_rewrite": True, "query": rewritten}
            return {"need_rewrite": False, "query": user_message}
        except Exception as e:
            print(f"[查询改写] 失败: {e}")
            return {"need_rewrite": False, "query": user_message}

    def _agent_node(self, state: AgentState, config):
        """
        Agent 节点：路由分类 + 分流执行

        这是 LangGraph 图的入口节点，负责：
        1. 检测“联网搜索”关键词（强制走搜索）
        2. 调用路由器分类（PERSONAL / REALTIME）
        3. 根据类型调用对应的处理器
        """
        _start = time.time()
        messages = state["messages"]
        last_msg = messages[-1] if messages else None
        user_message = last_msg.content if last_msg and hasattr(last_msg, 'content') else ""

        # ========== 查询改写（所有消息都过） ==========
        rewrite_result = self._rewrite_query(user_message, state)
        if rewrite_result.get("need_rewrite", False):
            rewritten = rewrite_result.get("query", user_message)
            if rewritten != user_message:
                log_debug("Agent", f"查询改写: {user_message} → {rewritten}")
                state["messages"][-1] = {"role": "user", "content": rewritten}
                user_message = rewritten
        # ================================================

        log_debug("Agent", f"收到用户消息: {user_message[:50]}...")
        log_time("Agent节点开始", _start)

        # ========== 强制搜索关键词检测 ==========
        # 用户明确要求联网搜索时，绕过路由分类
        if "联网搜索" in user_message:
            log_router("检测到'联网搜索'关键词，强制走搜索")
            result = force_search(self, user_message, state)
            result["query_type"] = "REALTIME"
            return result

        # ========== 路由分类 ==========
        query_type = classify_query(self, user_message)
        log_router(f"问题类型: {query_type}")

        # ========== 分流处理 ==========
        if query_type == "REALTIME":
            log_router("实时信息，直接走搜索")
            result = force_search(self, user_message, state)
            result["query_type"] = "REALTIME"
            return result

        elif query_type == "PERSONAL":
            log_router("个人闲聊，只走记忆")
            result = handle_personal(self, user_message, state)
            result["query_type"] = "PERSONAL"
            return result

        else:
            log_router("无类型，直接走搜索")
            result = force_search(self, user_message, state)
            result["query_type"] = "REALTIME"
            return result

    def _handle_result_node(self, state: AgentState):
        """处理搜索结果，生成最终回复（不经过路由）"""
        return handle_result_node(self, state)

    def _should_continue(self, state: AgentState) -> Literal["tools", "end"]:
        """
        判断是否继续调用工具

        Returns:
            "tools": 需要调用工具，进入 tools 节点
            "end": 结束流程
        """
        messages = state["messages"]
        iteration = state.get("iteration", 0)

        # 防止无限循环
        if iteration > 10:
            return "end"

        if not messages:
            return "end"

        last_message = messages[-1]

        # 如果最后一条消息包含 tool_calls，则进入 tools 节点
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            return "tools"

        return "end"

    def _generate_with_main_model(self, messages, image: Optional[str] = None):
        """
        用主模型生成回复，支持图片输入

        Args:
            messages: 消息列表
            image: 图片 Base64 数据（可选）

        Returns:
            主模型生成的回复文本
        """
        import re
        _start = time.time()
        msgs = messages.copy()

        # 处理图片输入（多模态）
        if image:
            image_data = re.sub(r'^data:image/.+;base64,', '', image)
            for msg in reversed(msgs):
                if msg.get('role') == 'user':
                    msg['images'] = [image_data]
                    break

        try:
            result = self.main_adapter.chat_with_tools(messages=msgs, tools=None)
            log_time("主模型生成回复", _start)
            return result.get("content", "啊咧？香澄还没想好怎么回答呢…让我们再试一次吧！")
        except Exception as e:
            log_debug("主模型", f"生成回复失败: {e}")
            return f"❌ 主模型生成回复失败：{e}"

    # ==================== 核心对外接口 ====================

    def chat(self, user_id: str, user_message: str, image: Optional[str] = None) -> str:
        """
        与 Agent 对话（同步接口，兼容原有代码）

        Args:
            user_id: 用户ID
            user_message: 用户消息
            image: 图片 Base64 数据（可选）

        Returns:
            Agent 回复文本
        """
        start_time = time.time()
        log_time("开始处理消息", start_time)

        # ========== 加载该用户的短期记忆（持久化的最近对话） ==========
        # 如果当前 conversation_history 为空（首次会话或重启后），从向量库加载
        if not self.conversation_history:
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
                log_debug("短期记忆", f"加载了 {len(self.conversation_history)//2} 轮历史对话")
            else:
                log_debug("短期记忆", "无历史对话")

        # ========== 初始化 LangGraph 状态 ==========
        initial_state: AgentState = {
            "messages": [{"role": "user", "content": user_message}],
            "user_id": user_id,
            "iteration": 0,
            "image": image,
            "query_type": "UNKNOWN"
        }

        try:
            # ========== 执行 LangGraph 图 ==========
            final_state = self.graph.invoke(initial_state)

            # 获取 query_type（从 state 中读取）
            query_type = final_state.get("query_type", "UNKNOWN")

            # 提取最终回复
            messages = final_state.get("messages", [])
            if messages:
                last = messages[-1]
                if hasattr(last, 'content'):
                    reply = last.content
                else:
                    reply = "啊咧？香澄还没想好怎么回答呢…让我们再试一次吧！"
            else:
                reply = "啊咧？香澄还没想好怎么回答呢…让我们再试一次吧！"

            # ========== 异步存储记忆 ==========
            threading.Thread(
                target=self.memory_manager.save_memory,
                args=(user_id, user_message, reply, query_type),
                daemon=True
            ).start()

            log_time("前台流程结束", start_time)
            return reply

        except Exception as e:
            log_debug("Agent", f"运行失败: {e}")
            return f"❌ Agent 运行失败：{e}"