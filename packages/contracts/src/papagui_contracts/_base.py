"""Small dependency-free helpers used by the public contract DTOs."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
import json
from typing import Any, ClassVar, Mapping, TypeVar, cast


JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
DtoType = TypeVar("DtoType", bound="JsonDto")


class ContractValidationError(ValueError):
    """Raised when an external payload violates a PapaGUI contract."""


class JsonDto:
    """JSON convenience API for immutable dataclass DTOs."""

    _JSON_INDENT: ClassVar[int | None] = None

    def to_dict(self) -> dict[str, JsonValue]:
        encoded = encode_json_value(self)
        if not isinstance(encoded, dict):  # pragma: no cover - defensive guard
            raise TypeError(f"{type(self).__name__} did not encode to an object")
        return encoded

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            indent=self._JSON_INDENT if indent is None else indent,
        )

    @classmethod
    def from_json(cls: type[DtoType], payload: str | bytes | bytearray) -> DtoType:
        try:
            decoded = json.loads(payload)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractValidationError("payload must contain valid JSON") from exc
        return cls.from_dict(require_mapping(decoded, "payload"))

    @classmethod
    def from_dict(cls: type[DtoType], payload: Mapping[str, object]) -> DtoType:
        raise NotImplementedError


def encode_json_value(value: object) -> JsonValue:
    if isinstance(value, Enum):
        return cast(JsonScalar, value.value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return cast(JsonScalar, value)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: encode_json_value(getattr(value, item.name))
            for item in fields(value)
            if not item.name.startswith("_")
        }
    if isinstance(value, Mapping):
        return {str(key): encode_json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [encode_json_value(item) for item in value]
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def require_mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ContractValidationError(f"{field_name} must be an object")
    return cast(Mapping[str, object], value)


def require_string(
    value: object,
    field_name: str,
    *,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ContractValidationError(f"{field_name} must be a string")
    if not allow_empty and not value.strip():
        raise ContractValidationError(f"{field_name} must not be empty")
    return value


def optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return require_string(value, field_name)


def require_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ContractValidationError(f"{field_name} must be a boolean")
    return value


def require_int(
    value: object,
    field_name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        else:
            raise ContractValidationError(f"{field_name} must be an integer")
    result = int(value)
    if minimum is not None and result < minimum:
        raise ContractValidationError(f"{field_name} must be at least {minimum}")
    if maximum is not None and result > maximum:
        raise ContractValidationError(f"{field_name} must be at most {maximum}")
    return result


def optional_int(
    value: object,
    field_name: str,
    *,
    minimum: int | None = None,
) -> int | None:
    if value is None:
        return None
    return require_int(value, field_name, minimum=minimum)


def string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ContractValidationError(f"{field_name} must be an array of strings")
    return tuple(require_string(item, field_name) for item in value)


def mapping_get(payload: Mapping[str, object], key: str, default: Any) -> Any:
    """Typed spelling of Mapping.get that keeps constructor code readable."""

    return payload[key] if key in payload else default
