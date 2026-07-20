import datetime
import enum
import ipaddress
import os
import re
import sys
import typing
import uuid
import zoneinfo
from base64 import encodebytes
from collections import ChainMap, Counter, OrderedDict, deque
from collections.abc import Callable, Collection, Mapping, Sequence, Set
from contextlib import suppress
from dataclasses import is_dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Any, ForwardRef, Tuple

import typing_extensions
from typing_extensions import NotRequired

from mashumaro.core.const import PY_311_MIN
from mashumaro.core.meta.code.lines import CodeLines
from mashumaro.core.meta.helpers import (
    get_args,
    get_class_that_defines_method,
    get_function_return_annotation,
    get_literal_values,
    get_type_origin,
    get_type_var_default,
    is_final,
    is_generic,
    is_literal,
    is_named_tuple,
    is_new_type,
    is_not_required,
    is_optional,
    is_readonly,
    is_required,
    is_self,
    is_special_typing_primitive,
    is_type_alias_type,
    is_type_var,
    is_type_var_any,
    is_type_var_tuple,
    is_typed_dict,
    is_union,
    is_unpack,
    not_none_type_arg,
    resolve_type_params,
    substitute_type_params,
    type_name,
    type_var_has_default,
)
from mashumaro.core.meta.types.common import (
    Expression,
    ExpressionWrapper,
    NoneType,
    Registry,
    ValueSpec,
    clean_id,
    ensure_generic_collection,
    ensure_generic_collection_subclass,
    ensure_generic_mapping,
    expr_or_maybe_none,
    random_hex,
)
from mashumaro.exceptions import (
    UnserializableDataError,
    UnserializableField,
    UnsupportedSerializationEngine,
)
from mashumaro.helper import pass_through
from mashumaro.types import (
    GenericSerializableType,
    SerializableType,
    SerializationStrategy,
)

if sys.version_info >= (3, 14):
    from typing import evaluate_forward_ref

    from annotationlib import get_annotations
else:
    from typing_extensions import evaluate_forward_ref, get_annotations

__all__ = ["PackerRegistry"]


PackerRegistry = Registry()
register = PackerRegistry.register


def _pack_with_annotated_serialization_strategy(
    spec: ValueSpec, strategy: SerializationStrategy
) -> Expression:
    pass


def get_overridden_serialization_method(
    spec: ValueSpec,
) -> Callable | str | ExpressionWrapper | None:
    pass


@register
def pack_type_with_overridden_serialization(
    spec: ValueSpec,
) -> Expression | None:
    pass


def _pack_annotated_serializable_type(spec: ValueSpec) -> Expression | None:
    pass


@register
def pack_serializable_type(spec: ValueSpec) -> Expression | None:
    pass


@register
def pack_generic_serializable_type(spec: ValueSpec) -> Expression | None:
    pass


@register
def pack_dataclass(spec: ValueSpec) -> Expression | None:
    pass


@register
def pack_final(spec: ValueSpec) -> Expression | None:
    pass


@register
def pack_any(spec: ValueSpec) -> Expression | None:
    pass


def pack_union(
    spec: ValueSpec, args: tuple[type, ...], prefix: str = "union"
) -> Expression:
    pass


def pack_literal(spec: ValueSpec) -> Expression:
    pass


@register
def pack_special_typing_primitive(spec: ValueSpec) -> Expression | None:
    pass


@register
def pack_number_and_bool_and_none(spec: ValueSpec) -> Expression | None:
    pass


@register
def pack_date_objects(spec: ValueSpec) -> Expression | None:
    pass


@register
def pack_timedelta(spec: ValueSpec) -> Expression | None:
    pass


@register
def pack_timezone(spec: ValueSpec) -> Expression | None:
    pass


@register
def pack_zone_info(spec: ValueSpec) -> Expression | None:
    pass


@register
def pack_uuid(spec: ValueSpec) -> Expression | None:
    pass


@register
def pack_ipaddress(spec: ValueSpec) -> Expression | None:
    pass


@register
def pack_decimal(spec: ValueSpec) -> Expression | None:
    pass


@register
def pack_fraction(spec: ValueSpec) -> Expression | None:
    pass


def pack_tuple(spec: ValueSpec, args: tuple[type, ...]) -> Expression:
    if not args:
        if spec.type in (Tuple, tuple):
            args = [Any, ...]  # type: ignore
        else:
            return "[]"
    elif len(args) == 1 and args[0] == ():
        if not PY_311_MIN:
            return "[]"
    if len(args) == 2 and args[1] is Ellipsis:
        packer = PackerRegistry.get(
            spec.copy(type=args[0], expression="value", could_be_none=True)
        )
        return f"[{packer} for value in {spec.expression}]"
    else:
        arg_indexes: list[int | tuple[int, int | None]] = []
        unpack_idx: int | None = None
        for arg_idx, type_arg in enumerate(args):
            if is_unpack(type_arg):
                if unpack_idx is not None:
                    raise TypeError(
                        "Multiple unpacks are disallowed within a single type "
                        f"parameter list for {type_name(spec.type)}"
                    )
                unpack_idx = arg_idx
                if len(args) == 1:
                    arg_indexes.append((arg_idx, None))
                elif arg_idx < len(args) - 1:
                    arg_indexes.append((arg_idx, arg_idx + 1 - len(args)))
                else:
                    arg_indexes.append((arg_idx, None))
            else:
                if unpack_idx is None:
                    arg_indexes.append(arg_idx)
                else:
                    arg_indexes.append(arg_idx - len(args))
        packers: list[Expression] = []
        for _idx, _arg_idx in enumerate(arg_indexes):
            if isinstance(_arg_idx, tuple):
                p_expr = f"{spec.expression}[{_arg_idx[0]}:{_arg_idx[1]}]"
            else:
                p_expr = f"{spec.expression}[{_arg_idx}]"
            packer = PackerRegistry.get(
                spec.copy(
                    type=args[_idx], expression=p_expr, could_be_none=True
                )
            )
            if packer != "*[]":
                packers.append(packer)
        return f"[{', '.join(packers)}]"


def pack_named_tuple(spec: ValueSpec) -> Expression:
    pass


def pack_typed_dict(spec: ValueSpec) -> Expression:
    pass


@register
def pack_collection(spec: ValueSpec) -> Expression | None:
    pass


@register
def pack_pathlike(spec: ValueSpec) -> Expression | None:
    pass


@register
def pack_enum(spec: ValueSpec) -> Expression | None:
    pass


@register
def pack_pattern(spec: ValueSpec) -> Expression | None:
    pass
