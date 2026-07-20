import collections
import collections.abc
import datetime
import enum
import ipaddress
import os
import pathlib
import re
import sys
import types
import typing
import uuid
import zoneinfo
from abc import ABC
from base64 import decodebytes
from collections.abc import (
    Callable,
    Collection,
    Iterable,
    Mapping,
    Sequence,
    Set,
)
from contextlib import suppress
from dataclasses import is_dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Any, ForwardRef, Tuple

import typing_extensions
from typing_extensions import NotRequired

from mashumaro.core.const import PY_311_MIN
from mashumaro.core.helpers import parse_timezone
from mashumaro.core.meta.code.lines import CodeLines
from mashumaro.core.meta.helpers import (
    get_args,
    get_class_that_defines_method,
    get_function_arg_annotation,
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
    iter_all_subclasses,
    not_none_type_arg,
    resolve_type_params,
    substitute_type_params,
    type_name,
    type_var_has_default,
)
from mashumaro.core.meta.types.common import (
    AbstractMethodBuilder,
    AttrsHolder,
    Expression,
    ExpressionWrapper,
    NoneType,
    Registry,
    TypeMatchEligibleExpression,
    ValueSpec,
    clean_id,
    ensure_generic_collection,
    ensure_generic_collection_subclass,
    ensure_generic_mapping,
    expr_or_maybe_none,
    random_hex,
)
from mashumaro.exceptions import (
    ThirdPartyModuleNotFoundError,
    UnserializableDataError,
    UnserializableField,
    UnsupportedDeserializationEngine,
)
from mashumaro.helper import pass_through
from mashumaro.types import (
    Discriminator,
    GenericSerializableType,
    SerializableType,
    SerializationStrategy,
)

if sys.version_info >= (3, 14):
    from typing import evaluate_forward_ref

    from annotationlib import get_annotations
else:
    from typing_extensions import evaluate_forward_ref, get_annotations

try:
    import ciso8601
except ImportError:  # pragma: no cover
    ciso8601: types.ModuleType | None = None  # type: ignore
try:
    import pendulum
except ImportError:  # pragma: no cover
    pendulum: types.ModuleType | None = None  # type: ignore


__all__ = ["UnpackerRegistry", "SubtypeUnpackerBuilder"]


UnpackerRegistry = Registry()
register = UnpackerRegistry.register


class AbstractUnpackerBuilder(AbstractMethodBuilder, ABC):
    def _generate_method_name(self, spec: ValueSpec) -> str:
        prefix = self.get_method_prefix()
        if prefix:
            prefix = f"{prefix}_"
        if spec.field_ctx.name:
            suffix = f"_{spec.field_ctx.name}"
        else:
            suffix = ""
        return (
            f"__unpack_{prefix}{spec.builder.cls.__name__}{suffix}"
            f"__{random_hex()}"
        )

    def _add_definition(self, spec: ValueSpec, lines: CodeLines) -> str:
        method_name = self._generate_method_name(spec)
        method_args = self._generate_method_args(spec)
        if spec.builder.is_nailed:
            lines.append("@classmethod")
        lines.append(f"def {method_name}({method_args}):")
        return method_name

    def _get_extra_method_args(self) -> list[str]:
        return []

    def _generate_method_args(self, spec: ValueSpec) -> str:
        default_kwargs = spec.builder.get_unpack_method_default_flag_values()
        extra_args = self._get_extra_method_args()
        if extra_args:
            extra_args_str = f", {', '.join(extra_args)}"
        else:
            extra_args_str = ""
        if spec.builder.is_nailed:
            first_args = "cls, value"
        else:
            first_args = "value"
        if default_kwargs:
            return f"{first_args}{extra_args_str}, {default_kwargs}"
        else:  # pragma: no cover
            return f"{first_args}{extra_args_str}"

    def _get_call_expr(self, spec: ValueSpec, method_name: str) -> str:
        method_args = ", ".join(
            filter(
                None, (spec.expression, spec.builder.get_unpack_method_flags())
            )
        )
        return f"{spec.cls_attrs_name}.{method_name}({method_args})"


