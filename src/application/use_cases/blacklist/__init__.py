from typing import Final

from src.application.common import Interactor

from .commands.sources import AddBlacklistSource, RemoveBlacklistSource, SyncBlacklistSources
from .queries.fetch import FetchBlacklistIds, ParseBlacklistIds
from .queries.sources import GetBlacklistSources

BLACKLIST_USE_CASES: Final[tuple[type[Interactor], ...]] = (
    AddBlacklistSource,
    RemoveBlacklistSource,
    SyncBlacklistSources,
    GetBlacklistSources,
    FetchBlacklistIds,
    ParseBlacklistIds,
)

__all__ = [
    "AddBlacklistSource",
    "FetchBlacklistIds",
    "ParseBlacklistIds",
    "RemoveBlacklistSource",
    "SyncBlacklistSources",
    "GetBlacklistSources",
    "BLACKLIST_USE_CASES",
]
