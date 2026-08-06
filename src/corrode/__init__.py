"""A Rust-like Result type for Python."""

from . import async_iterator, iterator
from .result import (
    DoError,
    Err,
    Ok,
    Result,
    UnwrapError,
    as_async_result,
    as_result,
    do,
    do_async,
    from_optional,
    from_optional_or_else,
    is_err,
    is_ok,
)

__all__ = [
    "DoError",
    "Err",
    "Ok",
    "Result",
    "UnwrapError",
    "as_async_result",
    "as_result",
    "async_iterator",
    "do",
    "do_async",
    "from_optional",
    "from_optional_or_else",
    "is_err",
    "is_ok",
    "iterator",
]
