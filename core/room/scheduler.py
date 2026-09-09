"""
群聊接力调度器（A 阶段优化）
================================
把原来 api/websocket.py 里"串行 for _speak_all + 全局 ROOM_RELAY_ROUNDS"的
接力逻辑替换为**每房间一个协程调度器**，解决三个痛点：

1. 串行等待 / 延迟累加  -> 有界并发（asyncio.Semaphore），房间可配并发额度
2. 无法中途打断         -> 调度器以事件循环跑，用户新消息触发 interrupt 重置当前接力
3. 无成员粒度控制        -> 复用 Room.member_config 的 participate/weight 做选择性发言
   + 附带打字指示器（room_turn 广播，前端可显示"谁在思考"）与失败降级。

对齐项目"deterministic / bounded / harness 辅助"原则：
- 不新增 LLM 调用，只做调度编排。
- 单角色发言用 asyncio.wait_for 包超时；超时/异常静默跳过（不伪造、不阻塞其他角色）。
- 发言用 run_in_executor 跑同步 agent.chat（与旧实现一致），保留完整 room_context。
- 一个房间一个调度任务；无消息时任务挂起等待，零轮询开销。

消息播放（playout）流程：
    submit_user_message() -> 触发 interrupt + 保证任务在跑
    _room_loop：等事件 -> 从 bus 取最新用户消息 -> 排演一轮
    _playout：首轮回应（挑选策略）-> 接力轮 N 次
    期间新用户消息 -> 再次 interrupt -> 循环重启，旧接力自然被打断
"""
import asyncio
import random
from typing import Dict, Optional

from core.logger import log_error, log_info


