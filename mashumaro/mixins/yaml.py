from collections.abc import Callable
from typing import Any, Type, TypeVar

import yaml

from mashumaro.mixins.dict import DataClassDictMixin

T = TypeVar("T", bound="DataClassYAMLMixin")


EncodedData = str | bytes
Encoder = Callable[[Any], EncodedData]
Decoder = Callable[[EncodedData], dict[Any, Any]]


DefaultLoader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
DefaultDumper = getattr(yaml, "CDumper", yaml.Dumper)


def default_encoder(data: Any) -> EncodedData:
    pass


def default_decoder(data: EncodedData) -> dict[Any, Any]:
    pass


class DataClassYAMLMixin(DataClassDictMixin):
    __slots__ = ()

    def to_yaml(
        self: T, encoder: Encoder = default_encoder, **to_dict_kwargs: Any
    ) -> EncodedData:
        pass

    @classmethod
    def from_yaml(
        cls: Type[T],
        data: EncodedData,
        decoder: Decoder = default_decoder,
        **from_dict_kwargs: Any,
    ) -> T:
        pass
