from typing import Final

from src.application.common import Interactor

from .commands.manage import CreateAdLink, DeleteAdLink, UpdateAdLink
from .queries.generate import GenerateAdLinkCode
from .queries.list import GetAdLinks
from .queries.stats import GetAdLinkStats
from .queries.validate import ValidateAdLinkCode

AD_LINK_USE_CASES: Final[tuple[type[Interactor], ...]] = (
    ValidateAdLinkCode,
    GenerateAdLinkCode,
    GetAdLinks,
    GetAdLinkStats,
    CreateAdLink,
    UpdateAdLink,
    DeleteAdLink,
)
