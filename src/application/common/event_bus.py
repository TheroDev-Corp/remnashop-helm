from typing import Any, Callable, Protocol, runtime_checkable

from src.application.events import BaseEvent


@runtime_checkable
class EventPublisher(Protocol):
    async def publish(self, event: BaseEvent) -> None: ...


@runtime_checkable
class EventSubscriber(Protocol):
    def autodiscover(self) -> None: ...

    async def shutdown(self) -> None: ...

    def set_container_factory(self, factory: Callable[[], Any]) -> None: ...
