import threading
from typing import Callable, Dict, List

class EventBus:
    """
    A lightweight, in-memory event bus to decouple Django views from Agent execution.
    In Phase 4/5, this will be swapped out for Google Cloud Pub/Sub or Celery/Redis.
    """
    _subscribers: Dict[str, List[Callable]] = {}
    _lock = threading.Lock()

    @classmethod
    def subscribe(cls, event_type: str, handler: Callable):
        """Registers a listener for a specific event type."""
        with cls._lock:
            if event_type not in cls._subscribers:
                cls._subscribers[event_type] = []
            if handler not in cls._subscribers[event_type]:
                cls._subscribers[event_type].append(handler)

    @classmethod
    def publish(cls, event_type: str, payload: dict):
        """
        Publishes an event asynchronously.
        This immediately frees up the caller (e.g., the Django HTTP thread).
        """
        print(f"📡 [EventBus] Publishing event: {event_type}")
        with cls._lock:
            handlers = cls._subscribers.get(event_type, []).copy()

        for handler in handlers:
            # Fire-and-forget in a background thread
            thread = threading.Thread(target=handler, args=(payload,), daemon=True)
            thread.start()