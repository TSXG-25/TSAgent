from typing import Dict, List, Callable, Any
import asyncio

class EventBus:
    def __init__(self):
        self._subscribers = {}

    def subscribe(self, event_type, callback):
        self._subscribers.setdefault(event_type, []).append(callback)

    def emit(self, event_type, data):
        for cb in self._subscribers.get(event_type, []):
            if asyncio.iscoroutinefunction(cb):
                asyncio.create_task(cb(data))
            else:
                cb(data)

event_bus = EventBus()