class RoomTurnScheduler:
    def __init__(self, bus, room_manager, config=None):
        self.bus = bus
        self.room_manager = room_manager
        self._config = config or {}
        self._default_relay_rounds = int(self._config.get("relay_rounds", 2))
        self._default_max_concurrency = int(self._config.get("max_concurrency", 2))
        self._tasks: Dict[str, asyncio.Task] = {}
        self._semaphores: Dict[str, asyncio.Semaphore] = {}
        self._interrupts: Dict[str, asyncio.Event] = {}

    # ===================== 对外接口 =====================

    def submit_user_message(self, room_id: str):
        """用户消息已写入 bus；唤醒该房间调度器，若在接力则打断重启。"""
        room = self.room_manager.get_room(room_id)
        if room is None:
            return
        if room_id not in self._tasks or self._tasks[room_id].done():
            self._start_room(room_id)
        self._interrupts.setdefault(room_id, asyncio.Event()).set()

    def shutdown_room(self, room_id: str):
        """房间被删除时清理任务。"""
        task = self._tasks.pop(room_id, None)
        if task is not None:
            task.cancel()
        self._semaphores.pop(room_id, None)
        self._interrupts.pop(room_id, None)

    def _start_room(self, room_id: str):
        task = asyncio.ensure_future(self._room_loop(room_id))
        self._tasks[room_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(room_id, None) if self._tasks.get(room_id) is _t else None)

    def _sem(self, room_id: str) -> asyncio.Semaphore:
        room = self.room_manager.get_room(room_id)
        limit = room.max_concurrency if room else self._default_max_concurrency
        limit = max(1, limit)
        sem = self._semaphores.get(room_id)
        if sem is None or sem._value != limit:
            sem = asyncio.Semaphore(limit)
            self._semaphores[room_id] = sem
        return sem

    # ===================== 调度循环 =====================

    async def _room_loop(self, room_id: str):
        ev = self._interrupts.setdefault(room_id, asyncio.Event())
        gen = 0
        while True:
            await ev.wait()
            ev.clear()
            gen += 1
            try:
                await self._playout(room_id, gen)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_error("Room", f"[{room_id}] 调度异常: {e}")

    async def _playout(self, room_id: str, gen: int):
        room = self.room_manager.get_room(room_id)
        if room is None:
            return
        user_msg = self._latest_user_message(room_id)
        if not user_msg:
            return

        # 首轮：回应用户（若 @点名，则只让被点名的角色回应）
        await self._speak_batch(
            room_id, user_msg, round_idx=0, gen=gen,
            speakers=self._select_speakers_for_message(room, user_msg),
        )

        # 接力轮
        rounds = self._relay_rounds(room)
        if not (room.enable_relay and rounds > 0):
            return
        relay_prompt = (
            "（群聊接力：上面是群里最新对话。请以你自己的身份自然接一句话，"
            "回应/调侃/接续别人刚说的话，或补充一个观点。保持角色性格，简短自然，"
            "不要重复已经说过的话，不要一次说太多。）"
        )
        for i in range(1, rounds + 1):
            if self._interrupts.get(room_id) and self._interrupts[room_id].is_set():
                log_info("Room", f"[{room_id}] 接力被用户新消息打断")
                break
            await self._speak_batch(room_id, relay_prompt, round_idx=i, gen=gen)

    def _select_speakers_for_message(self, room, user_msg):
        """识别用户消息里的 @点名：@role_id 或 @显示名 只让被点名角色回应。

        未点名则走常规 select_speakers（参与度/权重策略）。
        """
        mentioned = self._extract_mentions(room, user_msg or "")
        if not mentioned:
            return self._select_speakers(room)
        # 只保留被点名且参与聊天的角色
        return [r for r in mentioned if room.get_member_participate(r)]

    @staticmethod
    def _extract_mentions(room, text: str) -> Optional[list]:
        """解析 @role_id / @角色显示名，返回匹配到的 role_id 列表；无则 None。"""
        import re
        if not text or "@" not in text:
            return None
        # 收集可作为 @ 目标的名字：role_id + 已知显示名
        names = list(room.members.keys())
        # 尝试通过 agent 拿 display_name（若 room_manager 有 contacts；这里用 role_id 稳妥）
        mentions = set()
        for token in re.findall(r"@([^\s,，。！!？?]+)", text):
            t = token.strip()
            if t in room.members:
                mentions.add(t)
            else:
                # 尝试后缀匹配 role_id（如 @香澄 -> kasumi 需由外部映射，这里仅精确匹配）
                for rid in names:
                    if rid and (t == rid or t in rid):
                        mentions.add(rid)
        return list(mentions) if mentions else None

    def _latest_user_message(self, room_id: str) -> str:
        recent = self.bus.get_recent_messages(room_id, n=10)
        for m in reversed(recent):
            if m.is_user and m.content:
                return m.content
        return ""

    def _relay_rounds(self, room) -> int:
        if room.relay_rounds is not None:
            return room.relay_rounds
        return self._default_relay_rounds

    # ===================== 发言批次 =====================

    async def _speak_batch(self, room_id, prompt, round_idx, gen, speakers=None):
        room = self.room_manager.get_room(room_id)
        if room is None:
            return
        if speakers is None:
            speakers = self._select_speakers(room)
        if not speakers:
            return
        sem = self._sem(room_id)
        timeout = room.member_timeout_s

        async def _one(role):
            agent = room.members.get(role)
            if agent is None:
                return
            await self.bus.broadcast_event(room_id, {
                "type": "room_turn", "role_id": role, "status": "thinking",
            })
            # 上下文排除自己，避免"角色跟自己对话"；限制字符数防膨胀
            ctx = self.bus.get_formatted_context(room_id, n=30, exclude_role=role, max_chars=2000)
            user_id = "_room_" + room_id  # 与原实现一致：群聊用 __room__ 维度
            try:
                async with sem:
                    async def _call():
                        loop = asyncio.get_running_loop()
                        text = await loop.run_in_executor(
                            None, agent.chat, user_id, prompt, None, ctx, False
                        )
                        return text
                    text = await asyncio.wait_for(_call(), timeout=timeout)
            except asyncio.TimeoutError:
                log_error("Room", f"[{room_id}] {role} 发言超时，跳过")
                await self.bus.broadcast_event(room_id, {
                    "type": "room_turn", "role_id": role, "status": "timeout",
                })
                return
            except Exception as e:
                log_error("Room", f"[{room_id}] {role} 发言失败: {e}")
                await self.bus.broadcast_event(room_id, {
                    "type": "room_turn", "role_id": role, "status": "error",
                })
                return
            text = (text or "").strip()
            if text:
                await self.bus.send_agent_message(room_id, role, text)
            await self.bus.broadcast_event(room_id, {
                "type": "room_turn", "role_id": role, "status": "done",
            })

        # 同一轮内角色并行（受信号量约束），打破原串行长延迟
        await asyncio.gather(*[_one(r) for r in speakers])

    # ===================== 挑选策略 =====================

    def _select_speakers(self, room):
        eligible = [r for r in room.members if room.get_member_participate(r)]
        if not eligible:
            return []
        if room.relay_style == "all" or len(eligible) <= 2:
            return eligible
        # weighted：按 weight 无放回抽取 2~3 个不同角色，避免全员话痨扎堆
        weights = [room.get_member_weight(r) for r in eligible]
        k = min(max(2, int(len(eligible) * 0.6)), len(eligible), 3)
        chosen = self._weighted_sample(eligible, weights, k)
        return chosen

    @staticmethod
    def _weighted_sample(items, weights, k):
        """按权重无放回抽取 k 个不同项（回退：不足则取全部）。"""
        if not items or k <= 0:
            return []
        k = min(k, len(items))
        pool = list(range(len(items)))
        picked = []
        while len(picked) < k and pool:
            w = [weights[i] for i in pool]
            idx = random.choices(pool, weights=w, k=1)[0]
            picked.append(items[idx])
            pool.remove(idx)
        return picked
