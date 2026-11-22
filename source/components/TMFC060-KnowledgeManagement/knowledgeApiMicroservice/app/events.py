"""Event publishing. #37 replaces the recorder with delivery to the /hub subscribers."""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger(__name__)


@dataclass
class Event:
    event_type: str  # for example KnowledgeSpecificationCreateEvent
    resource_name: str  # payload key, for example knowledgeSpecification
    resource: dict
    time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class EventRecorder:
    """Keeps published events in memory (for tests and until hub delivery exists)."""

    def __init__(self):
        self.events: list[Event] = []

    async def publish(self, event: Event) -> None:
        log.debug("event %s %s", event.event_type, event.resource.get("id"))
        self.events.append(event)

    def types(self) -> list[str]:
        return [e.event_type for e in self.events]
