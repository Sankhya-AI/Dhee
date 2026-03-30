"""Scoped namespace wrapper around Bus."""

from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from engram_bus.bus import Bus


class Workspace:
    """All operations scoped to a single namespace."""

    def __init__(self, bus: "Bus", name: str) -> None:
        self._bus = bus
        self._namespace = name

    @property
    def name(self) -> str:
        return self._namespace

    def put(self, key: str, value: Any, **kwargs: Any) -> None:
        self._bus.put(key, value, namespace=self._namespace, **kwargs)

    def get(self, key: str) -> Optional[Any]:
        return self._bus.get(key, namespace=self._namespace)

    def delete(self, key: str) -> bool:
        return self._bus.delete(key, namespace=self._namespace)

    def keys(self, **kwargs: Any) -> List[str]:
        return self._bus.keys(namespace=self._namespace, **kwargs)

    def all(self) -> Dict[str, Any]:
        return self._bus.all(namespace=self._namespace)

    def clear(self) -> int:
        return self._bus.clear(namespace=self._namespace)

    def publish(self, topic: str, data: Any, **kwargs: Any) -> int:
        return self._bus.publish(topic, data, **kwargs)

    def subscribe(self, topic: str, callback: Callable, **kwargs: Any) -> None:
        self._bus.subscribe(topic, callback, **kwargs)
