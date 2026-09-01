"""The long-lived collaborators a request needs, bundled.

Handlers would otherwise take seven or eight injected parameters each.
"""

from dataclasses import dataclass

from src.core.pacing import Pacer, PlatformPacer
from src.storage.cache import FileIdCache
from src.storage.stats import EventLog
from src.telegram.notify import Notifier


@dataclass(frozen=True)
class Services:
    cache: FileIdCache
    events: EventLog
    chat_pacer: Pacer
    platform_pacer: PlatformPacer
    notifier: Notifier
