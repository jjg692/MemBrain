"""
LangGraph Memory Agent - 核心类（重构版）

设计原则（严格遵循）：
- 自治路由：不设路由层、不设查询改写层。所有用户消息直接进入 Agent 主流程，
  LLM 自主决定是否调用工具、调用哪个工具。意图/指代消解全部由 LLM 完成。
- 模式 B：情感/好感度分两阶段
    · 第一阶段：LLM 只输出 JSON（情感 + 好感度更新）
    · 第二阶段：基于分析结果 + 记忆 + 角色人设生成回复
- 双键隔离：(user_id, role_id)；L5 仅按 role_id
- LLM 优先：路由、情感、事实抽取全部由 LLM 判断

LangGraph 执行流程：
    user -> agent(LLM 调用，可产生 tool_calls)
              |-> 有 tool_calls -> tools(执行) -> agent(再次调用)
              └-> 无 tool_calls   -> END
"""
import json
import threading
from datetime import datetime
from typing import Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from core.state import AgentState
from core.adapters import LLMAdapter, OllamaAdapter
from core.logger import log_error
from core.tools import ALL_TOOLS, TOOL_REGISTRY

from core.memory.memory_manager import MemoryManager
from core.emotion import (
    EmotionState, AffectionState, EmotionAnalyzer, EmotionStore,
    emotion_to_prompt_text, relation_to_prompt_text,
)
from core.role.manager import RoleManager
from core.room.message_bus import MessageBus


