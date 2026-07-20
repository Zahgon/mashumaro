import re
from collections.abc import Callable
from typing import Any, Type

from mashumaro.core.meta.code.builder import CodeBuilder
from mashumaro.core.meta.helpers import is_optional, is_type_var_any
from mashumaro.core.meta.types.common import (
    AttrsHolder,
    FieldContext,
    ValueSpec,
)
from mashumaro.core.meta.types.pack import PackerRegistry
from mashumaro.core.meta.types.unpack import UnpackerRegistry

CALL_EXPR = re.compile(r"^([^ ]+)\(value\)$")


class CodecCodeBuilder(CodeBuilder):
    @classmethod
    def new(cls, **kwargs: Any) -> "CodecCodeBuilder":
        pass

    def add_decode_method(
        self,
        shape_type: Type,
        decoder_obj: Any,
        pre_decoder_func: Callable[[Any], Any] | None = None,
    ) -> None:
        pass

    def add_encode_method(
        self,
        shape_type: Type,
        encoder_obj: Any,
        post_encoder_func: Callable[[Any], Any] | None = None,
    ) -> None:
        pass
