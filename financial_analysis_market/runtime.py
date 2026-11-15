from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable


EventListener = Callable[[dict[str, Any]], None]


class Context:
    """精简的服务注册与事件中心，对应参考工程的插件组合方式。"""

    def __init__(self) -> None:
        self._services: dict[str, Any] = {}
        self._listeners: dict[str, list[EventListener]] = defaultdict(list)

    def register(self, name: str, service: Any) -> None:
        if name in self._services:
            raise ValueError(f"服务已注册: {name}")
        self._services[name] = service

    def get(self, name: str) -> Any:
        try:
            return self._services[name]
        except KeyError as exc:
            raise RuntimeError(f"未注册服务: {name}") from exc

    def on(self, event: str, listener: EventListener) -> None:
        self._listeners[event].append(listener)

    def emit(self, event: str, payload: dict[str, Any]) -> None:
        for listener in self._listeners[event]:
            listener(payload)
