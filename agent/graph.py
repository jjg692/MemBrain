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
from core.behavior import BehaviorMapper
from agent.planner import TaskPlanner
from core.config import (
    LIVE2D_BODY_MODE,
    ENVIRONMENT_SENSING_ENABLED,
    BROWSER_TAB_SENSING_ENABLED,
    FOREGROUND_SENSING_ENABLED,
)
from core.body_tools import merge_express

from core.relation_memory import RelationMemory, get_relation_memory, half_life_decay, build_reflection_prompt, parse_reflection


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
        # 最近一次回复的行为事件（供 WS 层随 reply 一起广播；无副作用仅缓存）
        self._last_behavior: Optional[Dict] = None
        # 关系记忆内核：持续存在的自我模型 / 共同经历账本 / 时间衰减（底层内在状态层）
        # 按 role_id 共享实例（role 内部按 user 分账），与五层记忆的 role 隔离语义一致。
        self.relation: Optional[RelationMemory] = None  # 惰性：首次需要时用 get_relation_memory(role_id)
        # M2 多步任务规划器（纯函数、确定性，可注入替身便于测试/关闭）
        self.task_planner = None  # 惰性：首次使用时确定为 TaskPlanner（可用 False 关闭）
        # 最近一次图运行的 task_status / plan（供 WS/test 读取任务收敛结果；无副作用）
        self.last_task_status: Optional[Dict] = None
        self.last_task_plan: Optional[Dict] = None

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

    def _timing_hint(self, user_id: str) -> str:
        """④ 主动择时：用感知层判断"此刻是否适合主动、该说什么"。

        复用 PerceptionManager 已采集的数据（不新增采集、不额外调 LLM）：
        - 长时间没联系 -> "想念/关心"时机；
        - 深夜且平时安静 -> "熬夜关心"时机（克制）；
        - 在正常时段 -> 不额外加择时，保持自然。
        返回一句可注入的"此刻时机"提示；没有明确时机时返回空串。
        """
        try:
            perception = getattr(self, "perception", None)
            if perception is None:
                return ""
            day_hint = ""
            quiet_hint = ""
            # 断联/在场（感知层 attendance）
            try:
                att = perception.routine.attendance(user_id)
                if att and att.get("has_log"):
                    days = int(att.get("days_since_contact", 0) or 0)
                    if days >= 3:
                        day_hint = f"你们好几天没聊了（约{days}天），可以自然地想念/关心一下。"
                    elif days <= 0:
                        day_hint = "用户刚刚还在，可以轻松地招呼一句。"
            except Exception:
                pass
            # 深夜且平时安静 -> 熬夜关心
            try:
                from core.perception import time_situation
                t = time_situation()
                if t.get("period") in ("深夜", "清晨"):
                    quiet = perception.routine.quiet_hours(user_id)
                    qt = getattr(perception.routine, "quiet_hours", None)
                    if qt and quiet and t["now"] and (int(t["now"][11:13]) if len(t["now"]) >= 13 else -1) in quiet:
                        quiet_hint = "这个点你平时通常睡了，如果你还在线，可以温柔提一句别太晚。"
            except Exception:
                pass
            parts = [h for h in (day_hint, quiet_hint) if h]
            if not parts:
                return ""
            return "· 此刻时机：" + " ".join(parts[:2])
        except Exception:
            return ""

    def _compose_proactive_hint(self, user_id: str, trigger: str = "", context: str = "") -> str:
        """主动性 harness 辅助：从关系记忆里挑"此刻值得主动开口"的素材。

        优先级（克制、有界、不强制）：
        1. 未兑现的承诺（人格一致性：自然地记起答应过的事）
        2. 最近鲜活经历 / 共同兴趣（记忆召回）
        3. 用户情绪走向（情感智力：在对方低落时给一句关心）
        纯 harness 确定性挑选，不额外调 LLM；生成自然语句仍交给主模型。
        """
        try:
            rel = self._get_relation()
            if rel is None:
                return ""
            lines = []
            # 0) 择时：感知层判断此刻是否适合主动、更合适说什么（④ 主动择时）
            timing = self._timing_hint(user_id)
            if timing:
                lines.append(timing)
            # 感知→表达触发对齐：前台/标签页变化时给低频提示（主动开口也能自然接上一句）
            try:
                from core.sensing_hint import sensing_change_hint
                change_hint = sensing_change_hint(user_id)
                if change_hint:
                    lines.append(change_hint)
            except Exception:
                pass

            # 1) 未兑现承诺
            promises = rel.pending_promises(user_id, n=3)
            if promises:
                lines.append("- 你可能答应过/记得要做的是：" + "；".join(
                    "「%s」" % str(p.get("text", ""))[:40] for p in promises))
            # 2) 鲜活经历 / 共同话题（最近 3 条，按话题相关度，取共通点）
            recall = rel.decayed_episodes(user_id, n=6, min_vitality=0.3)
            topics = []
            for ep in recall:
                msg = str(ep.get("user_msg", "")).strip()
                if msg and len(msg) >= 2:
                    topics.append(msg[:30])
            if topics:
                lines.append("- 你们最近聊过：" + "、".join(topics[:3]))
            # 3) 情绪走向（若明显偏负，提示一句关心）
            mood = rel._mood_trend_text(user_id)
            if mood and ("变差" in mood):
                lines.append("- 用户最近情绪不太好，可以温柔地关心一句（别追问、别施压）。")
            return "\n".join(lines)
        except Exception:
            return ""

    def _get_session(self, user_id: str) -> dict:
        """获取用户的会话状态（L1 列表、情感、好感度）"""
        if user_id not in self._user_states:
            # 冷启动：L2 加载到 L1
            self.memory.cold_start_load(user_id, self.role_id)
            emotion = self.emotion_store.load_emotion(user_id, self.role_id) or EmotionState.default()
            affection = self.emotion_store.load_affection(user_id, self.role_id) or AffectionState.default()
            # 关系记忆内核：冷加载时对跨会话的情绪/好感度做**真实时间衰减**
            # （久别再聊，强度/好感度会像真人一样向基线回落，而不是冻结在旧瞬时值）
            try:
                rel = self._get_relation()
                if rel is not None:
                    emo_d, aff_d = rel.decay_state(user_id, emotion, affection)
                    emotion = emo_d or emotion
                    affection = aff_d or affection
            except Exception:
                pass
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
        workflow.add_node("observe", self._observe_node)

        workflow.set_entry_point("agent")

        workflow.add_conditional_edges(
            "agent",
            self._route_after_agent,
            {"tools": "tools", "end": END},
        )
        # act: 工具执行后先经 observe 汇总观察，再回 agent 决定下一步
        workflow.add_edge("tools", "observe")
        workflow.add_edge("observe", "agent")

        return workflow.compile()

    def _tool_registry_fns(self):
        from langchain_core.tools import tool
        funcs = []
        for name, fn in TOOL_REGISTRY.items():
            # LangChain tool() 需要 docstring 或 description；缺则兜底补一个，
            # 避免个别注册函数（如 MCP 包装）无描述导致 Agent 图构建失败。
            if not getattr(fn, "__doc__", None):
                try:
                    fn.__doc__ = f"执行工具：{name}"
                except Exception:
                    pass
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

        # ========== M2 任务循环：plan 阶段（只在第一轮，无 plan 时规划） ==========
        # 用确定性纯函数 TaskPlanner 判断「简单一句消费」vs「需要多步任务」。
        # 若为多步任务，生成骨架 plan 存入 state；后续 agent 轮根据 plan + observe
        # 维护的 task_status 决定执行哪个子步。简单单轮则 plan 保持 None，行为不回归。
        plan = state.get("plan")
        if plan is None and self.task_planner is not False:
            planner = self.task_planner or TaskPlanner
            try:
                p = planner.plan(user_msg)
                plan = p.to_dict() if p else None
            except Exception:
                plan = None
        task_status = state.get("task_status") or {}

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
            session, retrieval, user_id, role_id, state.get("room_context"), user_msg
        )
        # M2：任务上下文注入（若有 plan）——让 LLM 知道当前是多步任务及进度
        if plan:
            base_prompt = base_prompt + self._build_task_prompt(plan, task_status)


        # 场景 A：对话图片经本地视觉模型转述给主模型（若 VISION_ENABLED+VISION_IN_CHAT）
        if state.get("image"):
            try:
                from core.vision import get_vision_service
                _desc = get_vision_service().describe_image(state.get("image"))
                if _desc:
                    base_prompt = base_prompt + ("\n\n【用户刚发来一张图片，以下是视觉转述（机器识别，可能有误，供参考）】\n" + _desc)
            except Exception:
                pass

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
            return {"messages": [msg], "iteration": state.get("iteration", 0) + 1,
                    "plan": plan, "task_status": task_status}

        # ---- 兜底守卫：模型"说要查却不查"时的可靠性补位 ----
        # 自治路由把工具决定权交给 LLM，但本地/远程模型偶发会只用口述承诺
        # （如"让我帮你查一下天气"、”我去给你设个提醒”）却不发起 tool_call，
        # 导致"说查不查 / 说提醒不提醒"。这里做一层无害兜底：
        #   当用户消息明显需要实时信息（天气/百科/联网查询）或明确要求设提醒，
        #   且本轮尚未真正执行过任何工具，且模型没走工具就准备直接回答时，
        #   强制注入一次对应工具，让 tools 节点真正执行。
        if (
            self.tool_fallback
            and not self._tool_executed_this_turn(state)
            and state.get("iteration", 0) < 8
        ):
            forced = None
            if self._needs_realtime(user_msg):
                forced = [{
                    "name": "search_web",
                    "args": {"query": user_msg},
                    "id": f"forced_search_{state.get('iteration', 0)}",
                }]
            elif self._needs_remind(user_msg):
                forced = [{
                    "name": "remind_me",
                    "args": {"text": user_msg, "when": ""},
                    "id": f"forced_remind_{state.get('iteration', 0)}",
                }]
            if forced:
                msg = AIMessage(
                    content=content or "",
                    tool_calls=forced,
                )
                return {"messages": [msg], "iteration": state.get("iteration", 0) + 1,
                        "plan": plan, "task_status": task_status}

        # 无工具：最终回复（任务循环的完成确认 / 单轮的直接回复）
        final_msg = AIMessage(content=content)
        self._after_reply(user_id, session, user_msg, content)
        # 多步任务在此结束：把已完成状态写回（供外部/测试读取收敛结果）
        if plan:
            task_status["done"] = True
            task_status["conclusion"] = content
        return {"messages": [final_msg], "iteration": state.get("iteration", 0) + 1,
                "plan": plan, "task_status": task_status}

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
    def _needs_remind(user_msg: str) -> bool:
        """判断用户消息是否明确要求设提醒（兜底守卫用）。

        复用 core.tools._detect_remind 的保守词表（只认"提醒/设提醒/定时"等明确表述，
        不含裸"记"字），避免把纯闲聊强制成 remind_me。
        """
        try:
            from core.tools import _detect_remind
            return _detect_remind(user_msg or "")
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

    # ===================== M2 任务循环：observe / 提示注入 =====================

    def _observe_node(self, state: AgentState) -> dict:
        """observe 节点：工具执行后，汇总本轮工具结果到 task_status.observations。

        有 plan（多步任务）时才记录；单轮无 plan 时保持 task_status 为空，不产生副作用。
        只做累积记录，不改变执行路径（tools -> observe -> agent）。
        """
        task_status = dict(state.get("task_status") or {})
        obs = list(task_status.get("observations") or [])
        plan = state.get("plan")
        # 取最近一条 ToolMessage 作为本轮观察
        for m in reversed(state.get("messages") or []):
            if isinstance(m, ToolMessage):
                obs.append({
                    "tool_call_id": getattr(m, "tool_call_id", ""),
                    "result": (m.content or "")[:400],
                })
                break
        task_status["observations"] = obs
        # 若有 plan，标记当前进行中的子步/进度（供提示注入）
        if plan:
            task_status.setdefault("progress", {"done": 0, "total": len(plan.get("steps") or [])})
        return {"task_status": task_status}

    def _build_task_prompt(self, plan: Dict, task_status: Dict) -> str:
        """把多步任务骨架 + 当前进度注入 system prompt，指导 LLM 按步骤完成并确认。"""
        steps = plan.get("steps") or []
        lines = ["", "【当前多步任务】你要帮用户完成一个多步任务，请按顺序想清楚再逐步行动："]
        lines.append(f"- 任务目标：{plan.get('goal') or ''}")
        for s in steps:
            mark = "☐"
            if s.get("status") == "done":
                mark = "☑"
            hint = f"（可用工具：{s['tool_hint']}）" if s.get("tool_hint") else ""
            lines.append(f"  {mark} 第{s.get('index', 0) + 1}步：{s.get('description')}{hint}")
        obs = (task_status.get("observations") or [])
        if obs:
            lines.append("【已执行的中间结果】")
            for o in obs[-5:]:
                lines.append(f"- {str(o.get('result') or '')[:120]}")
        lines.append(
            "【要求】先做必要的工具调用去完成每个子步；全部完成后，给用户一句自然的总结确认，"
            "不要只做第一步就停。若某一步无法完成，如实说明。"
        )
        return "\n".join(lines)

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

    def _build_system_prompt(self, session, retrieval, user_id, role_id, room_context=None, user_msg="") -> str:
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
        # 认知架构统一：以"衰减后的内在状态"为单一事实源，避免即时值与内核注入不一致。
        # decay_state 返回副本、不修改入参；久别再聊时强度/好感度会自然回到基线。
        _emo, _aff = session["emotion"], session["affection"]
        try:
            rel_cur = self._get_relation()
            if rel_cur is not None:
                _de, _da = rel_cur.decay_state(user_id, _emo, _aff)
                if _de is not None:
                    _emo = _de
                if _da is not None:
                    _aff = _da
        except Exception:
            pass
        lines.append(emotion_to_prompt_text(_emo, _aff))
        # 关系养成：好感度 -> 关系阶段 -> 行为差异（称呼 + 距离感 + 开放度），
        # 使好感度真正驱动角色行为，而非仅注入数值（用衰减后的好感度推导关系阶段）
        # ② 有原因的演化：共同经历证据参与升段判断（经历足够时阶段可提前，且"有据可依"）
        _exp = None
        try:
            _rel_stage = self._get_relation()
            if _rel_stage is not None:
                _exp = _rel_stage.experience_evidence(user_id)
        except Exception:
            _exp = None
        lines.append(relation_to_prompt_text(_aff, nickname, experience=_exp))

        # 感知层：时序/系统/位置情境/作息/情绪趋势（让角色"意识到当下时空与用户状态"）
        if getattr(self, "perception", None) is not None:
            try:
                perception_text = self.perception.summarize(user_id)
                if perception_text:
                    lines.append("【你对当下时空与用户状态的感知】\n" + perception_text)
            except Exception:
                pass

        # 感知→表达触发对齐：前台/标签页变化时给低频提示（感知当前变化，落地主动择时）
        try:
            from core.sensing_hint import sensing_change_hint
            _chg = sensing_change_hint(user_id)
            if _chg:
                lines.append("【感知到的变化】" + _chg)
        except Exception:
            pass

        # 滚动帧趋势感知（第 2/3 项）：跨帧归纳"你刚才从 X 切到了 Y / 一直专注在 Z"。
        # 有内容变化时才提示，让角色接续语境，纯 harness 确定性归纳。
        try:
            from core.sensing_hint import frame_trend
            _trend = frame_trend(user_id)
            if _trend:
                lines.append("【感知到的注意力趋势】" + _trend)
        except Exception:
            pass


        # 关系记忆内核：持续存在的自我模型 / 对用户的长久理解 / 共同经历（底层内在状态层）
        try:
            rel = self._get_relation()
            if rel is not None:
                rel_txt = rel.summary_text(user_id, max_chars=360, query=user_msg)
                if rel_txt:
                    lines.append("【你的内在状态与和用户的长久积累】\n" + rel_txt)
        except Exception:
            pass

        lines.append(
            "【我还能做到】有些事我可以直接帮你办妥：\n"
            "- 上网查实时信息（天气、新闻、最新动态、查询概念/资料）· 打开电脑上的应用或网页\n"
            "- 我想和你聊天的当下这些功能，能不用'我在调工具'这种生硬话，自然地说话。\n"
            "【重要：必须真正行动，不许空口承诺】\n"
            "1. 用户明确要实时/最新/外部资料信息时（如'今天天气''查一下XX''最新新闻''XX是什么'），你必须立刻调用 "
            "search_web 工具获取真实结果，而不是只说'我去帮你查一下'却不调用工具——『说要查却不查』等于说谎，绝不允许。\n"
            "2. 调用工具后，等拿到真实结果再开口，把结果自然地转述给用户。\n"
            "3. 为了自然，你可以先简单回应语气词再接工具调用，但工具调用这一步必须真正发生。\n"
            "4. **不要因为闲聊就调工具**：以下情况**绝不调用 search_web**，而是直接以角色身份聊天——\n"
            "   · 用户分享感受、回忆、观点、心情（'我最喜欢的歌是…''你怎么看…'）\n"
            "   · 用户问你个人看法、偏好、过去经历（'你印象最深的演出''放假想去做什么''你喜欢什么'）\n"
            "   · 纯社交寒暄、闲聊、开玩笑、分享计划——这些都**不需要任何网络查询**。\n"
            "   只有用户**明确要求**查实时/最新/外部资料时，才调用 search_web。"
        )

        # 环境感知能力：仅当感知开关开启时，告知 LLM 它能看到当前浏览器标签页/前台窗口/拉取感知摘要。
        # 关闭时完全不提，避免 LLM 误以为有这能力却读不到（不伪造）。
        try:
            if ENVIRONMENT_SENSING_ENABLED:
                sense_lines = ["【你的感知】我能感知到用户此刻的环境："]
                if BROWSER_TAB_SENSING_ENABLED:
                    sense_lines.append("- 我能查看用户当前浏览器(Chrome/Edge)打开的标签页标题与链接，"
                        "知道他此刻在看什么（如他在查资料、看视频、看新闻）。需要时调用 get_current_tab。")
                if FOREGROUND_SENSING_ENABLED:
                    sense_lines.append("- 我能查看用户当前正在用的前台应用/窗口（不限浏览器，如写文档、看代码、看视频）。"
                        "需要时调用 get_foreground_window。")
                sense_lines.append("- 我能拉取用户当下的环境感知摘要（时间/时段/场景/作息/最近心情与好感度趋势/是否在线），"
                        "需要时调用 get_perception_summary。")
                sense_lines.append("这些能力只有在用户想知道你能’看到’什么，或你确实需要确认用户当下在看什么/心境时使用，"
                        "不要为了调用而调用。若工具返回’未开启/读取失败’，如实告诉用户该功能尚未开启，不要凭空编造看到的内容。")
                lines.append("\n".join(sense_lines))
        except Exception:
            pass

        # Live2D 身体表达（C 方案）：告知 LLM 它有一个身体，情绪会自动映射成表情/动作
        # 只在 C 模式注入；B 模式由 express_body 工具承担"主动指挥"，不必重复告知自动映射。
        try:
            if LIVE2D_BODY_MODE == "B":
                lines.append(
                    "【你的身体表达】你拥有一个 Live2D 形象(能表情/动作/情绪的肢体)。"
                    "当你想用肢体强调某个情绪或动作(如开心挥手、惊讶睁眼致敬)时，"
                    "调用 express_body 工具来指挥身体；调用后自然说出你的话即可。"
                    "不要为了调用而调用——只需在真正想表达肢体/情绪强度时使用。"
                )
            else:
                lines.append(
                    "【你的身体】你拥有一个 Live2D 形象，能通过表情(眉眼/嘴巴/脸颊)、"
                    "头部姿态和肢体动作表达情绪。你就自然地按当下情绪说话即可，"
                    "你的情绪会自动同步到形象的表情与动作上，无需特别说明或输出指令。"
                )
        except Exception:
            pass

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
        """记录 L1，异步存 L2 + L4；并计算/缓存本次回复的行为事件（behavior）。"""
        self.memory.add_to_l1(user_id, self.role_id, "user", user_msg)
        self.memory.add_to_l1(user_id, self.role_id, "assistant", reply)

        # 行为事件：由纯函数 BehaviorMapper 基于会话情绪 + 回复文本推导（无副作用）。
        # 缓存到实例，供 WS 层在广播 reply 时一并下发（契约 §3.2，向后兼容：壳未收到也不影响）。
        try:
            emotion_state = session.get("emotion")
            self._last_behavior = BehaviorMapper.derive_from_state(reply, emotion_state)
            # B 方案：若本轮回合 LLM 调用过 express_body，把其主动指定的情绪/表情/动作
            # 并入 behavior（LLM 主动指挥优先于自动映射；无调用则原样返回，向后兼容）
            if LIVE2D_BODY_MODE == "B":
                self._last_behavior = merge_express(self._last_behavior)
        except Exception:
            self._last_behavior = None

        def worker():
            self.memory.save_short_term(user_id, self.role_id, user_msg, reply)
            self.memory.judge_and_extract_facts(user_id, self.role_id, user_msg, reply)
            # 关系记忆内核：沉淀共同经历 + 周期反思（底层内在状态层，异步不阻塞回复）
            self._relation_after_reply(user_id, user_msg, reply, session)
            # 承诺兑现闭环：用户表达"谢谢/记得/办到了"等确认时，把相关承诺标记为已兑现
            try:
                rel_c2 = self._get_relation()
                if rel_c2 is not None:
                    rel_c2.resolve_promises_on_user_signal(user_id, user_msg)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def last_behavior(self) -> Optional[Dict]:
        """返回最近一次回复的 behavior 事件（供 WS 层随 reply 广播）。返回副本避免外部改动。"""
        if self._last_behavior is None:
            return None
        try:
            import copy
            return copy.deepcopy(self._last_behavior)
        except Exception:
            return dict(self._last_behavior)

    def _persist_emotion(self, user_id, session):
        self.emotion_store.save_emotion(user_id, self.role_id, session["emotion"])
        self.emotion_store.save_affection(user_id, self.role_id, session["affection"])

    def _get_relation(self):
        """惰性取关系记忆内核实例（按 role_id；可被测试注入替身）。"""
        if self.relation is None:
            try:
                self.relation = get_relation_memory(self.role_id)
            except Exception:
                self.relation = None
        return self.relation

    def _relation_after_reply(self, user_id, user_msg, reply, session):
        """关系记忆内核写入钩子（后台 worker 线程调用，不阻塞回复）。
        1. 写共同经历账本（共振阈值过滤日常闲聊；有 impact 的算重要）。
        2. 融合当前情绪/好感度到衰减通道。
        3. 距上次反思超过 RELATION_REFLECT_INTERVAL 时，用 LLM 做周期反思并沉淀。
        """
        try:
            rel = self._get_relation()
            if rel is None:
                return
            emotion_state = session.get("emotion")
            emotion_dict = None
            if emotion_state is not None and hasattr(emotion_state, "to_dict"):
                try:
                    emotion_dict = emotion_state.to_dict()
                except Exception:
                    emotion_dict = None
            resonance = 0.4
            if emotion_dict:
                try:
                    intensity = float(emotion_dict.get("intensity") or 0.5)
                    valence = float(emotion_dict.get("valence") or 0.0)
                    resonance = round(0.3 + intensity * (0.5 + abs(valence) * 0.5), 3)
                except Exception:
                    resonance = 0.4
            impact = self._detect_impact(reply, user_msg)
            rel.add_episode(user_id, user_msg, reply, emotion=emotion_dict,
                            resonance=resonance, impact=impact)
            if emotion_state is not None or session.get("affection") is not None:
                rel.apply_decay(user_id, emotion_state, session.get("affection"))
            rel.mark_active(user_id)
            try:
                from core.config import RELATION_REFLECT_INTERVAL
                interval = RELATION_REFLECT_INTERVAL
            except Exception:
                interval = 0
            if interval and interval > 0:
                last = (rel.latest_reflection(user_id) or {}).get("ts", "")
                if not last:
                    self._maybe_reflect(user_id, rel)
                else:
                    from datetime import datetime as _dt
                    try:
                        delta = (_dt.now() - _dt.fromisoformat(last)).total_seconds()
                        if delta >= interval:
                            self._maybe_reflect(user_id, rel)
                    except Exception:
                        pass
        except Exception:
            pass

    @staticmethod
    def _detect_impact(reply, user_msg):
        """粗略识别实际动作（布尔提示，不替代 LLM）。"""
        reply = reply or ""
        marks = ("已为你设置提醒", "已在浏览器打开", "已打开", "已写入", "搜索结果", "当前天气", "当前气温", "已提醒")
        return "做出了一个实际动作" if any(k in reply for k in marks) else None

    def _maybe_reflect(self, user_id, rel):
        """用 LLM 做一次周期反思并沉淀（失败/不可用静默跳过）。

        候选经历数量受 RELATION_REFLECT_BATCH 配置约束（落地批量配置），
        避免单次反思把大量候选经历全塞进 prompt 造成膨胀。
        """
        try:
            try:
                from core.config import RELATION_REFLECT_BATCH
                batch = RELATION_REFLECT_BATCH
            except Exception:
                batch = 0
            episodes = rel.decayed_episodes(user_id, n=max(batch or 0, 6))
            if not episodes:
                return
            last = rel.latest_reflection(user_id)
            prev = last.get("reflection", {}) if last else {}
            prompt = build_reflection_prompt(episodes, prev, max_batch=batch)
            text = self.tool_adapter.chat([{"role": "user", "content": prompt}]).strip()
            data = parse_reflection(text)
            if isinstance(data, dict) and data:
                rel.store_reflection(user_id, data)
        except Exception:
            pass

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
            # 记录任务循环的最终 plan/task_status（供 WS/测试读取）
            self.last_task_plan = result.get("plan")
            self.last_task_status = result.get("task_status")
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
            # 主动性（harness 辅助）：从关系记忆里挑"此刻值得主动开口"的素材，
            # 让主动不再只是"到点问候"，而是结合承诺/记忆漏洞/鲜活经历/最近话题。
            proactive_hint = self._compose_proactive_hint(user_id, trigger, context)
            prompt_bits = [self.system_prompt or f"你是角色 {self.role_id}。"]
            prompt_bits.append(f"【当前日期】{datetime.now().strftime('%Y年%m月%d日')}")
            if trigger:
                prompt_bits.append(f"【触发原因】{trigger}")
            if context:
                prompt_bits.append(f"【背景信息】{context}")
            if proactive_hint:
                prompt_bits.append("【此刻可以自然地提起的】\n" + proactive_hint)
            prompt_bits.append(
                "现在需要你【主动开口】和用户说一句话。"
                "不是回复提问，而是作为一个有感情的角色自然发起的问候/分享/关切。"
                "优先自然地提起上面【此刻可以自然地提起的】里的内容（比如记得对方的承诺、"
                "上次聊到一半的话题、或一句关心），但不要生硬地罗列、不要复述'我有一条承诺'之类。"
                "请保持角色性格，自然、简短（1-3 句），不要加任何前缀如'系统提示'。"
                "没有合适的话题时，就自然地问候或分享一件小事。"
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
            # 主动开口同样推导 behavior（宠物壳可据此做表情/口型）
            try:
                self._last_behavior = BehaviorMapper.derive(text, session.get("emotion"))
            except Exception:
                self._last_behavior = None
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