class UnionUnpackerBuilder(AbstractUnpackerBuilder):
    def __init__(self, args: tuple[type, ...]):
        self.union_args = args
        self.method_name: str | None = None

    def get_method_prefix(self) -> str:
        return "union"

    def _generate_method_name(self, spec: ValueSpec) -> str:
        method_name = super()._generate_method_name(spec)
        self.method_name = method_name
        return method_name

    def _add_body(self, spec: ValueSpec, lines: CodeLines) -> None:
        if not spec.field_ctx.unpacker and self.method_name:
            spec.field_ctx.unpacker = self._get_call_expr(
                spec, self.method_name
            )
        orig_lines = lines
        lines = CodeLines()
        unpackers = set()
        fallback_unpackers = []
        type_arg_unpackers = []
        type_match_statements = 0
        for type_arg in self.union_args:
            unpacker = UnpackerRegistry.get(
                spec.copy(type=type_arg, expression="value", owner=spec.type)
            )
            type_arg_unpackers.append((type_arg, unpacker))
            if isinstance(unpacker, TypeMatchEligibleExpression):
                type_match_statements += 1
        for type_arg, unpacker in type_arg_unpackers:
            condition = ""
            do_try = unpacker != "value"
            unpacker_block = CodeLines()
            if isinstance(unpacker, TypeMatchEligibleExpression):
                do_try = False
                if type_match_statements > 1:
                    condition = f"__value_type is {type_arg.__name__}"
                else:
                    condition = f"type(value) is {type_arg.__name__}"
                if (condition, unpacker) in unpackers:  # pragma: no cover
                    continue
                with unpacker_block.indent(f"if {condition}:"):
                    unpacker_block.append("return value")
                if (condition, unpacker) not in unpackers:
                    fallback_unpackers.append(unpacker)
            elif (condition, unpacker) in unpackers:
                continue
            else:
                unpacker_block.append(f"return {unpacker}")

            if do_try:
                with lines.indent("try:"):
                    lines.extend(unpacker_block)
                lines.append("except Exception: pass")
            else:
                lines.extend(unpacker_block)
            unpackers.add((condition, unpacker))
        for fallback_unpacker in fallback_unpackers:
            with lines.indent("try:"):
                lines.append(f"return {fallback_unpacker}")
            lines.append("except Exception: pass")
        field_type = spec.builder.get_type_name_identifier(
            typ=spec.type,
            resolved_type_params=spec.builder.get_field_resolved_type_params(
                spec.field_ctx.name
            ),
        )
        if spec.builder.is_nailed:
            lines.append(
                "raise InvalidFieldValue("
                f"'{spec.field_ctx.name}',{field_type},value,cls)"
            )
        else:
            lines.append("raise ValueError(value)")
        if type_match_statements > 1:
            orig_lines.append("__value_type = type(value)")
        orig_lines.extend(lines)

    def _get_existing_method(self, spec: ValueSpec) -> str | None:
        if spec.owner is spec.type:
            return spec.field_ctx.unpacker


class TypeVarUnpackerBuilder(UnionUnpackerBuilder):
    def get_method_prefix(self) -> str:
        return "type_var"


class LiteralUnpackerBuilder(AbstractUnpackerBuilder):
    def _before_build(self, spec: ValueSpec) -> None:
        spec.builder.add_type_modules(spec.type)

    def get_method_prefix(self) -> str:
        return "literal"

    def _add_body(self, spec: ValueSpec, lines: CodeLines) -> None:
        for literal_value in get_literal_values(spec.type):
            if isinstance(literal_value, enum.Enum):
                lit_type = type(literal_value)
                enum_type_name = spec.builder.get_type_name_identifier(
                    lit_type
                )
                with lines.indent(
                    f"if value == {enum_type_name}.{literal_value.name}.value:"
                ):
                    lines.append(
                        f"return {enum_type_name}.{literal_value.name}"
                    )
            elif isinstance(literal_value, bytes):
                unpacker = UnpackerRegistry.get(
                    spec.copy(type=bytes, expression="value")
                )
                with lines.indent("try:"):
                    with lines.indent(f"if {unpacker} == {literal_value!r}:"):
                        lines.append(f"return {literal_value!r}")
                lines.append("except Exception: pass")
            elif isinstance(
                literal_value, (int, str, bool, NoneType)  # type: ignore
            ):
                with lines.indent(f"if value == {literal_value!r}:"):
                    lines.append(f"return {literal_value!r}")
        lines.append("raise ValueError(value)")