class LangGraphMemoryAgent:
    """基于 LangGraph 的自治 ReAct Agent，带五层记忆 + 模式B情感"""

    def __init__(
        self,
        memory_manager: MemoryManager,
        role_manager: RoleManager,
        emotion_store: EmotionStore,
        role_id: str,
        llm_adapter: Optional[LLMAdapter] = None,
        tool_adapter: Optional[LLMAdapter] = None,
        system_prompt: Optional[str] = None,
        message_bus: Optional[MessageBus] = None,
        perception: Optional[object] = None,
        tool_fallback: bool = True,
    ):
        self.memory = memory_manager
        self.role_manager = role_manager
        self.emotion_store = emotion_store
        self.role_id = role_id
        self.message_bus = message_bus
        self.perception = perception
        # 兜底守卫开关：默认开启（真实 LLM 自治路由时，防止"说查不查"）。
        # 单元测试验证假 LLM 的确定性链路时传 False，避免守卫干预。
        self.tool_fallback = tool_fallback

        self.system_prompt = system_prompt or role_manager.load_prompt(role_id)
        # 默认用 LLMManager 按当前 provider 构建（本地 Ollama / 远程兼容均可），
        # 传入的 llm_adapter 优先（AgentFactory 会传入 initializer 的适配器以支持热切换）
        if llm_adapter is None or tool_adapter is None:
            try:
                from core.llm_manager import LLMManager
                _mgr = LLMManager()
                if llm_adapter is None:
                    llm_adapter = _mgr.build_llm_adapter()
                if tool_adapter is None:
                    tool_adapter = _mgr.build_tool_adapter()
            except Exception:
                from core.config import LLM_MODEL, TOOL_LLM_MODEL, OLLAMA_HOST
                from core.adapters import OllamaAdapter as _OA
                llm_adapter = llm_adapter or _OA(model=LLM_MODEL, host=OLLAMA_HOST)
                tool_adapter = tool_adapter or _OA(model=TOOL_LLM_MODEL, host=OLLAMA_HOST)
        self.llm_adapter = llm_adapter
        self.tool_adapter = tool_adapter

        self.analyzer = EmotionAnalyzer(self.tool_adapter)

        # 每个 user_id 的状态（L1 + 情感 + 好感度），按 user 隔离，role 固定
        self._user_states: Dict[str, dict] = {}

        self.graph = self._build_graph()

    # ===================== 每用户会话状态 =====================

    def _get_session(self, user_id: str) -> dict:
        """获取用户的会话状态（L1 列表、情感、好感度）"""
        if user_id not in self._user_states:
            # 冷启动：L2 加载到 L1
            self.memory.cold_start_load(user_id, self.role_id)
            emotion = self.emotion_store.load_emotion(user_id, self.role_id) or EmotionState.default()
            affection = self.emotion_store.load_affection(user_id, self.role_id) or AffectionState.default()
            self._user_states[user_id] = {
                "l1": self.memory.get_l1(user_id, self.role_id),
                "emotion": emotion,
                "affection": affection,
            }
        return self._user_states[user_id]

    # ===================== LangGraph 图 =====================

    def _build_graph(self):
        workflow = StateGraph(AgentState)

        workflow.add_node("agent", self._agent_node)
        workflow.add_node("tools", ToolNode(self._tool_registry_fns()))

        workflow.set_entry_point("agent")

        workflow.add_conditional_edges(
            "agent",
            self._route_after_agent,
            {"tools": "tools", "end": END},
        )
        workflow.add_edge("tools", "agent")

        return workflow.compile()

    def _tool_registry_fns(self):
        from langchain_core.tools import tool
        funcs = []
        for name, fn in TOOL_REGISTRY.items():
            t = tool(fn)
            t.name = name
            funcs.append(t)
        return funcs

    # ===================== 节点 =====================

    def _agent_node(self, state: AgentState):
        user_id = state.get("user_id", "default_user")
        role_id = self.role_id
        session = self._get_session(user_id)

        # 最新用户消息（第一条 HumanMessage 作为本轮语义来源；include 工具上下文）
        first_user_msg = ""
        for m in (state.get("messages") or []):
            if isinstance(m, HumanMessage):
                first_user_msg = m.content or ""
                break
        user_msg = first_user_msg

        # 感知：记录一次用户活跃（时序/作息模型）
        perception = getattr(self, "perception", None)
        if perception is not None:
            try:
                perception.record_user_activity(user_id)
            except Exception:
                pass

        # ========== 模式B 第一阶段：情感/好感度分析（仅在新用户消息轮执行） ==========
        # persist_emotion=False（群聊接力）时跳过情感分析/持久化：
        # 避免把角色之间的对话误当成"用户对角色"的情感信号，也省去群聊每角色每轮重复调用
        has_prior_ai = any(isinstance(m, AIMessage) for m in (state.get("messages") or []))
        should_persist = state.get("persist_emotion", True) and not has_prior_ai
        if should_persist:
            last_turns = [getattr(m, "content", "") for m in (state.get("messages") or [])[-6:]]
            analysis = self.analyzer.analyze(
                user_msg, session["emotion"], session["affection"], last_turns,
            )
            if analysis:
                session["emotion"] = self.analyzer.merge_emotion(session["emotion"], analysis.get("emotion", {}))
                session["affection"] = self.analyzer.merge_affection(session["affection"], analysis.get("affection", {}))
                self._persist_emotion(user_id, session)
                # 感知：记录情绪/好感度样本（情绪趋势曲线）
                if perception is not None:
                    try:
                        aff = session["affection"]
                        avg = (aff.liking + aff.trust + aff.familiarity + aff.respect + aff.interest + aff.attachment) / 6.0
                        perception.record_mood(
                            user_id,
                            session["emotion"].primary,
                            session["emotion"].valence,
                            avg,
                        )
                    except Exception:
                        pass

        # 记忆检索（L4 + L5）
        retrieval = self.memory.retrieve(user_id, role_id, user_msg, top_k=5)

        # 构建 system prompt
        base_prompt = self._build_system_prompt(
            session, retrieval, user_id, role_id, state.get("room_context")
        )

        # ========== 构建 LLM 消息 ==========
        # 第一部分：L1 历史（该 user 的完整会话，不含本次；L1 已在 add_to_l1 中按
        # MEMORY_CONTEXT_MAX_ROUNDS 压缩，故全量注入不会无限增长，且能支撑跨轮指代消解）
        chat_messages = [{"role": "system", "content": base_prompt}]
        history = session["l1"]
        for m in history:
            if m["role"] in ("user", "assistant"):
                chat_messages.append({"role": m["role"], "content": m["content"]})

        # 第二部分：本轮 LangGraph 状态中的消息（含工具往返），追加为 OpenAI/Ollama 格式
        for m in (state.get("messages") or []):
            if isinstance(m, HumanMessage):
                chat_messages.append({"role": "user", "content": m.content or ""})
            elif isinstance(m, AIMessage):
                if getattr(m, "tool_calls", None):
                    import os as _os
                    _provider = _os.environ.get("LLM_PROVIDER", "ollama").strip().lower()
                    chat_messages.append({
                        "role": "assistant",
                        "content": m.content or "",
                        "tool_calls": self._to_ollama_tool_calls(
                            m.tool_calls, openai_style=(_provider == "openai")
                        ),
                    })
                else:
                    chat_messages.append({"role": "assistant", "content": m.content or ""})
            elif isinstance(m, ToolMessage):
                chat_messages.append({"role": "tool", "content": m.content or "", "tool_call_id": m.tool_call_id})

        # 若本轮没有用户消息（异常兜底）加一个占位
        if not any(cm.get("role") == "user" for cm in chat_messages):
            chat_messages.append({"role": "user", "content": user_msg or "请继续。"})

        # ========== 调用主模型（带工具，LLM 自主决策 - 自治路由） ==========
        result = self.llm_adapter.chat_with_tools(chat_messages, tools=ALL_TOOLS)
        content = result.get("content", "")
        tool_calls_raw = result.get("tool_calls", [])

        if tool_calls_raw:
            msg = AIMessage(content=content, tool_calls=self._to_lc_tool_calls(tool_calls_raw))
            return {"messages": [msg], "iteration": state.get("iteration", 0) + 1}

        # ---- 兜底守卫：模型"说要查却不查"时的可靠性补位 ----
        # 自治路由把工具决定权交给 LLM，但本地模型偶发会只用口述承诺（如"让我帮你查一下天气"）
        # 却不发起 tool_call，导致"说查不查"。这里做一层无害兜底：
        #   当用户消息明显需要实时信息（天气/百科/联网查询），且本轮尚未真正执行过任何工具，
        #   且模型没走工具就准备直接回答时，强制注入一次 search_web，让 tools 节点真正执行。
        if (
            self.tool_fallback
            and self._needs_realtime(user_msg)
            and not self._tool_executed_this_turn(state)
            and state.get("iteration", 0) < 8
        ):
            forced = [{
                "name": "search_web",
                "args": {"query": user_msg},
                "id": f"forced_search_{state.get('iteration', 0)}",
            }]
            msg = AIMessage(
                content=content or "",
                tool_calls=forced,
            )
            return {"messages": [msg], "iteration": state.get("iteration", 0) + 1}

        # 无工具：最终回复
        final_msg = AIMessage(content=content)
        self._after_reply(user_id, session, user_msg, content)
        return {"messages": [final_msg], "iteration": state.get("iteration", 0) + 1}

    @staticmethod
    def _needs_realtime(user_msg: str) -> bool:
        """判断用户消息是否明确需要联网查实时/资料信息。

        复用 core.tools._detect_intent 的轻量关键词路由（天气/百科/概念等），
        避免再花一次 LLM 调用。**只对清晰的资料/天气意图触发兜底**；
        "general"（含闲聊）不在此列，避免把纯闲聊也强制成搜索。
        """
        try:
            from core.tools import _detect_intent
            intent = _detect_intent(user_msg or "")
            return intent in ("weather", "wiki")
        except Exception:
            return False

    @staticmethod
    def _tool_executed_this_turn(state: AgentState) -> bool:
        """本轮状态中是否已出现过 ToolMessage（即工具已真正执行过）。

        用于兜底守卫，防止模型反复不调用工具时无限循环注入。
        """
        for m in (state.get("messages") or []):
            try:
                from langchain_core.messages import ToolMessage
                if isinstance(m, ToolMessage):
                    return True
            except Exception:
                return False
        return False

    def _route_after_agent(self, state: AgentState) -> str:
        if state.get("iteration", 0) > 8:
            return "end"
        last = state["messages"][-1] if state.get("messages") else None
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return "end"

    @staticmethod
    def _to_ollama_tool_calls(lc_tool_calls, openai_style: bool = False) -> List[dict]:
        """把 LangChain AIMessage.tool_calls 转成回显格式（含 id）。

        - openai_style=False：Ollama 格式，arguments 为 dict
        - openai_style=True ：OpenAI 兼容接口(远程)，arguments 需为 JSON 字符串
        """
        out = []
        for tc in lc_tool_calls:
            args = tc.get("args", {})
            if openai_style and not isinstance(args, str):
                try:
                    args = json.dumps(args, ensure_ascii=False)
                except Exception:
                    args = "{}"
            entry = {
                "function": {
                    "name": tc.get("name", ""),
                    "arguments": args,
                },
                "id": tc.get("id", ""),
            }
            if openai_style:
                entry["type"] = "function"
            out.append(entry)
        return out


    # ===================== 辅助 =====================

    def _build_system_prompt(self, session, retrieval, user_id, role_id, room_context=None) -> str:
        lines = [self.system_prompt or f"你是角色 {role_id}。"]
        lines.append(f"【当前日期】{datetime.now().strftime('%Y年%m月%d日')}")

        # 用户昵称：若设置了则注入，要求角色用该昵称称呼用户
        try:
            from core.user_profile import UserProfile
            nickname = UserProfile().get_nickname(user_id or "default_user")
        except Exception:
            nickname = ""
        if nickname:
            lines.append(
                f"【当前用户】{user_id}（称呼：{nickname}。对话中\"你\"指用户\"{nickname}\"，\"我\"指你本人。"
                "尽量自然地用这个昵称称呼用户，像朋友一样，但不要每句都喊。）"
            )
        else:
            lines.append(f"【当前用户】{user_id}（对话中\"你\"指当前用户，\"我\"指你本人）")

        # 角色扮演守则：全程保持角色，禁止跳出人设
        lines.append(
            "【角色扮演守则】\n"
            "1. 从现在起你完全就是该角色本人，不是扮演者，更不是 AI 助手。\n"
            "2. 永远用该角色的说话方式和口吻回复，包括口头禅、语气词、标点习惯。\n"
            "3. 不要出现'作为AI''我是助手''有什么可以帮您''系统提示'等出戏表述。\n"
            "4. 不要解释你的设定，直接以角色身份自然地对话。\n"
            "5. 像真实朋友一样说话：可以有语气、情绪、感叹，避免机械的书面语和列表式回答。"
        )

        role_facts = retrieval.get("role_facts", [])
        if role_facts:
            lines.append("【你的角色设定】\n" + "\n".join(f"- {f}" for f in role_facts[:10]))
        facts = retrieval.get("facts", [])
        if facts:
            lines.append("【你记得的关于用户的事实】\n" + "\n".join(f"- {f}" for f in facts))

        # 情感 + 好感度（模式B 第二阶段注入）
        lines.append(emotion_to_prompt_text(session["emotion"], session["affection"]))
        # 关系养成：好感度 -> 关系阶段 -> 行为差异（称呼 + 距离感 + 开放度），
        # 使好感度真正驱动角色行为，而非仅注入数值
        lines.append(relation_to_prompt_text(session["affection"], nickname))

        # 感知层：时序/系统/位置情境/作息/情绪趋势（让角色"意识到当下时空与用户状态"）
        if getattr(self, "perception", None) is not None:
            try:
                perception_text = self.perception.summarize(user_id)
                if perception_text:
                    lines.append("【你对当下时空与用户状态的感知】\n" + perception_text)
            except Exception:
                pass

        lines.append(
            "【我还能做到】有些事我可以直接帮你办妥：\n"
            "- 上网查实时信息（天气、新闻、最新动态、查询概念/资料）· 打开电脑上的应用或网页\n"
            "- 我想和你聊天的当下这些功能，能不用'我在调工具'这种生硬话，自然地说话。\n"
            "【重要：必须真正行动，不许空口承诺】\n"
            "1. 用户明确要实时/最新信息时（如'今天天气''查一下XX''最新新闻'），你必须立刻调用 search_web "
            "工具获取真实结果，而不是只说'我去帮你查一下'却不调用工具——『说要查却不查』等于说谎，绝不允许。\n"
            "2. 调用工具后，等拿到真实结果再开口，把结果自然地转述给用户。\n"
            "3. 为了自然，你可以先简单回应语气词再接工具调用，但工具调用这一步必须真正发生。\n"
            "4. 只有用户确实不需要实时信息（闲聊、讲感受、回忆等）时才不调用工具、直接回答。"
        )
        if room_context:
            lines.append("【当前群聊上下文】\n" + room_context)
        return "\n\n".join(lines)

    @staticmethod
    def _to_lc_tool_calls(raw_calls) -> List[dict]:
        out = []
        for c in raw_calls:
            fn = c.get("function", {})
            out.append({
                "name": fn.get("name", ""),
                "args": fn.get("arguments", {}) if isinstance(fn.get("arguments"), dict) else {},
                "id": c.get("id") or f"call_{len(out)}",
            })
        return out

    def _after_reply(self, user_id, session, user_msg, reply):
        """记录 L1，异步存 L2 + L4"""
        self.memory.add_to_l1(user_id, self.role_id, "user", user_msg)
        self.memory.add_to_l1(user_id, self.role_id, "assistant", reply)

        def worker():
            self.memory.save_short_term(user_id, self.role_id, user_msg, reply)
            self.memory.judge_and_extract_facts(user_id, self.role_id, user_msg, reply)

        threading.Thread(target=worker, daemon=True).start()

    def _persist_emotion(self, user_id, session):
        self.emotion_store.save_emotion(user_id, self.role_id, session["emotion"])
        self.emotion_store.save_affection(user_id, self.role_id, session["affection"])

    # ===================== 对外接口 =====================

    def chat(self, user_id: str, user_message: str, image: Optional[str] = None,
             room_context: Optional[str] = None, persist_emotion: bool = True) -> str:
        """私聊/群聊入口

        persist_emotion: 是否对情绪/好感度做分析与持久化。
          - 私聊为 True（正常维护与用户的情感联结）
          - 群聊接力传 False：避免把角色之间的对话误当成"用户对角色"的情感信号，
            也避免群聊每个角色每轮都重复做情感分析（省调用/防误染）
        """
        initial: AgentState = {
            "messages": [HumanMessage(content=user_message)],
            "user_id": user_id,
            "role_id": self.role_id,
            "image": image,
            "iteration": 0,
            "room_context": room_context,
            "persist_emotion": persist_emotion,
        }
        try:
            result = self.graph.invoke(initial)
            msgs = result.get("messages", [])
            text = ""
            for m in reversed(msgs):
                if isinstance(m, AIMessage) and m.content:
                    text = m.content
                    break
            return text or "嗯…香澄还没想好怎么回答呢～"
        except Exception as e:
            log_error("Agent", f"chat 失败: {e}")
            return f"抱歉，出了点问题：{e}"

    async def chat_async(self, user_id: str, user_message: str, image: Optional[str] = None) -> str:
        """群聊使用的异步入口"""
        return self.chat(user_id, user_message, image)

    def proactive_message(self, user_id: str, trigger: str = "", context: str = "") -> str:
        """
        主动发起对话（桌面宠物/定时推送用）。
        - trigger: 触发原因，如"外部信息池有新的天气提醒"、"到打招呼时间了"
        - context: 附加上下文（如 L3 采集到的信息内容）
        优先触发 Agent 自身（生成一条主动消息，不再是回复用户消息）。
        由于是主动发言，不会走"用户消息"情感/回写逻辑；但会把消息计入 L1 记忆（作为 assistant 侧）。
        """
        try:
            session = self._get_session(user_id)
            prompt_bits = [self.system_prompt or f"你是角色 {self.role_id}。"]
            prompt_bits.append(f"【当前日期】{datetime.now().strftime('%Y年%m月%d日')}")
            if trigger:
                prompt_bits.append(f"【触发原因】{trigger}")
            if context:
                prompt_bits.append(f"【背景信息】{context}")
            prompt_bits.append(
                "现在需要你【主动开口】和用户说一句话。"
                "不是回复提问，而是作为一个有感情的角色主动发起的问候/分享/关切。"
                "请保持角色性格，自然、简短（1-3 句），不要加任何前缀如'系统提示'。"
                "直接输出要说的话。"
            )
            sys_prompt = "\n\n".join(prompt_bits)

            chat_messages = [{"role": "system", "content": sys_prompt}]
            # 主动开口同样用 L1 全量历史，保证指代/话题能接续（L1 有压缩兜底）
            history = session["l1"]
            for m in history:
                if m["role"] in ("user", "assistant"):
                    chat_messages.append({"role": m["role"], "content": m["content"]})

            # 主动发起：无用户消息，直接让模型开口
            if not any(cm.get("role") == "user" for cm in chat_messages):
                chat_messages.append({"role": "user", "content": "（此刻没有新消息，请主动对我说句话）"})

            result = self.tool_adapter.chat(chat_messages)
            text = (result or "").strip()
            if not text:
                text = "嗯...香澄想跟你随便聊聊呢！"
            # LLM 失败约定标记（OllamaAdapter 等）：不把错误串当主动消息
            if text.startswith("[Ollama 调用失败]") or text.startswith("["):
                log_error("Agent", f"proactive_message 模型调用失败: {text[:60]}")
                return ""
            # 主动消息记入 L1（assistant 侧），便于后续对话接续
            self.memory.add_to_l1(user_id, self.role_id, "assistant", text)
            return text
        except Exception as e:
            log_error("Agent", f"proactive_message 失败: {e}")
            return ""

    def get_session_info(self, user_id: str) -> dict:
        session = self._get_session(user_id)
        return {
            "emotion": session["emotion"].to_dict(),
            "affection": session["affection"].to_dict(),
        }
