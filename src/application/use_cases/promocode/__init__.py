from typing import Final

from src.application.common import Interactor

from .commands.activate import ActivatePromocode
from .commands.manage import CreatePromocode, DeletePromocode, UpdatePromocode
from .queries.get import GetPromocode, GetPromocodeList
from .queries.validate import ValidatePromocode

PROMOCODE_USE_CASES: Final[tuple[type[Interactor], ...]] = (
    ValidatePromocode,
    GetPromocode,
    GetPromocodeList,
    CreatePromocode,
    UpdatePromocode,
    DeletePromocode,
    ActivatePromocode,
)
