from app.core.config import settings
from app.sources.base import SourceAdapter
from app.sources.promoters import FourceAdapter, KnockoutAdapter
from app.sources.stub import StubSourceAdapter
from app.sources.ticketmaster import TicketmasterAdapter


def build_adapters() -> dict[str, SourceAdapter]:
    return {
        "ticketmaster": TicketmasterAdapter(enabled=settings.source_ticketmaster_enabled),
        "knockout": KnockoutAdapter(enabled=settings.source_knockout_enabled),
        "fource": FourceAdapter(enabled=settings.source_fource_enabled),
        "ebilet": StubSourceAdapter("ebilet", enabled=settings.source_ebilet_enabled),
        "eventim": StubSourceAdapter("eventim", enabled=settings.source_eventim_enabled),
        "going": StubSourceAdapter("going", enabled=settings.source_going_enabled),
        "livenation": StubSourceAdapter("livenation", enabled=settings.source_livenation_enabled),
    }
