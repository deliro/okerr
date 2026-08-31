"""Type-safety examples validated by mypy, basedpyright, ty, and pyrefly.

This file is never executed at runtime.  Each type checker validates it
statically to ensure the corrode public API is correctly typed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Coroutine
from typing import Any

from corrode import (
    DoError,
    Err,
    Ok,
    Result,
    UnwrapError,
    as_async_result,
    as_result,
    async_iterator,
    from_optional,
    from_optional_or_else,
    is_err,
    is_ok,
    iterator,
)

# ---------------------------------------------------------------------------
# Helpers — use function returns so checkers don't over-narrow
# ---------------------------------------------------------------------------


def make_ok() -> Result[int, str]:
    return Ok(1)


def make_err() -> Result[int, str]:
    return Err("bad")


# ---------------------------------------------------------------------------
# 1. Construction
# ---------------------------------------------------------------------------

ok: Ok[int] = Ok(42)
err: Err[str] = Err("oops")
res: Result[int, str] = make_ok()

# ---------------------------------------------------------------------------
# 2. isinstance narrowing
# ---------------------------------------------------------------------------

res2 = make_ok()
if isinstance(res2, Ok):
    _isinstance_ok_val: int = res2.ok()
    _isinstance_ok_prop: int = res2.ok_value
elif isinstance(res2, Err):
    _isinstance_err_val: str = res2.err()
    _isinstance_err_prop: str = res2.err_value

# ---------------------------------------------------------------------------
# 3. is_ok / is_err type guard narrowing
# ---------------------------------------------------------------------------

res3 = make_ok()
if is_ok(res3):
    _guard_ok: Ok[int] = res3
    _guard_ok_val: int = res3.ok()
if is_err(res3):
    _guard_err: Err[str] = res3
    _guard_err_val: str = res3.err()

# ---------------------------------------------------------------------------
# 4. Pattern matching
# ---------------------------------------------------------------------------

res4 = make_ok()
match res4:
    case Ok(v):
        _match_ok: int = v
    case Err(e):
        _match_err: str = e

# ---------------------------------------------------------------------------
# 5. Covariance
# ---------------------------------------------------------------------------

ok_int: Ok[int] = Ok(1)
ok_float: Ok[float] = ok_int  # int is subtype of float

err_te: Err[TypeError] = Err(TypeError())
err_exc: Err[Exception] = err_te  # TypeError is subtype of Exception

res_narrow: Result[int, TypeError] = Ok(1)
res_wide: Result[float, Exception] = res_narrow

# ---------------------------------------------------------------------------
# 6. map / map_err return types
# ---------------------------------------------------------------------------

_map_ok: Ok[str] = Ok(42).map(str)
_map_err_ok: Ok[int] = Ok(42).map_err(str)  # Ok untouched
_map_err_err: Err[int] = Err("bad").map_err(len)
_map_err_noop: Err[str] = Err("bad").map(int)  # Err untouched

_map_or_ok: str = Ok(42).map_or("default", str)
_map_or_err: str = Err(1).map_or("default", str)

_map_or_else_ok: str = Ok(42).map_or_else(str, str)
_map_or_else_err: str = Err(1).map_or_else(str, str)

# ---------------------------------------------------------------------------
# 7. and_then / or_else chaining
# ---------------------------------------------------------------------------


def _to_str(x: int) -> Result[str, str]:
    return Ok(str(x))


def _recover(e: str) -> Result[int, int]:
    return Ok(len(e))


_at: Result[str, str] = Ok(42).and_then(_to_str)
_at_err: Err[str] = Err("e").and_then(_to_str)

_oe: Ok[int] = Ok(42).or_else(_recover)
_oe_err: Result[int, int] = Err("e").or_else(_recover)

# ---------------------------------------------------------------------------
# 8. Unwrapping
# ---------------------------------------------------------------------------

_unwrap: int = ok.unwrap()
_expect: int = ok.expect("msg")
_unwrap_or: int = ok.unwrap_or(0)
_unwrap_or_raise: int = ok.unwrap_or_raise(ValueError)

_unwrap_err: str = err.unwrap_err()
_expect_err: str = err.expect_err("msg")

# ---------------------------------------------------------------------------
# 9. ok() / err() on unnarrowed Result
# ---------------------------------------------------------------------------

res5 = make_ok()
_ok_optional: int | None = res5.ok()
_err_optional: str | None = res5.err()

# ---------------------------------------------------------------------------
# 10. inspect / inspect_err return Self
# ---------------------------------------------------------------------------

_inspect_ok: Ok[int] = Ok(42).inspect(print)
_inspect_err_ok: Ok[int] = Ok(42).inspect_err(print)
_inspect_err: Err[str] = Err("e").inspect_err(print)
_inspect_ok_err: Err[str] = Err("e").inspect(print)

# ---------------------------------------------------------------------------
# 11. as_result / as_async_result
# ---------------------------------------------------------------------------


@as_result(ValueError)
def parse(s: str) -> int:
    return int(s)


_as_result: Result[int, ValueError] = parse("42")


@as_async_result(OSError)
async def fetch(url: str) -> bytes:  # noqa: ARG001
    return b""


_as_async_result: Awaitable[Result[bytes, OSError]] = fetch("http://example.com")

# ---------------------------------------------------------------------------
# 12. Async method types
# ---------------------------------------------------------------------------


async def _async_examples() -> None:
    async def to_str(x: int) -> str:
        return str(x)

    async def validate(x: int) -> Result[str, str]:
        return Ok(str(x))

    async def pred(x: int) -> bool:
        return x > 0

    async def err_pred(e: str) -> bool:
        return len(e) > 0

    async def side_effect(x: int) -> None:
        _ = x

    async def err_side_effect(e: str) -> None:
        _ = e

    async def fallback(e: str) -> int:  # noqa: ARG001
        return 0

    async def err_transform(e: str) -> int:
        return len(e)

    # map_async
    _ma_ok: Ok[str] = await Ok(42).map_async(to_str)
    _ma_err: Err[str] = await Err("e").map_async(to_str)

    # map_err_async
    _mea_ok: Ok[int] = await Ok(42).map_err_async(err_transform)
    _mea_err: Err[int] = await Err("e").map_err_async(err_transform)

    # map_or_async
    _moa: str = await Ok(42).map_or_async("default", to_str)
    _moa_err: str = await Err(1).map_or_async("default", to_str)

    # and_then_async
    _ata: Result[str, str] = await Ok(42).and_then_async(validate)
    _ata_err: Err[str] = await Err("e").and_then_async(validate)

    # or_else_async
    async def async_recover(e: str) -> Result[int, int]:
        return Ok(len(e))

    _oea: Ok[int] = await Ok(42).or_else_async(async_recover)
    _oea_err: Result[int, int] = await Err("e").or_else_async(async_recover)

    # unwrap_or_else_async
    _uoea: int = await Ok(42).unwrap_or_else_async(fallback)
    _uoea_err: int = await Err("e").unwrap_or_else_async(fallback)

    # is_ok_and_async / is_err_and_async
    _ioa: bool = await Ok(42).is_ok_and_async(pred)
    _iea: bool = await Err("e").is_err_and_async(err_pred)

    # inspect_async / inspect_err_async
    _ia: Ok[int] = await Ok(42).inspect_async(side_effect)
    _iea2: Err[str] = await Err("e").inspect_err_async(err_side_effect)


# ---------------------------------------------------------------------------
# 13. UnwrapError
# ---------------------------------------------------------------------------

_ue = UnwrapError(Ok(1), "test")
_ue_result: Result[object, object] = _ue.result

# ---------------------------------------------------------------------------
# 14. zip
# ---------------------------------------------------------------------------


def make_int_result() -> Result[int, str]:
    return Ok(1)


def make_str_result() -> Result[str, str]:
    return Ok("a")


def make_float_result() -> Result[float, str]:
    return Ok(3.0)


def make_bool_result() -> Result[bool, str]:
    return Ok(True)


# Realistic usage: runtime Results with unknown Ok/Err
_zip2: Result[tuple[int, str], str] = make_int_result().zip(make_str_result())
_zip3: Result[tuple[int, str, float], str] = make_int_result().zip(
    make_str_result(),
    make_float_result(),
)
_zip4: Result[tuple[int, str, float, bool], str] = make_int_result().zip(
    make_str_result(),
    make_float_result(),
    make_bool_result(),
)

# Err.zip always returns Err[E_co]
_zip_err_self: Err[str] = Err("bad").zip(make_int_result())
_zip_err_self2: Err[str] = Err("bad").zip(make_int_result(), make_str_result())

# ---------------------------------------------------------------------------
# 15. iterator / async_iterator module re-exports
# ---------------------------------------------------------------------------

_iter_mod = iterator
_async_iter_mod = async_iterator

# ---------------------------------------------------------------------------
# 16. flatten / transpose
# ---------------------------------------------------------------------------


def make_nested() -> Result[Result[int, str], str]:
    return Ok(Ok(1))


_flattened: Result[int, str] = make_nested().flatten()
_flatten_err: Err[str] = Err("bad").flatten()


def make_optional_result() -> Result[int | None, str]:
    return Ok(1)


_transposed: Result[int, str] | None = make_optional_result().transpose()
_transpose_err: Err[str] = Err("bad").transpose()

# ---------------------------------------------------------------------------
# 17. from_optional / from_optional_or_else
# ---------------------------------------------------------------------------


def maybe_int() -> int | None:
    return 1


_fo: Result[int, str] = from_optional(maybe_int(), "missing")
_foe: Result[int, str] = from_optional_or_else(maybe_int(), lambda: "missing")

# ---------------------------------------------------------------------------
# 18. DoError
# ---------------------------------------------------------------------------

_do_error = DoError(Err("x"))
_do_error_err: Err[object] = _do_error.err

# ---------------------------------------------------------------------------
# 19. iterator: collect_all / map_partition
# ---------------------------------------------------------------------------


def _parse_ts(s: str) -> Result[int, str]:
    return Ok(len(s))


_ca: Result[list[int], list[str]] = iterator.collect_all([make_ok(), make_err()])
_mp: tuple[list[int], list[str]] = iterator.map_partition(["a", "b"], _parse_ts)

# ---------------------------------------------------------------------------
# 20. async_iterator: collect_all / map_partition
# ---------------------------------------------------------------------------


async def _async_iter_examples() -> None:
    async def fetch(i: int) -> Result[int, str]:
        return Ok(i)

    _aca: Result[list[int], list[str]] = await async_iterator.collect_all(
        [fetch(1), fetch(2)],
    )
    _amp: tuple[list[int], list[str]] = await async_iterator.map_partition(
        [1, 2],
        fetch,
        concurrency=2,
    )


# ---------------------------------------------------------------------------
# 21. async_iterator: async iterable sources
# ---------------------------------------------------------------------------


async def _async_source_examples() -> None:
    async def fetch(i: int) -> Result[int, str]:
        return Ok(i)

    async def source() -> AsyncIterator[Coroutine[Any, Any, Result[int, str]]]:
        yield fetch(1)
        yield fetch(2)

    _acollected: Result[list[int], str] = await async_iterator.collect(source(), concurrency=2)
    _afiltered: list[int] = [v async for v in async_iterator.filter_ok(source(), concurrency=2)]


# ---------------------------------------------------------------------------
# 22. async_iterator: zip
# ---------------------------------------------------------------------------


async def _async_zip_examples() -> None:
    async def fetch_int() -> Result[int, str]:
        return Ok(1)

    async def fetch_str() -> Result[str, str]:
        return Ok("a")

    async def fetch_float() -> Result[float, ValueError]:
        return Ok(3.0)

    # heterogeneous value types infer the right tuple
    _az2: Result[tuple[int, str], str] = await async_iterator.zip(fetch_int(), fetch_str())
    _az5: Result[tuple[int, str, int, str, int], str] = await async_iterator.zip(
        fetch_int(),
        fetch_str(),
        fetch_int(),
        fetch_str(),
        fetch_int(),
    )

    # heterogeneous error types produce a usable union
    _az_mixed_err: Result[tuple[int, float], str | ValueError] = await async_iterator.zip(
        fetch_int(),
        fetch_float(),
    )
    match _az_mixed_err:
        case Ok(pair):
            _az_values: tuple[int, float] = pair
        case Err(error):
            _az_error: str | ValueError = error
