"""Shared query-parameter types.

Browser-built query strings often include filters as empty strings
(`?brand_id=&category_id=`) rather than omitting them entirely. FastAPI/
Pydantic treats `""` as invalid input for `int | None`, not as "absent" -
`OptionalInt` normalizes that empty string to None before validation.

`Skip`/`Limit` are the bounded pagination pair every list endpoint uses.
"""
from datetime import date
from typing import Annotated

from fastapi import Query
from pydantic import BeforeValidator


def _empty_str_to_none(value: object) -> object:
    if value == "":
        return None
    return value


OptionalInt = Annotated[int | None, BeforeValidator(_empty_str_to_none)]


def _empty_list_to_none(value: object) -> object:
    """Same normalization as above, for a filter the browser may repeat.

    A repeated parameter reaches FastAPI as a list, so the empty-string case
    arrives as `[""]` (`?category_id=`) rather than as `""` - which `int | None`
    rejects just as hard inside a list as outside one. An all-empty list becomes
    None, i.e. "no filter", which is what an untouched form field means.
    """
    if value == "":
        return None
    if isinstance(value, (list, tuple)):
        cleaned = [item for item in value if item != ""]
        return cleaned or None
    return value


# A filter the caller may pass more than once: `?category_id=8&category_id=19`.
# A single `?category_id=8` still arrives as [8], so every existing caller that
# sends one value keeps working unchanged.
#
# Query() is not decoration here, it is the whole thing: FastAPI reads a scalar
# parameter out of the query string but a *list* one out of the request BODY
# unless told otherwise, so without it every endpoint below silently ignores the
# filter and returns the unfiltered catalogue.
OptionalIntList = Annotated[
    list[int] | None, Query(), BeforeValidator(_empty_list_to_none)
]

# Same problem, same fix, for the date-range filters on the activity log: an admin
# who clears the "from" box posts `?date_from=`, which is "no lower bound" and not a
# malformed date.
OptionalDate = Annotated[date | None, BeforeValidator(_empty_str_to_none)]

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