class DiscriminatedUnionUnpackerBuilder(AbstractUnpackerBuilder):
    def __init__(
        self,
        discriminator: Discriminator,
        base_variants: tuple[type, ...] | None = None,
    ):
        self.discriminator = discriminator
        self.base_variants = base_variants or tuple()
        self._variants_attr: str | None = None

    def get_method_prefix(self) -> str:
        return ""

    def _get_extra_method_args(self) -> list[str]:
        return ["_dialect", "_default_dialect"]

    def _get_variants_attr(self, spec: ValueSpec) -> str:
        if self._variants_attr is None:
            self._variants_attr = (
                f"__mashumaro_{spec.field_ctx.name}_variants_{random_hex()}__"
            )
        return self._variants_attr

    def _get_variants_map(self, spec: ValueSpec) -> str:
        variants_attr = self._get_variants_attr(spec)
        if spec.builder.is_nailed:
            typ_name = spec.builder.get_type_name_identifier(spec.builder.cls)
            return f"{typ_name}.{variants_attr}"
        else:
            return f"{spec.cls_attrs_name}.{variants_attr}"

    def _get_variant_names(self, spec: ValueSpec) -> list[str]:
        base_variants = self.base_variants or (spec.origin_type,)
        variant_names: list[str] = []
        if self.discriminator.include_subtypes:
            spec.builder.ensure_object_imported(iter_all_subclasses)
            variant_names.extend(
                f"*iter_all_subclasses("
                f"{spec.builder.get_type_name_identifier(base_variant)})"
                for base_variant in base_variants
            )
        if self.discriminator.include_supertypes:
            variant_names.extend(
                map(spec.builder.get_type_name_identifier, base_variants)
            )
        return variant_names

    def _get_variant_names_iterable(self, spec: ValueSpec) -> str:
        variant_names = self._get_variant_names(spec)
        if len(variant_names) == 1:
            if variant_names[0].startswith("*"):
                return variant_names[0][1:]
            else:
                return f"[{variant_names[0]}]"
        return f'({", ".join(variant_names)})'

    @staticmethod
    def _get_variants_attr_holder(spec: ValueSpec) -> type:
        return spec.attrs

    @staticmethod
    def _get_variant_method_call(method_name: str, spec: ValueSpec) -> str:
        method_flags = spec.builder.get_unpack_method_flags()
        if method_flags:
            return f"{method_name}(value, {method_flags})"
        else:
            return f"{method_name}(value)"

    def _add_body(self, spec: ValueSpec, lines: CodeLines) -> None:
        discriminator = self.discriminator

        variants_attr = self._get_variants_attr(spec)
        variants_map = self._get_variants_map(spec)
        variants_attr_holder = self._get_variants_attr_holder(spec)
        variants = self._get_variant_names_iterable(spec)
        variants_type_expr = spec.builder.get_type_name_identifier(spec.type)

        if variants_attr not in variants_attr_holder.__dict__:
            setattr(variants_attr_holder, variants_attr, {})
        variant_method_name = spec.builder.get_unpack_method_name(
            format_name=spec.builder.format_name
        )
        variant_method_call = self._get_variant_method_call(
            variant_method_name, spec
        )
        if discriminator.variant_tagger_fn:
            spec.builder.ensure_object_imported(
                discriminator.variant_tagger_fn, "variant_tagger_fn"
            )
            variant_tagger_expr = "variant_tagger_fn(variant)"
        else:
            variant_tagger_expr = f"variant.__dict__['{discriminator.field}']"

        if spec.builder.dialect:
            spec.builder.ensure_object_imported(
                spec.builder.dialect, clean_id(type_name(spec.builder.dialect))
            )
        if spec.builder.default_dialect:
            spec.builder.ensure_object_imported(
                spec.builder.default_dialect,
                clean_id(type_name(spec.builder.default_dialect)),
            )

        if discriminator.field:
            chosen_cls = f"{variants_map}[discriminator]"
            with lines.indent("try:"):
                lines.append(f"discriminator = value['{discriminator.field}']")
            with lines.indent("except KeyError:"):
                lines.append(
                    f"raise MissingDiscriminatorError('{discriminator.field}')"
                    " from None"
                )
            with lines.indent("try:"):
                if spec.builder.is_nailed:
                    lines.append(f"return {chosen_cls}.{variant_method_call}")
                else:
                    lines.append(
                        f"return {spec.attrs_registry_name}"
                        f"[{chosen_cls}].{variant_method_call}"
                    )
            with lines.indent("except (KeyError, AttributeError):"):
                lines.append(f"variants_map = {variants_map}")
                with lines.indent(f"for variant in {variants}:"):
                    if discriminator.variant_tagger_fn is not None:
                        self._add_register_variant_tags(
                            lines, variant_tagger_expr
                        )
                    else:
                        with lines.indent("try:"):
                            self._add_register_variant_tags(
                                lines, variant_tagger_expr
                            )
                        with lines.indent("except KeyError:"):
                            lines.append("continue")
                    self._add_build_variant_unpacker(
                        spec, lines, variant_method_name, variant_method_call
                    )
                with lines.indent("try:"):
                    if spec.builder.is_nailed:
                        lines.append(
                            "return variants_map[discriminator]"
                            f".{variant_method_call}"
                        )
                    else:
                        lines.append(
                            f"return {spec.attrs_registry_name}["
                            "variants_map[discriminator]]"
                            f".{variant_method_call}"
                        )
                with lines.indent("except KeyError:"):
                    lines.append(
                        "raise SuitableVariantNotFoundError("
                        f"{variants_type_expr}, '{discriminator.field}', "
                        "discriminator) from None"
                    )
        else:
            with lines.indent(f"for variant in {variants}:"):
                with lines.indent("try:"):
                    if spec.builder.is_nailed:
                        lines.append(f"return variant.{variant_method_call}")
                    else:
                        lines.append(
                            f"return {spec.attrs_registry_name}"
                            f"[variant].{variant_method_call}"
                        )
                if spec.builder.is_nailed:
                    exc_to_catch = "AttributeError"
                else:
                    exc_to_catch = "(KeyError, AttributeError)"
                with lines.indent(f"except {exc_to_catch}:"):
                    self._add_build_variant_unpacker(
                        spec, lines, variant_method_name, variant_method_call
                    )
                lines.append("except Exception: pass")
            lines.append(
                f"raise SuitableVariantNotFoundError({variants_type_expr}) "
                "from None"
            )

    def _get_call_expr(self, spec: ValueSpec, method_name: str) -> str:
        method_args = ", ".join(
            filter(
                None,
                (
                    spec.expression,
                    clean_id(type_name(spec.builder.dialect)),
                    clean_id(type_name(spec.builder.default_dialect)),
                    spec.builder.get_unpack_method_flags(),
                ),
            )
        )
        return f"{spec.cls_attrs_name}.{method_name}({method_args})"

    def _add_build_variant_unpacker(
        self,
        spec: ValueSpec,
        lines: CodeLines,
        variant_method_name: str,
        variant_method_call: str,
    ) -> None:
        if spec.builder.is_nailed:
            spec.builder.ensure_object_imported(get_class_that_defines_method)
            lines.append(
                "if get_class_that_defines_method("
                f"'{variant_method_name}',variant) != variant:"
            )
            with lines.indent():
                spec.builder.ensure_object_imported(spec.builder.__class__)
                lines.append(
                    "CodeBuilder(variant, "
                    "dialect=_dialect, "
                    f"format_name={repr(spec.builder.format_name)}, "
                    "default_dialect=_default_dialect)"
                    ".add_unpack_method()"
                )
                if not self.discriminator.field:
                    with lines.indent("try:"):
                        lines.append(f"return variant.{variant_method_call}")
                    lines.append("except Exception: pass")
        else:
            spec.builder.ensure_object_imported(AttrsHolder)
            attrs = f"attrs_{random_hex()}"
            lines.append(f"{attrs} = AttrsHolder('{attrs}')")
            lines.append(f"{spec.attrs_registry_name}[variant] = {attrs}")
            lines.append(
                "CodeBuilder(variant, "
                "dialect=_dialect, "
                f"format_name={repr(spec.builder.format_name)}, "
                "default_dialect=_default_dialect,"
                f"attrs={attrs},"
                f"attrs_registry={spec.attrs_registry_name})"
                ".add_unpack_method()"
            )
            if not self.discriminator.field:
                with lines.indent("try:"):
                    lines.append(f"return {attrs}.{variant_method_call}")
                lines.append("except Exception: pass")

    def _add_register_variant_tags(
        self, lines: CodeLines, variant_tagger_expr: str
    ) -> None:
        if self.discriminator.variant_tagger_fn:
            lines.append(f"variant_tags = {variant_tagger_expr}")
            with lines.indent("if type(variant_tags) is list:"):
                with lines.indent("for varint_tag in variant_tags:"):
                    lines.append("variants_map[varint_tag] = variant")
            with lines.indent("else:"):
                lines.append("variants_map[variant_tags] = variant")
        else:
            lines.append(f"variants_map[{variant_tagger_expr}] = variant")


