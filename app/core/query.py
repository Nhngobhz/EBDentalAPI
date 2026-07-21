"""Shared query-parameter types.

Browser-built query strings often include filters as empty strings
(`?brand_id=&category_id=`) rather than omitting them entirely. FastAPI/
Pydantic treats `""` as invalid input for `int | None`, not as "absent" -
`OptionalInt` normalizes that empty string to None before validation.
"""
from typing import Annotated

from pydantic import BeforeValidator


def _empty_str_to_none(value: object) -> object:
    if value == "":
        return None
    return value


OptionalInt = Annotated[int | None, BeforeValidator(_empty_str_to_none)]
