"""模型客户端：进程级复用与显式关闭。

单次任务不应销毁共享连接，所以客户端生命周期与 Runtime（单次任务）分开管理。
"""
from functools import lru_cache

from openai import AsyncOpenAI

from .config import Settings

_CLIENTS: list[AsyncOpenAI] = []


def thinking_off(s: Settings) -> dict:
    """关掉 qwen3 的思考模式。

    实测校内端点：默认思考单次 22 秒且 800 token 全被推理吃掉、正文为空；
    加上这个开关后同一请求 1.6 秒返回正常正文。
    """
    if not s.llm_disable_thinking:
        return {}
    return {"chat_template_kwargs": {"enable_thinking": False}}


@lru_cache(maxsize=4)
def _build(base_url: str, api_key: str, timeout: float, retries: int) -> AsyncOpenAI:
    # ponytail: 缓存键只取连接相关四项；进程退出时由 aclose_client 统一关闭。
    client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout,
                         max_retries=retries)
    _CLIENTS.append(client)
    return client


def get_client(s: Settings) -> AsyncOpenAI:
    if not s.llm_base_url or not s.llm_api_key:
        raise ValueError("缺少模型本地配置")
    return _build(s.llm_base_url, s.llm_api_key, s.llm_request_timeout, s.llm_max_retries)


async def aclose_client() -> None:
    """进程关闭时调用：关闭全部缓存的客户端连接。"""
    while _CLIENTS:
        await _CLIENTS.pop().close()
    _build.cache_clear()


def reset_for_tests() -> None:
    """仅测试使用：丢弃缓存但不等待异步关闭。"""
    _CLIENTS.clear()
    _build.cache_clear()
