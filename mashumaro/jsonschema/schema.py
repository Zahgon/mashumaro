import datetime
import inspect
import ipaddress
import os
import sys
import warnings
from base64 import encodebytes
from collections import ChainMap, Counter, deque
from collections.abc import (  # type: ignore[attr-defined]
    ByteString,
    Callable,
    Collection,
    Iterable,
    Mapping,
    Sequence,
    Set,
)
from dataclasses import MISSING, dataclass, field, is_dataclass, replace
from decimal import Decimal
from enum import Enum
from fractions import Fraction
from functools import cached_property
from typing import Any, ForwardRef, Tuple, Type
from uuid import UUID
from zoneinfo import ZoneInfo

from typing_extensions import NotRequired, TypeAlias

from mashumaro.config import BaseConfig
from mashumaro.core.const import PY_311_MIN
from mashumaro.core.meta.code.builder import CodeBuilder
from mashumaro.core.meta.helpers import (
    get_args,
    get_function_return_annotation,
    get_literal_values,
    get_type_origin,
    is_annotated,
    is_generic,
    is_literal,
    is_named_tuple,
    is_new_type,
    is_not_required,
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
    resolve_type_params,
    type_name,
)
from mashumaro.core.meta.types.common import NoneType, clean_id
from mashumaro.helper import pass_through
from mashumaro.jsonschema.annotations import (
    Annotation,
    Contains,
    DependentRequired,
    ExclusiveMaximum,
    ExclusiveMinimum,
    MaxContains,
    Maximum,
    MaxItems,
    MaxLength,
    MaxProperties,
    MinContains,
    Minimum,
    MinItems,
    MinLength,
    MinProperties,
    MultipleOf,
    Pattern,
    UniqueItems,
)
from mashumaro.jsonschema.models import (
    DATETIME_FORMATS,
    IPADDRESS_FORMATS,
    Context,
    JSONArraySchema,
    JSONObjectSchema,
    JSONSchema,
    JSONSchemaInstanceFormatExtension,
    JSONSchemaInstanceType,
    JSONSchemaStringFormat,
)
from mashumaro.types import SerializationStrategy

try:
    from mashumaro.mixins.orjson import (
        DataClassORJSONMixin as DataClassJSONMixin,
    )
except ImportError:  # pragma: no cover
    from mashumaro.mixins.json import DataClassJSONMixin  # type: ignore

if sys.version_info >= (3, 14):
    from typing import evaluate_forward_ref

    from annotationlib import get_annotations
else:
    from typing_extensions import evaluate_forward_ref, get_annotations


UTC_OFFSET_PATTERN = r"^UTC([+-][0-2][0-9]:[0-5][0-9])?$"


@dataclass
class Instance:
    type: Type
    name: str | None = None

    __owner_builder: CodeBuilder | None = None
    __self_builder: CodeBuilder | None = None

    _original_type: Type = field(init=False)

    origin_type: Type = field(init=False)
    annotations: list[Annotation] = field(init=False, default_factory=list)

    @cached_property
    def metadata(self) -> dict[str, Any]:
        if self.name and self.__owner_builder:
            return dict(**self.__owner_builder.metadatas.get(self.name, {}))
        else:
            return {}

    @property
    def _self_builder(self) -> CodeBuilder:
        pass

    @property
    def alias(self) -> str | None:
        pass

    @property
    def owner_class(self) -> Type | None:
        pass

    def derive(self, **changes: Any) -> "Instance":
        pass

    def __post_init__(self) -> None:
        self._original_type = self.type
        self.update_type(self.type)
        if is_annotated(self.type):
            self.annotations = getattr(self.type, "__metadata__", [])
            self.type = get_args(self.type)[0]
            self.origin_type = get_type_origin(self.type)

    def update_type(self, new_type: Type) -> None:
        pass

    def fields(self) -> Iterable[tuple[str, Type, bool, Any]]:
        for f_name, f_type in self._self_builder.get_field_types(
            include_extras=True
        ).items():
            f = self._self_builder.dataclass_fields.get(f_name)
            if not f or f and not f.init:
                continue
            f_default = f.default
            if f_default is MISSING:
                f_default = self._self_builder.namespace.get(f_name, MISSING)
            if f_default is not MISSING and not inspect.isdatadescriptor(
                f_default
            ):
                f_default = _default(f_type, f_default, self.get_self_config())

            has_default = (
                f.default is not MISSING or f.default_factory is not MISSING
            )

            yield f_name, f_type, has_default, f_default

    def get_overridden_serialization_method(self) -> Callable | str | None:
        pass

    def get_owner_config(self) -> Type[BaseConfig]:
        pass

    def get_owner_dialect_or_config_option(
        self, option: str, default: Any
    ) -> Any:
        pass

    def get_self_config(self) -> Type[BaseConfig]:
        if self.__self_builder:
            return self.__self_builder.get_config()
        else:
            return BaseConfig


InstanceSchemaCreator: TypeAlias = Callable[
    [Instance, Context], JSONSchema | None
]


