"""
星露谷运行时桥（方向一 · 运行时）：让主 Agent 运行时自动感知游戏状态并沉淀记忆

设计：
- 后台线程轮询 `mcp_stardew_get_state`（只读、零副作用）。
- 当检测到有意义的游戏变化（地点 / 天气 / 季节 / 日期 / 同伴 / 金钱等）时，
  用 GameMemoryBridge 反写一条紧凑的"游戏事件"到 L1/L4 记忆，宠物以后能回忆。
- 完全不改动 Agent 的 ReAct 闭环：仅仅是一个可选的、可关闭的旁路观察者。
- 降级友好：MCP 未启用 / server 未启动 / 游戏未开（bridge 不存在）时静默跳过，
  绝不抛异常影响主进程。
"""
import datetime
import threading
import time
from typing import Dict, Optional

from core.logger import log_info, log_debug, log_error


class GameStatePoller:
    """周期性读取星露谷游戏状态并沉淀记忆。"""

    # 轮询间隔默认 60s；可从外部覆盖（测试/后台配置用）
    DEFAULT_INTERVAL = 60.0

    def __init__(self, memory_bridge, tool_call=None, interval: Optional[float] = None,
                 role_id: str = "kasumi"):
        """
        Args:
            memory_bridge: GameMemoryBridge 实例（或 None，表示记忆不可用）。
            tool_call: callable(tool_name:str, arguments:dict) -> str，默认取 MCP manager。
                测试时可注入假实现。
            interval: 轮询间隔秒。
        """
        self.bridge = memory_bridge
        self._tool_call = tool_call
        self.interval = float(interval if interval is not None else self.DEFAULT_INTERVAL)
        self.role_id = role_id

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # 状态缓存（用于变化检测 + 后台页面展示）
        self.last_state: Optional[Dict] = None
        self.last_success_at: Optional[str] = None
        self.last_error: Optional[str] = None
        self.recorded_events: int = 0
        self.last_recorded_at: Optional[str] = None

        self._lock = threading.Lock()

    # ---------------- 工具调用 ----------------

    @staticmethod
    def _find_state_tool_name() -> Optional[str]:
        """在 TOOL_REGISTRY 里找到星露谷'读取状态'工具的真实注册名。

        真实的 MCP 工具是 mcp_<server>_<tool>（如 mcp_stardew_stardew_get_state，
        因 server 名与工具名都含 'stardew'）。这里按后缀匹配，避免硬编码前缀拼错。
        """
        try:
            from core.tools import TOOL_REGISTRY
            for k in TOOL_REGISTRY:
                if k.startswith("mcp_") and k.endswith("get_state"):
                    return k
        except Exception:
            pass
        return None

    def _default_tool_call(self, tool_name: str, arguments: dict) -> str:
        # 惰性取 MCP manager（可能开关后才注册）
        try:
            from core.mcp_client import get_mcp_manager
            mgr = get_mcp_manager()
            return mgr.call(tool_name, arguments or {})
        except Exception as e:
            raise RuntimeError(f"MCP 调用不可用: {e}")

    def read_state(self) -> Optional[Dict]:
        """读取一次游戏状态；解析 JSON；失败返回 None。"""
        if self._tool_call is not None:
            # 注入的调用（测试/外部绑定）已自带目标，直接调用
            call = self._tool_call
            tool_name = None
        else:
            call = self._default_tool_call
            # 动态解析正确的工具名，避免硬编码拼错前缀
            tool_name = self._find_state_tool_name()
            if not tool_name:
                self.last_error = "未找到星露谷状态工具（扩展未启用）"
                return None
        try:
            raw = call(tool_name, {}) if tool_name else call("get_state", {})
        except Exception as e:
            self.last_error = f"调用失败: {e}"
            log_debug("Stardew", f"读取状态失败: {e}")
            return None
        # 归一化：可能直接 JSON 字符串，也可能包在节选文本里
        text = (raw or "").strip()
        # MCP 返回 "（MCP 工具错误）..." / "Error: ..." 时说明链路有问题
        if not text or text.startswith("Error") or "工具错误" in text:
            self.last_error = text[:160] or "无返回"
            return None
        try:
            # 尝试提取第一个 {...}
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end <= start:
                self.last_error = "无法解析状态(非 JSON)"
                return None
            import json
            data = json.loads(text[start:end + 1])
            self.last_error = None
            return data
        except Exception as e:
            self.last_error = f"解析失败: {e}"
            return None

    # ---------------- 变化检测 -> 记忆 ----------------

    @staticmethod
    def _sig(state: Dict) -> tuple:
        """从游戏状态里提取'用于变化检测的特征签名'。"""
        s = state or {}
        return (
            s.get("season"),
            s.get("day_of_month"),
            s.get("day_of_week"),
            s.get("weather"),
            s.get("location"),
            (s.get("player") or {}).get("money"),
        )

    def _narrative(self, state: Dict) -> str:
        """把当前游戏状态转成一条角色视角的紧凑描述（供记忆反写）。"""
        p = state.get("player") or {}
        parts = []
        season = state.get("season")
        dom = state.get("day_of_month")
        weather = state.get("weather")
        loc = state.get("location")
        if season and dom:
            season_cn = {"Spring": "春天", "Summer": "夏天", "Fall": "秋天", "Winter": "冬天"}.get(str(season), str(season))
            parts.append(f"{season_cn}{dom}日")
        if weather:
            w_cn = {"Sun": "晴天", "Rain": "下雨", "Snow": "下雪", "Storm": "暴风雨",
                    "Wind": "有风", "Windy": "有风"}.get(str(weather), str(weather))
            parts.append(f"天气{w_cn}")
        if loc:
            loc_cn = {
                "Farm": "农场", "Town": "镇上", "Mine": "矿井", "Mines": "矿井",
                "Beach": "海边", "Forest": "森林", "Mountain": "山区",
                "Woods": "森林", "BusStop": "公交站", "CommunityCenter": "社区中心",
                "SeedShop": "种子店", "Saloon": "酒吧", "Barn": "谷仓",
                "Coop": "鸡舍", "Greenhouse": "温室", "Desert": "沙漠",
                "Sewer": "下水道", "WitchSwamp": "女巫沼泽", "IslandWest": "姜岛",
                "VolcanoDungeon": "火山地牢",
            }.get(str(loc), str(loc))
            parts.append(f"在{loc_cn}")
        money = p.get("money")
        money = p.get("money")
        if money is not None:
            parts.append(f"金币{money}G")
        text = "、".join(parts)
        return f"今天的星露谷：{text}。" if text else "今天也在星露谷里忙活着。"

    def record_fingerprint(self) -> None:
        """读取当前状态，若与上次不同则沉淀一条记忆（合并每轮变化）。"""
        state = self.read_state()
        if state is None:
            return  # 没有状态（未开游戏/链路断）——静默跳过，不写记忆
        with self._lock:
            prev = self.last_state
            self.last_state = state
            self.last_success_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if prev is None:
                # 首次读到：视为刚感知到游戏进行中，记录一条
                changed = True
            else:
                changed = (self._sig(state) != self._sig(prev))
        if not changed:
            return
        if self.bridge is None:
            log_debug("Stardew", "记忆桥不可用，跳过反写")
            return
        try:
            narrative = self._narrative(state)
            self.bridge.record_event("default_user", narrative)
            self.recorded_events += 1
            self.last_recorded_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_info("Stardew", f"已记录游戏事件: {narrative}")
        except Exception as e:
            log_error("Stardew", f"记忆反写失败: {e}")

    # ---------------- 生命周期 ----------------

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="stardew-poller")
        self._thread.start()
        log_info("Stardew", f"星露谷状态轮询已启动（间隔 {self.interval:.0f}s）")

    def _loop(self):
        # 稍作延迟，等待 MCP/游戏就绪
        time.sleep(2.0)
        while not self._stop.is_set():
            try:
                self.record_fingerprint()
            except Exception as e:
                log_error("Stardew", f"轮询异常: {e}")
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 1)
        self._thread = None
        log_info("Stardew", "星露谷状态轮询已停止")

    # ---------------- 后台页面数据 ----------------

    def status(self) -> Dict:
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "interval": self.interval,
                "last_success_at": self.last_success_at,
                "last_error": self.last_error,
                "recorded_events": self.recorded_events,
                "last_recorded_at": self.last_recorded_at,
                "enabled": self.bridge is not None,
                "current_state": self.last_state,
            }
