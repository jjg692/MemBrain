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
from core.config import MEMORY_CONTEXT_MAX_ROUNDS, MEMORY_DEBUG
import ast


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

    def __init__(self, memory, llm_model: str = "qwen3.5:9b", tool_llm_model: str = "qwen2.5:7b", 
                 system_prompt=None, tool_adapter=None, main_adapter=None, l3_manager=None):
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
        self.l3_manager = l3_manager

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

        self._fact_extractor_loaded = True  # 占位，实际用 memory_manager 处理

        # ========== 当前角色 ID ==========
        self.role_id = "kasumi"

        # ========== 短期对话历史（当前会话） ==========
        self.conversation_history = {}    

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
        
        user_id = state.get("user_id", "default_user")

        # ========== 注入 L5 角色图谱信息 ==========
        role_context = ""
        try:
            role_facts = self.memory.get_role_facts("kasumi")
            if role_facts:
                role_context = "【当前角色信息】\n" + "\n".join(f"- {f}" for f in role_facts) + "\n"
        except Exception:
            pass
        # =========================================
        
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
        recent_history = self.conversation_history.get(user_id, [])[-5:]
        
        # 4. 构建上下文
        context_parts = []
        if role_context:
            context_parts.append(role_context)
        if short_term_texts:
            context_parts.append("【存储的最近对话】\n" + "\n".join(short_term_texts))
        if long_term_texts:
            context_parts.append("【相关历史摘要】\n" + "\n".join(long_term_texts))
        if recent_history:
            context_parts.append("【最近对话】\n" + "\n".join([f"{msg['role']}: {msg['content']}" for msg in recent_history]))
        
        context = "\n\n".join(context_parts)
        
        # ========== 调用模型进行查询改写 ==========
        # 复用原有的 tool_adapter，但改变 prompt 要求
        rewrite_prompt = f"""
        你是一个查询改写器。你的任务是：**只处理两种极端情况，其他情况一律原样输出。**

        【唯一允许改写的两种情况】
        1. **指代消解**：问题中包含“那个”、“这款”、“刚才的”、“它”、“她”、“他”等指代词，且上下文中有明确的实体可以替换。
        2. **省略补全**：问题中缺少主语或对象，导致无法独立理解（如“那明天呢”缺少时间指向）。

        【绝对禁止】
        - 不要改已有明确实体（地名、人名、物品名）——例如“长沙”就是“长沙”，不是任何别的东西。
        - 不要添加上下文隐含信息——如果用户没说“长沙”，就别加“长沙”；如果用户说了“长沙”，就别改成别的。
        - 不要解释或扩写——问题越短越不改。
        - 不要改变问题性质——“是什么”永远是“是什么”。

        【示例】
        用户：长沙今天天气怎么样
        → 输出：{{"status": "no_rewrite", "rewritten": "长沙今天天气怎么样"}}

        用户：那个好吃吗？
        上下文：用户说“草莓蛋糕很好吃”
        → 输出：{{"status": "success", "rewritten": "草莓蛋糕好吃吗", "entity": "草莓蛋糕"}}

        用户：那明天呢？
        上下文：用户说“明天去长沙”
        → 输出：{{"status": "success", "rewritten": "明天去长沙吗", "entity": "长沙"}}

        用户：小香澄喜欢吃什么？
        → 输出：{{"status": "no_rewrite", "rewritten": "小香澄喜欢吃什么"}}

        【上下文】
        {context}

        【用户原问题】
        {user_message}

        只输出 JSON，不要其他内容。
        """
        try:
            result = self.tool_adapter.chat_with_tools(
                messages=[
                    {"role": "system", "content": rewrite_prompt},
                    {"role": "user", "content": f"请改写这个查询：{user_message}"}  # ← 新增
                ],
                tools=None
            )
            import json, re
            content = result.get("content", "")
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:

                # ... 获取 content ...
                data = None

                # 1. 尝试直接解析 JSON
                try:
                    data = json.loads(content.strip())
                except json.JSONDecodeError:
                    pass
                # 2. 尝试用 ast.literal_eval 解析 Python 字典（支持单引号）
                if data is None:
                    try:
                        parsed = ast.literal_eval(content.strip())
                        if isinstance(parsed, dict):
                            data = parsed
                    except:
                        pass
                # 3. 尝试提取花括号内的内容并解析
                if data is None:
                    json_match = re.search(r'\{.*\}', content, re.DOTALL)
                    if json_match:
                        try:
                            data = json.loads(json_match.group())
                        except:
                            # 再试一次 ast.literal_eval
                            try:
                                parsed = ast.literal_eval(json_match.group())
                                if isinstance(parsed, dict):
                                    data = parsed
                            except:
                                pass

                if data is None:
                    data = {}
                status = data.get("status", "failed")
                rewritten = data.get("rewritten", user_message)
                candidates = data.get("candidates", [])
                
                if status == "success":
                    print(f"[查询改写] 成功: {user_message} → {rewritten}")
                    return {"status": "success", "query": rewritten, "entity": data.get("entity", "")}
                elif status == "multiple" and candidates:
                    print(f"[查询改写] 多候选: {candidates}")
                    return {"status": "multiple", "candidates": candidates, "query": user_message}
                else:
                    return {"status": "failed", "query": user_message}
            return {"status": "failed", "query": user_message}
        except Exception as e:
            print(f"[查询改写] 失败: {e}")
            print(f"[查询改写] 原始内容: {content}")  # 打印前200字符
            return {"status": "failed", "query": user_message}

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
        status = rewrite_result.get("status", "failed")
        
        if status == "success":
            rewritten = rewrite_result.get("query", user_message)
            if rewritten != user_message:
                log_debug("Agent", f"查询改写成功: {user_message} → {rewritten}")
                state["messages"][-1] = {"role": "user", "content": rewritten}
                user_message = rewritten
        elif status == "multiple":
            candidates = rewrite_result.get("candidates", [])
            log_debug("Agent", f"查询改写多候选: {candidates}")
            # 生成询问回复，让用户选择
            ask_prompt = f"""
            用户使用了指代词，可能指向以下多个实体：
            {', '.join(candidates)}

            请生成一段友好的、符合角色人设的回复，向用户确认具体是指哪一个。
            列出所有候选实体，让用户从中选择。
            """
            ask_result = self.tool_adapter.chat_with_tools(
                 messages=[
                    {"role": "system", "content": ask_prompt},
                    {"role": "user", "content": f"用户说的'那个'可能指代以下实体：{', '.join(candidates)}，请生成询问回复。"}
                ],
                tools=None
            )
            reply = ask_result.get("content", f"呐呐～你说的'那个'是指 {', '.join(candidates)} 中的哪一个呢？")
            user_id = state.get("user_id", "default_user")
            if user_id not in self.conversation_history:
                self.conversation_history[user_id] = []
            self.conversation_history[user_id].append({"role": "user", "content": user_message})
            self.conversation_history[user_id].append({"role": "assistant", "content": reply})
            # 直接返回询问，不继续路由
            return {
                "messages": [{"role": "assistant", "content": reply}],
                "iteration": state.get("iteration", 0) + 1,
                "image": state.get("image", None),
                "query_type": "PERSONAL"
            }
        # status == "failed" 时，保持原问题不变，继续走路由
        # =====================================================

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
        # 给 router 带上角色别名信息
        role_alias_hint = ""
        try:
            role_facts = self.memory.get_role_facts("kasumi")
            identity_fact = next((f for f in role_facts if "别号" in f or "别称" in f), role_facts[0] if role_facts else "")
            if identity_fact:
                role_alias_hint = f"当前角色名称：户山香澄。{identity_fact}"
        except Exception as e:
            role_alias_hint = ""

        query_type = classify_query(self, user_message, rewrite_context=rewrite_result, role_context=role_alias_hint)
        log_router(f"问题类型: {query_type}")

        # ========== 分流处理 ==========
        if query_type == "REALTIME":
            log_router("实时信息，直接走搜索")
            result = force_search(self, user_message, state)
            result["query_type"] = "REALTIME"
            return result

        elif query_type == "PERSONAL":
            log_router("个人闲聊，只走记忆")
            result = handle_personal(self, user_message, state, role_id="kasumi")
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

    def _generate_with_main_model(self, messages, image: Optional[str] = None, enable_search_fallback: bool = False):
        """
        主模型生成回复，支持在不确定时触发搜索
        """
        import re
        _start = time.time()
        msgs = messages.copy()

        # 如果启用搜索兜底，在 system prompt 中加入搜索权限
        if enable_search_fallback:
            for msg in msgs:
                if msg.get("role") == "system":
                    msg["content"] += """

    【搜索工具权限】
    - 如果你认为自己无法回答用户的问题，或者问题需要实时信息（天气、新闻、推荐、评分等），你可以触发搜索。
    - 当你决定需要搜索时，在回复的开头输出：[SEARCH] 搜索关键词
    - 如果你能回答，请正常回复，不要输出 [SEARCH] 标记。
    """
                    break

        # 处理图片输入
        if image:
            image_data = re.sub(r'^data:image/.+;base64,', '', image)
            for msg in reversed(msgs):
                if msg.get('role') == 'user':
                    msg['images'] = [image_data]
                    break

        try:
            result = self.main_adapter.chat(messages=msgs)
            content = result.get("content", "")
            
            # 检测是否触发了搜索
            if enable_search_fallback and "[SEARCH]" in content:
                match = re.search(r'\[SEARCH\]\s*(.+)', content)
                if match:
                    search_query = match.group(1).strip()
                    log_router(f"主模型决定搜索: {search_query}")
                    # 触发搜索
                    from agent.handlers.realtime import force_search
                    # 构造 state 用于 force_search
                    state = {"user_id": "default_user", "iteration": 0}
                    search_result = force_search(self, search_query, state)
                    # 从搜索结果中提取回复
                    # force_search 返回的是 {"messages": [...], ...}
                    # 需要提取出搜索结果并重新生成回复
                    return self._handle_search_result(search_result, msgs)
            
            log_time("主模型生成回复", _start)
            return content
        except Exception as e:
            log_debug("主模型", f"生成回复失败: {e}")
            return f"❌ 主模型生成回复失败：{e}"

    # ==================== 核心对外接口 ====================

    def chat(self, user_id: str, user_message: str, image: Optional[str] = None) -> str:
        """主入口：处理用户消息"""
        _start = time.time()
        
        # === L1: 初始化/加载用户内存上下文 ===
        if user_id not in self.conversation_history:
            self.conversation_history[user_id] = []
            # 从短期记忆加载最近对话
            short_term = self.memory.get_recent_conversations(user_id, n=10)
            for conv in short_term:
                parts = conv.split('\n')
                if len(parts) >= 2:
                    user_part = parts[0].replace("用户说：", "").strip()
                    assistant_part = parts[1].replace("助手回复：", "").strip()
                    if user_part:
                        self.conversation_history[user_id].append({"role": "user", "content": user_part})
                    if assistant_part:
                        self.conversation_history[user_id].append({"role": "assistant", "content": assistant_part})
            if MEMORY_DEBUG:
                    print(f"[Graph] 加载历史: {len(self.conversation_history[user_id])} 条消息")







            # === 触发模糊化（每次对话启动时检查旧记忆） ===
            try:
                fuzzy_count = self.memory_manager.trigger_fuzzify(user_id, role_id=self.role_id)
                if fuzzy_count > 0:
                    log_debug("模糊化", f"本次启动模糊化了 {fuzzy_count} 条记忆")
            except Exception:
                pass

        # 追加当前用户消息
        self.conversation_history[user_id].append({"role": "user", "content": user_message})

        # === 初始化 LangGraph 状态 ===
        initial_state: AgentState = {
            "messages": [{"role": "user", "content": user_message}],
            "user_id": user_id,
            "iteration": 0,
            "image": image,
            "query_type": "UNKNOWN",
            "rewritten_query": None,
            "memory_context": None,
            "short_term_ids": None,
            "importance_score": None,
            "search_results": None,
            "facts": None,
        }

        try:
            final_state = self.graph.invoke(initial_state)
            query_type = final_state.get("query_type", "UNKNOWN")

            messages = final_state.get("messages", [])
            if messages:
                last = messages[-1]
                if hasattr(last, 'content'):
                    reply = last.content
                elif isinstance(last, dict):
                    reply = last.get("content", "")
                else:
                    reply = "啊咧？香澄还没想好怎么回答呢…"
            else:
                reply = "啊咧？香澄还没想好怎么回答呢…"

            # 追加助手回复到内存上下文
            self.conversation_history[user_id].append({"role": "assistant", "content": reply})

            # === L1: 检查是否超过20轮，触发压缩 ===
            if len(self.conversation_history[user_id]) > MEMORY_CONTEXT_MAX_ROUNDS * 2:
                self._compress_context(user_id)

            # === 异步存储记忆（L2 + L4） ===
            importance = self.memory_manager.judge_importance(user_message, reply)
            threading.Thread(
                target=self.memory_manager.save_memory,
                args=(user_id, "kasumi", user_message, reply, query_type, importance),
                daemon=True
            ).start()

            if MEMORY_DEBUG:
                print(f"[Graph] 总耗时: {(time.time()-_start)*1000:.2f}ms")
            return reply

        except Exception as e:
            import traceback
            traceback.print_exc()
            return f"❌ Agent 运行失败：{e}"
        
    def _compress_context(self, user_id: str):
        """压缩 L1 内存上下文：保留最近10轮，前10轮压缩为摘要"""
        history = self.conversation_history.get(user_id, [])
        if len(history) <= 10:
            return
        
        # 取出前10轮（按 user/assistant 成对取）
        old_rounds = history[:10]
        recent_rounds = history[10:]
        
        # 生成摘要
        summary = self.memory_manager.compress_context(old_rounds)
        
        # 替换为摘要 + 最近10轮
        self.conversation_history[user_id] = [
            {"role": "system", "content": f"【对话摘要】{summary}"}
        ] + recent_rounds
        
        if MEMORY_DEBUG:
            print(f"[Graph] L1 压缩完成: 原 {len(history)} 条 -> {len(self.conversation_history[user_id])} 条")