@dataclass
class InstanceSchemaCreatorRegistry:
    _registry: list[InstanceSchemaCreator] = field(default_factory=list)

    def register(self, func: InstanceSchemaCreator) -> InstanceSchemaCreator:
        pass

    def iter(self) -> Iterable[InstanceSchemaCreator]:
        yield from self._registry


@dataclass
class EmptyJSONSchema(JSONSchema):
    pass


def get_schema(
    instance: Instance, ctx: Context, with_dialect_uri: bool = False
) -> JSONSchema:
    schema = None
    for schema_creator in Registry.iter():
        schema = schema_creator(instance, ctx)
        if schema is not None:
            if with_dialect_uri:
                schema.schema = ctx.dialect.uri
            break
    if schema is not None:
        apply_schema_annotations(instance, schema)
    for plugin in ctx.plugins:
        try:
            new_schema = plugin.get_schema(instance, ctx, schema)
            if new_schema:
                schema = new_schema
        except NotImplementedError:
            continue
    if schema:
        return schema
    raise NotImplementedError(
        f'Type {type_name(instance.type)} of field "{instance.name}" '
        f"in {type_name(instance.owner_class)} isn't supported"
    )


def _get_schema_or_none(instance: Instance, ctx: Context) -> JSONSchema | None:
    pass


def apply_schema_annotations(
    instance: Instance, schema: JSONSchema
) -> JSONSchema:
    for annotation in instance.annotations:
        if isinstance(annotation, JSONSchema):
            annotation_dict = replace(annotation).to_dict()
            for key in annotation_dict.keys():
                if key in ("$schema", "$ref", "$defs"):
                    continue
                if key in ("const", "default"):
                    value = annotation_dict[key]
                else:
                    value = getattr(annotation, key)
                setattr(schema, key, value)
    return schema


def _default(
    f_type: Type | None, f_value: Any, config_cls: Type[BaseConfig]
) -> Any:
    @dataclass
    class CC(DataClassJSONMixin):
        x: f_type = f_value  # type: ignore

        class Config(config_cls):  # type: ignore
            pass

    return CC(f_value).to_dict()["x"]


Registry = InstanceSchemaCreatorRegistry()
register = Registry.register


def _type_alias_definition_name(alias_type: Any) -> str:
    """Return a stable $defs key for PEP 695 TypeAliasType."""

    name = getattr(alias_type, "__name__", None)
    if isinstance(name, str) and name:
        return name
    return clean_id(str(id(alias_type)))


BASIC_TYPES = {str, int, float, bool}


@register
def on_type_with_overridden_serialization(
    instance: Instance, ctx: Context
) -> JSONSchema | None:
    pass


@register
def on_dataclass(instance: Instance, ctx: Context) -> JSONSchema | None:
    pass


@register
def on_any(instance: Instance, ctx: Context) -> JSONSchema | None:
    pass


def on_literal(instance: Instance, ctx: Context) -> JSONSchema | None:
    pass


@register
def on_special_typing_primitive(
    instance: Instance, ctx: Context
) -> JSONSchema | None:
    pass


@register
def on_number(instance: Instance, ctx: Context) -> JSONSchema | None:
    pass


@register
def on_bool(instance: Instance, ctx: Context) -> JSONSchema | None:
    pass


@register
def on_none(instance: Instance, ctx: Context) -> JSONSchema | None:
    pass


@register
def on_date_objects(instance: Instance, ctx: Context) -> JSONSchema | None:
    pass


@register
def on_timedelta(instance: Instance, ctx: Context) -> JSONSchema | None:
    pass


@register
def on_timezone(instance: Instance, ctx: Context) -> JSONSchema | None:
    pass


@register
def on_zone_info(instance: Instance, ctx: Context) -> JSONSchema | None:
    pass


@register
def on_uuid(instance: Instance, ctx: Context) -> JSONSchema | None:
    pass


@register
def on_ipaddress(instance: Instance, ctx: Context) -> JSONSchema | None:
    pass


@register
def on_decimal(instance: Instance, ctx: Context) -> JSONSchema | None:
    pass


@register
def on_fraction(instance: Instance, ctx: Context) -> JSONSchema | None:
    pass


def on_tuple(instance: Instance, ctx: Context) -> JSONArraySchema:
    pass


def on_named_tuple(instance: Instance, ctx: Context) -> JSONSchema:
    pass


def on_typed_dict(instance: Instance, ctx: Context) -> JSONObjectSchema:
    pass


def apply_array_constraints(
    instance: Instance, schema: JSONSchema
) -> JSONSchema:
    pass


def apply_object_constraints(
    instance: Instance, schema: JSONSchema
) -> JSONSchema:
    pass


@register
def on_collection(instance: Instance, ctx: Context) -> JSONSchema | None:
    pass


@register
def on_pathlike(instance: Instance, ctx: Context) -> JSONSchema | None:
    pass


@register
def on_enum(instance: Instance, ctx: Context) -> JSONSchema | None:
    pass


__all__ = ["Instance", "get_schema"]
