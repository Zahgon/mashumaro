from collections.abc import Callable, Sequence
from types import new_class
from typing import Any, Type, cast

from typing_extensions import Literal

from mashumaro.core.const import Sentinel
from mashumaro.types import SerializationStrategy

__all__ = ["Dialect"]


SerializationStrategyValueType = (
    SerializationStrategy | dict[str, str | Callable]
)


class Dialect:
    serialization_strategy: dict[Any, SerializationStrategyValueType] = {}
    serialize_by_alias: bool | Literal[Sentinel.MISSING] = Sentinel.MISSING
    namedtuple_as_dict: bool | Literal[Sentinel.MISSING] = Sentinel.MISSING
    omit_none: bool | Literal[Sentinel.MISSING] = Sentinel.MISSING
    omit_default: bool | Literal[Sentinel.MISSING] = Sentinel.MISSING
    no_copy_collections: Sequence[Any] | Literal[Sentinel.MISSING] = (
        Sentinel.MISSING
    )

    @classmethod
    def merge(cls, other: Type["Dialect"]) -> Type["Dialect"]:
        pass