class SubtypeUnpackerBuilder(DiscriminatedUnionUnpackerBuilder):
    def _get_variants_attr(self, spec: ValueSpec) -> str:
        if self._variants_attr is None:
            assert self.discriminator.include_subtypes
            self._variants_attr = "__mashumaro_subtype_variants__"
        return self._variants_attr


def _unpack_with_annotated_serialization_strategy(
    spec: ValueSpec, strategy: SerializationStrategy
) -> Expression:
    pass


def get_overridden_deserialization_method(
    spec: ValueSpec,
) -> Callable | str | ExpressionWrapper | None:
    pass


@register
def unpack_type_with_overridden_deserialization(
    spec: ValueSpec,
) -> Expression | None:
    pass


def _unpack_annotated_serializable_type(spec: ValueSpec) -> Expression | None:
    pass


@register
def unpack_serializable_type(spec: ValueSpec) -> Expression | None:
    pass


@register
def unpack_generic_serializable_type(spec: ValueSpec) -> Expression | None:
    pass


@register
def unpack_dataclass(spec: ValueSpec) -> Expression | None:
    pass


@register
def unpack_final(spec: ValueSpec) -> Expression | None:
    pass


@register
def unpack_any(spec: ValueSpec) -> Expression | None:
    pass


@register
def unpack_special_typing_primitive(spec: ValueSpec) -> Expression | None:
    pass


