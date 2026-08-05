"""Shared query-parameter types.

Browser-built query strings often include filters as empty strings
(`?brand_id=&category_id=`) rather than omitting them entirely. FastAPI/
Pydantic treats `""` as invalid input for `int | None`, not as "absent" -
`OptionalInt` normalizes that empty string to None before validation.

`Skip`/`Limit` are the bounded pagination pair every list endpoint uses.
"""
from typing import Annotated

from fastapi import Query
from pydantic import BeforeValidator


def _empty_str_to_none(value: object) -> object:
    if value == "":
        return None
    return value


OptionalInt = Annotated[int | None, BeforeValidator(_empty_str_to_none)]

# Largest page any caller may request. 500 is exactly what the widest existing
# caller asks for (the Flask app's catalog/admin pages fetch 200-500 at a time),
# so nothing legitimate is affected - but `?limit=100000000` no longer makes the
# database materialize every row of a table on an unauthenticated GET.
MAX_PAGE_SIZE = 500

# ge=0 matters as much as the cap: a negative offset reaches Postgres as
# `OFFSET -1`, which it rejects outright, turning a typo'd query string into a
# 500 instead of a 422.
Skip = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]
