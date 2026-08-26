"""
LLM 提供商管理器

统一管理 LLM 后端（本地 Ollama / 远程 OpenAI 兼容接口），提供：
- build_adapter         按当前配置构造 llm_adapter / tool_adapter
- test_connection       测试某配置的连通性（返回模型名/耗时/错误）
- switch_config         切换 provider 并持久化到 .env + 重建运行期依赖

远程兼容：任一 OpenAI 兼容接口（DeepSeek / 百度千帆 / OpenAI 等），只需 base_url + api_key + model。
"""
import threading
from typing import Dict, Optional, Tuple

from core import config as cfg
from core.adapters import OllamaAdapter, OpenAICompatAdapter, LLMAdapter
from core.logger import log_info, log_error


class LLMManager:
    """LLM 提供商管理"""

    def __init__(self, initializer=None):
        self._initializer = initializer
        self._lock = threading.Lock()

    def bind_initializer(self, initializer):
        self._initializer = initializer

    # ---------- 读取当前配置 ----------
    @staticmethod
    def get_config() -> dict:
        import os
        g = os.environ.get
        api_key = g('LLM_API_KEY', '') or ''
        return {
            'provider': g('LLM_PROVIDER', 'ollama').strip().lower(),
            'ollama_host': g('OLLAMA_HOST', 'http://localhost:11434'),
            'llm_model': g('LLM_MODEL', 'qwen3.5:9b'),
            'tool_llm_model': g('TOOL_LLM_MODEL', 'qwen2.5:7b'),
            'api_base_url': g('LLM_API_BASE_URL', ''),
            'api_key_masked': ('***' + api_key[-4:]) if api_key else '',
            'remote_model': g('LLM_REMOTE_MODEL', ''),
            'remote_tool_model': g('LLM_REMOTE_TOOL_MODEL', ''),
            'temperature': LLMManager._temperature(),
        }

    # ---------- 构造适配器 ----------
    @staticmethod
    def _effective_models() -> Tuple[str, str]:
        # 从 os.environ 动态读，确保后台切换后立即生效（不依赖模块导入时的快照）
        import os
        pv = os.environ.get("LLM_PROVIDER", "ollama").strip().lower()
        ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        llm_model = os.environ.get("LLM_MODEL", "qwen3.5:9b")
        tool_llm = os.environ.get("TOOL_LLM_MODEL", "qwen2.5:7b")
        remote_model = os.environ.get("LLM_REMOTE_MODEL", "")
        remote_tool = os.environ.get("LLM_REMOTE_TOOL_MODEL", "")
        if pv == 'openai':
            main = remote_model or llm_model
            tool = remote_tool or main
        else:
            main, tool = llm_model, tool_llm
        return main, tool

    @staticmethod
    def _temperature() -> float:
        import os
        raw = os.environ.get("LLM_TEMPERATURE", "0.85") or "0.85"
        try:
            # python-dotenv set_key 可能给值加引号（如 '0.9'），容忍
            return float(str(raw).strip().strip("'\""))
        except (TypeError, ValueError):
            return 0.85

    def build_adapter(self, role: str = 'llm') -> LLMAdapter:
        main, tool = self._effective_models()
        model = main if role == 'llm' else tool
        import os
        provider = os.environ.get('LLM_PROVIDER', 'ollama').strip().lower()
        if provider == 'openai':
            api_key = os.environ.get('LLM_API_KEY', '')
            base_url = os.environ.get('LLM_API_BASE_URL', '')
            if not api_key or not base_url:
                raise RuntimeError('远程 LLM 未配置完整：需要 LLM_API_KEY 和 LLM_API_BASE_URL')
            return OpenAICompatAdapter(api_key=api_key, model=model, base_url=base_url)
        host = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
        return OllamaAdapter(model=model, host=host)

    def build_llm_adapter(self) -> LLMAdapter:
        a = self.build_adapter('llm')
        if hasattr(a, 'set_temperature'):
            a.set_temperature(self._temperature())
        return a

    def build_tool_adapter(self) -> LLMAdapter:
        return self.build_adapter('tool')

    # ---------- 连通性测试 ----------
    @staticmethod
    def test_connection() -> dict:
        import time
        main, _ = LLMManager._effective_models()
        try:
            adapter = LLMManager().build_llm_adapter()
        except Exception as e:
            return {'ok': False, 'error': f'配置问题: {e}'}
        t0 = time.time()
        try:
            text = adapter.chat([{'role': 'user', 'content': '回复一个字即可：好'}])
            latency = int((time.time() - t0) * 1000)
            import os
            provider = os.environ.get('LLM_PROVIDER', 'ollama').strip().lower()
            if not text or str(text).startswith('['):
                return {'ok': False, 'provider': provider, 'model': main,
                        'latency_ms': latency, 'error': text or '空响应'}
            return {'ok': True, 'provider': provider, 'model': main,
                    'latency_ms': latency, 'latency_s': round(latency / 1000, 2), 'reply': text[:50]}
        except Exception as e:
            return {'ok': False, 'provider': cfg.LLM_PROVIDER, 'model': main, 'error': str(e)[:200]}

    # ---------- 切换配置 ----------
    def switch_config(self, updates: dict) -> dict:
        allowed = {
            'provider', 'ollama_host', 'llm_model', 'tool_llm_model',
            'api_base_url', 'api_key', 'remote_model', 'remote_tool_model', 'temperature',
        }
        applied = {}
        errors = []
        with self._lock:
            for k, v in updates.items():
                if k not in allowed:
                    continue
                key = self._map_key(k)
                if key is None:
                    continue
                if k == 'provider':
                    v = str(v).strip().lower()
                    if v not in ('ollama', 'openai'):
                        errors.append('provider 仅支持 ollama/openai')
                        continue
                if self._persist(key, str(v)):
                    applied[key] = str(v)
            rebuild_error = self._rebuild_runtime()
            if rebuild_error:
                errors.append('重建失败: ' + rebuild_error)
            return {'applied': applied, 'errors': errors}

    @staticmethod
    def _map_key(k: str) -> Optional[str]:
        m = {
            'provider': 'LLM_PROVIDER',
            'ollama_host': 'OLLAMA_HOST',
            'llm_model': 'LLM_MODEL',
            'tool_llm_model': 'TOOL_LLM_MODEL',
            'api_base_url': 'LLM_API_BASE_URL',
            'api_key': 'LLM_API_KEY',
            'remote_model': 'LLM_REMOTE_MODEL',
            'remote_tool_model': 'LLM_REMOTE_TOOL_MODEL',
            'temperature': 'LLM_TEMPERATURE',
        }
        return m.get(k)

    @staticmethod
    def _persist(env_key: str, value: str) -> bool:
        try:
            from pathlib import Path
            from dotenv import set_key
            import os
            env_path = Path(cfg.PROJECT_ROOT) / '.env'
            set_key(str(env_path), env_key, value)
            os.environ[env_key] = value
            return True
        except Exception as e:
            log_error('LLM', f'持久化 {env_key} 失败: {e}')
            return False

    def _rebuild_runtime(self) -> Optional[str]:
        if self._initializer is None:
            return 'initializer 未绑定'
        try:
            ini = self._initializer
            ini.llm_adapter = self.build_llm_adapter()
            ini.tool_adapter = self.build_tool_adapter()
            ini.memory_manager.tool_adapter = ini.tool_adapter
            ini.agent_factory._cache.clear()
            import os
            log_info('LLM', f'已切换 provider={os.environ.get("LLM_PROVIDER", "ollama")} 并重建运行期依赖')
            return None
        except Exception as e:
            log_error('LLM', f'重建运行期失败: {e}')
            return str(e)