@register
def unpack_number(spec: ValueSpec) -> Expression | None:
    pass


@register
def unpack_bool(spec: ValueSpec) -> Expression | None:
    pass


@register
def unpack_none(spec: ValueSpec) -> Expression | None:
    pass


@register
def unpack_date_objects(spec: ValueSpec) -> Expression | None:
    pass


@register
def unpack_timedelta(spec: ValueSpec) -> Expression | None:
    pass


@register
def unpack_timezone(spec: ValueSpec) -> Expression | None:
    pass


@register
def unpack_zone_info(spec: ValueSpec) -> Expression | None:
    pass


@register
def unpack_uuid(spec: ValueSpec) -> Expression | None:
    pass


@register
def unpack_ipaddress(spec: ValueSpec) -> Expression | None:
    pass


@register
def unpack_decimal(spec: ValueSpec) -> Expression | None:
    pass


@register
def unpack_fraction(spec: ValueSpec) -> Expression | None:
    pass


def unpack_tuple(spec: ValueSpec, args: tuple[type, ...]) -> Expression:
    if not args:
        if spec.type in (Tuple, tuple):
            args = [Any, ...]  # type: ignore
        else:
            return "()"
    elif len(args) == 1 and args[0] == ():
        if not PY_311_MIN:
            return "()"
    if len(args) == 2 and args[1] is Ellipsis:
        unpacker = UnpackerRegistry.get(
            spec.copy(type=args[0], expression="value", could_be_none=True)
        )
        return f"tuple([{unpacker} for value in {spec.expression}])"
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
        unpackers: list[Expression] = []
        for _idx, _arg_idx in enumerate(arg_indexes):
            if isinstance(_arg_idx, tuple):
                u_expr = f"{spec.expression}[{_arg_idx[0]}:{_arg_idx[1]}]"
            else:
                u_expr = f"{spec.expression}[{_arg_idx}]"
            unpacker = UnpackerRegistry.get(
                spec.copy(
                    type=args[_idx], expression=u_expr, could_be_none=True
                )
            )
            if unpacker != "*()":  # workaround for empty tuples
                unpackers.append(unpacker)
        return f"tuple([{', '.join(unpackers)}])"


def unpack_named_tuple(spec: ValueSpec) -> Expression:
    pass


def unpack_typed_dict(spec: ValueSpec) -> Expression:
    pass


@register
def unpack_collection(spec: ValueSpec) -> Expression | None:
    pass


@register
def unpack_pathlike(spec: ValueSpec) -> Expression | None:
    pass


@register
def unpack_enum(spec: ValueSpec) -> Expression | None:
    pass


@register
def unpack_pattern(spec: ValueSpec) -> Expression | None:
    pass
