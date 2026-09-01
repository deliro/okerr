"""A Rust-like Result type for Python."""

from __future__ import annotations

import functools
import inspect
import sys
import warnings
from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine, Generator, Iterator
from typing import (
    Any,
    Generic,
    Literal,
    NoReturn,
    ParamSpec,
    Self,
    TypeAlias,
    TypeVar,
    cast,
    overload,
)

if sys.version_info >= (3, 13):
    from typing import TypeIs
else:  # pragma: no cover
    from typing_extensions import TypeIs

T_co = TypeVar("T_co", covariant=True)  # Success type
E_co = TypeVar("E_co", covariant=True)  # Error type
U = TypeVar("U")
F = TypeVar("F")
P = ParamSpec("P")
R = TypeVar("R")
TBE = TypeVar("TBE", bound=BaseException)
TE = TypeVar("TE", bound=Exception)
T2 = TypeVar("T2")
T3 = TypeVar("T3")
T4 = TypeVar("T4")
T5 = TypeVar("T5")
E2 = TypeVar("E2")

_TRUTHINESS_MSG = (
    "Ok and Err have no truth value; use is_ok()/is_err(), pattern matching, "
    "or is_ok_and()/is_err_and() instead of `if result:`"
)


class Ok(Generic[T_co]):
    """An ``Ok`` value indicating success, storing arbitrary data for the return value."""

    __match_args__ = ("ok_value",)
    __slots__ = ("_value",)

    _value: T_co

    def __iter__(self) -> Iterator[T_co]:
        return iter((self._value,))

    def __init__(self, value: T_co) -> None:
        object.__setattr__(self, "_value", value)

    def __setattr__(self, name: str, value: object) -> NoReturn:
        msg = "Ok is immutable"
        raise AttributeError(msg)

    def __delattr__(self, name: str) -> NoReturn:
        msg = "Ok is immutable"
        raise AttributeError(msg)

    # ty: covariant T_co in the constructor callable is safe: pickle only ever
    # round-trips the instance's own value, never substitutes a supertype.
    def __reduce__(
        self,
    ) -> tuple[Callable[[T_co], Ok[T_co]], tuple[T_co]]:  # ty: ignore[invalid-generic-class]
        return (Ok, (self._value,))

    def __repr__(self) -> str:
        return f"Ok({self._value!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Ok) and self._value == other._value

    def __hash__(self) -> int:
        return hash((True, self._value))

    def __bool__(self) -> NoReturn:
        raise TypeError(_TRUTHINESS_MSG)

    def is_ok(self) -> Literal[True]:
        """
        Return ``True`` because this is an ``Ok`` value.

        Examples:
            >>> Ok(2).is_ok()
            True

        """
        return True

    def is_err(self) -> Literal[False]:
        """
        Return ``False`` because this is an ``Ok`` value.

        Examples:
            >>> Ok(2).is_err()
            False

        """
        return False

    def is_ok_and(self, f: Callable[[T_co], bool]) -> bool:
        """
        Return ``True`` if the result is ``Ok`` and the predicate *f* returns ``True``.

        Examples:
            >>> Ok(2).is_ok_and(lambda x: x > 1)
            True
            >>> Ok(0).is_ok_and(lambda x: x > 1)
            False

        """
        return f(self._value)

    async def is_ok_and_async(self, f: Callable[[T_co], Awaitable[bool]]) -> bool:
        """Async version of ``is_ok_and``."""
        return await f(self._value)

    def is_err_and(self, _f: Callable[[E_co], bool]) -> Literal[False]:
        """
        Return ``True`` if the result is ``Err`` and the predicate *f* returns ``True``.

        Since this is ``Ok``, always returns ``False``.

        Examples:
            >>> Ok(2).is_err_and(lambda e: True)
            False

        """
        return False

    async def is_err_and_async(self, _f: Callable[[E_co], Awaitable[bool]]) -> Literal[False]:
        """
        Async version of ``is_err_and``.

        Since this is ``Ok``, always returns ``False``.
        """
        return False

    def ok(self) -> T_co:
        """
        Convert from ``Result[T, E]`` to ``T | None``.

        Return the contained ``Ok`` value, discarding the error, if any.

        Examples:
            >>> Ok(2).ok()
            2

        """
        return self._value

    def err(self) -> None:
        """
        Convert from ``Result[T, E]`` to ``E | None``.

        Return ``None``, discarding the success value.

        Examples:
            >>> Ok(2).err() is None
            True

        """
        return

    @property
    def ok_value(self) -> T_co:
        """
        The contained ``Ok`` value.

        Examples:
            >>> Ok(2).ok_value
            2

        """
        return self._value

    def expect(self, _message: str) -> T_co:
        """
        Return the contained ``Ok`` value.

        Because this is an ``Ok``, the *message* argument is unused.

        Raises:
            UnwrapError: Never raised for ``Ok``.

        Examples:
            >>> Ok(2).expect("must exist")
            2

        """
        return self._value

    def expect_err(self, message: str) -> NoReturn:
        """
        Return the contained ``Err`` value.

        Raises:
            UnwrapError: Always, because this is an ``Ok`` value, with a
                message including the passed *message* and the ``Ok`` content.

        Examples:
            >>> Ok(2).expect_err("wanted an error")
            Traceback (most recent call last):
                ...
            corrode.result.UnwrapError: wanted an error

        """
        raise UnwrapError(self, message)

    def unwrap(self) -> T_co:
        """
        Return the contained ``Ok`` value.

        Because this is an ``Ok``, this method never raises.

        Raises:
            UnwrapError: Never raised for ``Ok``.

        Examples:
            >>> Ok(2).unwrap()
            2

        """
        return self._value

    def unwrap_err(self) -> NoReturn:
        """
        Return the contained ``Err`` value.

        Raises:
            UnwrapError: Always, because this is an ``Ok`` value.

        Examples:
            >>> Ok(2).unwrap_err()
            Traceback (most recent call last):
                ...
            corrode.result.UnwrapError: Called `Result.unwrap_err()` on an `Ok` value

        """
        raise UnwrapError(self, "Called `Result.unwrap_err()` on an `Ok` value")

    def unwrap_or(self, _default: U) -> T_co:
        """
        Return the contained ``Ok`` value or a provided default.

        The default value is ignored because this is an ``Ok``.

        Examples:
            >>> Ok(2).unwrap_or(0)
            2

        """
        return self._value

    def unwrap_or_else(self, _op: object) -> T_co:
        """
        Return the contained ``Ok`` value or compute it from a callable.

        The callable is never invoked because this is an ``Ok``.

        Examples:
            >>> Ok(2).unwrap_or_else(len)
            2

        """
        return self._value

    async def unwrap_or_else_async(self, _op: object) -> T_co:
        """
        Async version of ``unwrap_or_else``.

        The callable is never invoked because this is an ``Ok``.
        """
        return self._value

    def unwrap_or_raise(self, _e: object) -> T_co:
        """
        Return the contained ``Ok`` value or raise the provided exception.

        The exception is never raised because this is an ``Ok``.

        Examples:
            >>> Ok(2).unwrap_or_raise(ValueError)
            2

        """
        return self._value

    def map(self, op: Callable[[T_co], U]) -> Ok[U]:
        """
        Apply *op* to the contained ``Ok`` value.

        Map a ``Result[T, E]`` to ``Result[U, E]``, leaving an ``Err`` value untouched.

        Examples:
            >>> Ok(2).map(lambda x: x * 10)
            Ok(20)

        """
        return Ok(op(self._value))

    async def map_async(self, op: Callable[[T_co], Awaitable[U]]) -> Ok[U]:
        """
        Async version of ``map``.

        Await the coroutine returned by *op* applied to the contained ``Ok`` value.
        """
        return Ok(await op(self._value))

    def map_or(self, _default: object, op: Callable[[T_co], U]) -> U:
        """
        Apply *op* to the contained ``Ok`` value, or return *default* if ``Err``.

        Since this is ``Ok``, *default* is ignored.

        Examples:
            >>> Ok(2).map_or(0, lambda x: x * 10)
            20

        """
        return op(self._value)

    async def map_or_async(self, _default: object, op: Callable[[T_co], Awaitable[U]]) -> U:
        """
        Async version of ``map_or``.

        Since this is ``Ok``, *default* is ignored.
        """
        return await op(self._value)

    def map_or_else(self, _default_op: Callable[[E_co], U], op: Callable[[T_co], U]) -> U:
        """
        Apply *op* to a contained ``Ok`` value, or *default_op* to a contained ``Err``.

        Map a ``Result[T, E]`` to ``U``.

        Examples:
            >>> Ok(2).map_or_else(lambda e: 0, lambda x: x * 10)
            20

        """
        return op(self._value)

    async def map_or_else_async(
        self,
        _default_op: Callable[[E_co], Awaitable[U]],
        op: Callable[[T_co], Awaitable[U]],
    ) -> U:
        """
        Async version of ``map_or_else``.

        Since this is ``Ok``, *default_op* is ignored.
        """
        return await op(self._value)

    def map_err(self, _op: object) -> Ok[T_co]:
        """
        Apply *op* to a contained ``Err`` value, leaving ``Ok`` untouched.

        Map a ``Result[T, E]`` to ``Result[T, F]``.

        Examples:
            >>> Ok(2).map_err(str.upper)
            Ok(2)

        """
        return self

    async def map_err_async(self, _op: object) -> Ok[T_co]:
        """
        Async version of ``map_err``.

        Return the ``Ok`` value untouched.
        """
        return self

    def and_then(self, op: Callable[[T_co], Result[U, E_co]]) -> Result[U, E_co]:
        """
        Call *op* if the result is ``Ok``, otherwise return the ``Err`` value of *self*.

        This function can be used for control flow based on ``Result`` values.

        Examples:
            >>> def halve(x: int) -> Result[int, str]:
            ...     return Ok(x // 2) if x % 2 == 0 else Err("odd")
            >>> Ok(4).and_then(halve)
            Ok(2)
            >>> Ok(3).and_then(halve)
            Err('odd')

        """
        return op(self._value)

    async def and_then_async(
        self,
        op: Callable[[T_co], Awaitable[Result[U, E_co]]],
    ) -> Result[U, E_co]:
        """
        Async version of ``and_then``.

        Await the coroutine returned by *op* applied to the contained ``Ok`` value.
        """
        return await op(self._value)

    def or_else(self, _op: object) -> Ok[T_co]:
        """
        Call *op* if the result is ``Err``, otherwise return the ``Ok`` value of *self*.

        Since this is ``Ok``, *op* is never called.

        Examples:
            >>> Ok(2).or_else(lambda e: Ok(0))
            Ok(2)

        """
        return self

    async def or_else_async(self, _op: object) -> Ok[T_co]:
        """
        Async version of ``or_else``.

        Return the ``Ok`` value untouched.
        """
        return self

    def inspect(self, op: Callable[[T_co], Any]) -> Self:
        """
        Call *op* with the contained value if ``Ok``.

        Return the original result unchanged.

        Examples:
            >>> Ok(2).inspect(print)
            2
            Ok(2)

        """
        op(self._value)
        return self

    async def inspect_async(
        self,
        op: Callable[[T_co], Awaitable[Any]],
    ) -> Self:
        """
        Async version of ``inspect``.

        Await the coroutine returned by *op* applied to the contained ``Ok`` value.
        Return the original result unchanged.
        """
        await op(self._value)
        return self

    def inspect_err(self, _op: object) -> Self:
        """
        Call *op* with the contained error if ``Err``.

        Return the original result unchanged. Since this is ``Ok``, *op* is not called.

        Examples:
            >>> Ok(2).inspect_err(print)
            Ok(2)

        """
        return self

    async def inspect_err_async(self, _op: object) -> Self:
        """
        Async version of ``inspect_err``.

        Return the original result unchanged. Since this is ``Ok``, *op* is not called.
        """
        return self

    @overload
    def zip(self, r1: Result[T2, E2], /) -> Result[tuple[T_co, T2], E2]: ...

    @overload
    def zip(
        self,
        r1: Result[T2, E2],
        r2: Result[T3, E2],
        /,
    ) -> Result[tuple[T_co, T2, T3], E2]: ...

    @overload
    def zip(
        self,
        r1: Result[T2, E2],
        r2: Result[T3, E2],
        r3: Result[T4, E2],
        /,
    ) -> Result[tuple[T_co, T2, T3, T4], E2]: ...

    @overload
    def zip(
        self,
        r1: Result[T2, E2],
        r2: Result[T3, E2],
        r3: Result[T4, E2],
        r4: Result[T5, E2],
        /,
    ) -> Result[tuple[T_co, T2, T3, T4, T5], E2]: ...

    def zip(self, *results: Result[Any, Any]) -> Result[Any, Any]:
        """
        Combine this ``Ok`` with one to four other ``Result`` values into a tuple.

        Returns ``Ok`` of a tuple of all values if all results are ``Ok``.
        Returns the first ``Err`` encountered otherwise.

        Examples:
            >>> Ok(1).zip(Ok("a"))
            Ok((1, 'a'))
            >>> Ok(1).zip(Ok("a"), Ok(3.0))
            Ok((1, 'a', 3.0))
            >>> Ok(1).zip(Err("bad"))
            Err('bad')

        """
        values: list[Any] = [self._value]
        for r in results:
            match r:
                case Ok(v):
                    values.append(v)
                case Err():
                    return r
        return Ok(tuple(values))

    def flatten(self: Ok[Result[U, F]]) -> Result[U, F]:
        """
        Remove one level of ``Result`` nesting.

        Convert ``Result[Result[U, F], E]`` into ``Result[U, F]``.
        Only one level is removed — ``Ok(Ok(Ok(1))).flatten()`` is ``Ok(Ok(1))``.

        Examples:
            >>> Ok(Ok(1)).flatten()
            Ok(1)
            >>> Ok(Err("bad")).flatten()
            Err('bad')

        """
        return self._value

    def transpose(self: Ok[U | None]) -> Ok[U] | None:
        """
        Transpose a ``Result`` of an optional value into an optional ``Result``.

        Convert ``Result[U | None, E]`` into ``Result[U, E] | None``:
        ``None`` if the contained value is ``None``, the ``Ok`` unchanged
        otherwise. This is the inverse of ``from_optional``.

        Examples:
            >>> Ok(1).transpose()
            Ok(1)
            >>> Ok(None).transpose() is None
            True

        """
        if self._value is None:
            return None
        return cast("Ok[U]", self)


class DoError(Exception):
    """
    Signal to ``do()`` that the result is an ``Err``, short-circuiting the generator.

    Raised by ``Err.__iter__``. If you see this exception outside ``do()`` /
    ``do_async()``, you iterated an ``Err`` directly (e.g. ``list(Err(...))``,
    ``for x in err``) — ``Result`` values are not general-purpose iterables.
    """

    def __init__(self, err: Err[Any]) -> None:
        self.err: Err[Any] = err
        super().__init__(
            "Err is only iterable inside do() notation; "
            "use pattern matching or combinators to access the error value",
        )


def _err_do_iter(err: Err[Any]) -> Iterator[NoReturn]:
    """
    Raise ``DoError`` to short-circuit ``do()`` / ``do_async()`` generators.

    This yield is syntactically required to make the function a generator,
    but it is never reached because ``DoError`` is always raised first.
    """
    raise DoError(err)
    # SAFETY: This `yield` is unreachable at runtime — `DoError` is always raised
    # above. It exists solely to make Python treat this function as a generator
    # (required by the `do()` / `do_async()` machinery which expects a generator
    # protocol). Without it, the function would be a plain callable and the `yield
    # from early_return(err)` expression in user code would raise `TypeError`.
    yield  # type: ignore[unreachable]


class Err(Generic[E_co]):
    """An ``Err`` value signifying failure, storing arbitrary data for the error."""

    __match_args__ = ("err_value",)
    __slots__ = ("_value",)

    _value: E_co

    def __iter__(self) -> Iterator[NoReturn]:
        return _err_do_iter(self)

    def __init__(self, value: E_co) -> None:
        object.__setattr__(self, "_value", value)

    def __setattr__(self, name: str, value: object) -> NoReturn:
        msg = "Err is immutable"
        raise AttributeError(msg)

    def __delattr__(self, name: str) -> NoReturn:
        msg = "Err is immutable"
        raise AttributeError(msg)

    # ty: covariant E_co in the constructor callable is safe: pickle only ever
    # round-trips the instance's own value, never substitutes a supertype.
    def __reduce__(
        self,
    ) -> tuple[Callable[[E_co], Err[E_co]], tuple[E_co]]:  # ty: ignore[invalid-generic-class]
        return (Err, (self._value,))

    def __repr__(self) -> str:
        return f"Err({self._value!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Err) and self._value == other._value

    def __hash__(self) -> int:
        return hash((False, self._value))

    def __bool__(self) -> NoReturn:
        raise TypeError(_TRUTHINESS_MSG)

    def is_ok(self) -> Literal[False]:
        """
        Return ``False`` because this is an ``Err`` value.

        Examples:
            >>> Err("boom").is_ok()
            False

        """
        return False

    def is_err(self) -> Literal[True]:
        """
        Return ``True`` because this is an ``Err`` value.

        Examples:
            >>> Err("boom").is_err()
            True

        """
        return True

    def is_ok_and(self, _f: Callable[[T_co], bool]) -> Literal[False]:
        """
        Return ``True`` if the result is ``Ok`` and the predicate *f* returns ``True``.

        Since this is ``Err``, always returns ``False``.

        Examples:
            >>> Err("boom").is_ok_and(lambda x: True)
            False

        """
        return False

    async def is_ok_and_async(self, _f: Callable[[T_co], Awaitable[bool]]) -> Literal[False]:
        """
        Async version of ``is_ok_and``.

        Since this is ``Err``, always returns ``False``.
        """
        return False

    def is_err_and(self, f: Callable[[E_co], bool]) -> bool:
        """
        Return ``True`` if the result is ``Err`` and the predicate *f* returns ``True``.

        Examples:
            >>> Err("boom").is_err_and(lambda e: "boo" in e)
            True
            >>> Err("boom").is_err_and(lambda e: e == "x")
            False

        """
        return f(self._value)

    async def is_err_and_async(self, f: Callable[[E_co], Awaitable[bool]]) -> bool:
        """Async version of ``is_err_and``."""
        return await f(self._value)

    def ok(self) -> None:
        """
        Convert from ``Result[T, E]`` to ``T | None``.

        Return ``None``, discarding the error value.

        Examples:
            >>> Err("boom").ok() is None
            True

        """
        return

    def err(self) -> E_co:
        """
        Convert from ``Result[T, E]`` to ``E | None``.

        Return the contained ``Err`` value, discarding the success value, if any.

        Examples:
            >>> Err("boom").err()
            'boom'

        """
        return self._value

    @property
    def err_value(self) -> E_co:
        """
        The contained ``Err`` value.

        Examples:
            >>> Err("boom").err_value
            'boom'

        """
        return self._value

    def expect(self, message: str) -> NoReturn:
        """
        Return the contained ``Ok`` value.

        Raises:
            UnwrapError: Always, because this is an ``Err`` value, with a
                message including the passed *message* and the ``Err`` content.

        Examples:
            >>> Err("boom").expect("must exist")
            Traceback (most recent call last):
                ...
            corrode.result.UnwrapError: must exist: 'boom'

        """
        exc = UnwrapError(
            self,
            f"{message}: {self._value!r}",
        )
        if isinstance(self._value, BaseException):
            raise exc from self._value
        raise exc

    def expect_err(self, _message: str) -> E_co:
        """
        Return the contained ``Err`` value.

        Because this is an ``Err``, the *message* argument is unused.

        Raises:
            UnwrapError: Never raised for ``Err``.

        Examples:
            >>> Err("boom").expect_err("wanted an error")
            'boom'

        """
        return self._value

    def unwrap(self) -> NoReturn:
        """
        Return the contained ``Ok`` value.

        Raises:
            UnwrapError: Always, because this is an ``Err`` value, with a
                message provided by the ``Err`` content.

        Examples:
            >>> Err("boom").unwrap()
            Traceback (most recent call last):
                ...
            corrode.result.UnwrapError: Called `Result.unwrap()` on an `Err` value: 'boom'

        """
        exc = UnwrapError(
            self,
            f"Called `Result.unwrap()` on an `Err` value: {self._value!r}",
        )
        if isinstance(self._value, BaseException):
            raise exc from self._value
        raise exc

    def unwrap_err(self) -> E_co:
        """
        Return the contained ``Err`` value.

        Because this is an ``Err``, this method never raises.

        Raises:
            UnwrapError: Never raised for ``Err``.

        Examples:
            >>> Err("boom").unwrap_err()
            'boom'

        """
        return self._value

    def unwrap_or(self, default: U) -> U:
        """
        Return the contained ``Ok`` value or a provided default.

        The contained ``Err`` value is discarded.

        Examples:
            >>> Err("boom").unwrap_or(0)
            0

        """
        return default

    def unwrap_or_else(self, op: Callable[[E_co], U]) -> U:
        """
        Return the contained ``Ok`` value or compute it from a callable.

        The callable *op* is applied to the contained ``Err`` value.

        Examples:
            >>> Err("boom").unwrap_or_else(len)
            4

        """
        return op(self._value)

    async def unwrap_or_else_async(self, op: Callable[[E_co], Awaitable[U]]) -> U:
        """
        Async version of ``unwrap_or_else``.

        The callable *op* is applied to the contained ``Err`` value.
        """
        return await op(self._value)

    def unwrap_or_raise(self, e: type[TBE]) -> NoReturn:
        """
        Return the contained ``Ok`` value or raise the provided exception.

        The exception *e* is instantiated with the ``Err`` value and raised.

        Examples:
            >>> Err("boom").unwrap_or_raise(ValueError)
            Traceback (most recent call last):
                ...
            ValueError: boom

        """
        raise e(self._value)

    def map(self, _op: object) -> Err[E_co]:
        """
        Apply *op* to the contained ``Ok`` value.

        Map a ``Result[T, E]`` to ``Result[U, E]``, leaving an ``Err`` value untouched.

        Examples:
            >>> Err("boom").map(lambda x: x * 10)
            Err('boom')

        """
        return self

    async def map_async(self, _op: object) -> Err[E_co]:
        """
        Async version of ``map``.

        Return the ``Err`` value untouched.
        """
        return self

    def map_or(self, default: U, _op: object) -> U:
        """
        Apply *op* to the contained ``Ok`` value, or return *default* if ``Err``.

        Since this is ``Err``, *op* is ignored and *default* is returned.

        Examples:
            >>> Err("boom").map_or(0, lambda x: x * 10)
            0

        """
        return default

    async def map_or_async(self, default: U, _op: object) -> U:
        """
        Async version of ``map_or``.

        Since this is ``Err``, *op* is ignored and *default* is returned.
        """
        return default

    def map_or_else(self, default_op: Callable[[E_co], U], _op: object) -> U:
        """
        Apply *op* to a contained ``Ok`` value, or *default_op* to a contained ``Err``.

        Map a ``Result[T, E]`` to ``U``.

        Examples:
            >>> Err("boom").map_or_else(len, lambda x: x * 10)
            4

        """
        return default_op(self._value)

    async def map_or_else_async(
        self,
        default_op: Callable[[E_co], Awaitable[U]],
        _op: object,
    ) -> U:
        """
        Async version of ``map_or_else``.

        Since this is ``Err``, *op* is ignored.
        """
        return await default_op(self._value)

    def map_err(self, op: Callable[[E_co], F]) -> Err[F]:
        """
        Apply *op* to a contained ``Err`` value, leaving ``Ok`` untouched.

        Map a ``Result[T, E]`` to ``Result[T, F]``.

        Examples:
            >>> Err("boom").map_err(str.upper)
            Err('BOOM')

        """
        return Err(op(self._value))

    async def map_err_async(self, op: Callable[[E_co], Awaitable[F]]) -> Err[F]:
        """
        Async version of ``map_err``.

        Await the coroutine returned by *op* applied to the contained ``Err`` value.
        """
        return Err(await op(self._value))

    def and_then(self, _op: object) -> Err[E_co]:
        """
        Call *op* if the result is ``Ok``, otherwise return the ``Err`` value of *self*.

        This function can be used for control flow based on ``Result`` values.

        Examples:
            >>> Err("boom").and_then(lambda x: Ok(x * 10))
            Err('boom')

        """
        return self

    async def and_then_async(self, _op: object) -> Err[E_co]:
        """
        Async version of ``and_then``.

        Return the ``Err`` value untouched.
        """
        return self

    def or_else(self, op: Callable[[E_co], Result[T_co, F]]) -> Result[T_co, F]:
        """
        Call *op* if the result is ``Err``, otherwise return the ``Ok`` value of *self*.

        Since this is ``Err``, *op* is called with the error value.

        Examples:
            >>> Err("boom").or_else(lambda e: Ok(len(e)))
            Ok(4)

        """
        return op(self._value)

    async def or_else_async(
        self,
        op: Callable[[E_co], Awaitable[Result[T_co, F]]],
    ) -> Result[T_co, F]:
        """
        Async version of ``or_else``.

        Await the coroutine returned by *op* applied to the contained ``Err`` value.
        """
        return await op(self._value)

    def inspect(self, _op: object) -> Self:
        """
        Call *op* with the contained value if ``Ok``.

        Return the original result unchanged. Since this is ``Err``, *op* is not called.

        Examples:
            >>> Err("boom").inspect(print)
            Err('boom')

        """
        return self

    async def inspect_async(self, _op: object) -> Self:
        """
        Async version of ``inspect``.

        Return the original result unchanged. Since this is ``Err``, *op* is not called.
        """
        return self

    def inspect_err(self, op: Callable[[E_co], Any]) -> Self:
        """
        Call *op* with the contained error if ``Err``.

        Return the original result unchanged.

        Examples:
            >>> Err("boom").inspect_err(print)
            boom
            Err('boom')

        """
        op(self._value)
        return self

    async def inspect_err_async(
        self,
        op: Callable[[E_co], Awaitable[Any]],
    ) -> Self:
        """
        Async version of ``inspect_err``.

        Await the coroutine returned by *op* applied to the contained ``Err`` value.
        Return the original result unchanged.
        """
        await op(self._value)
        return self

    def zip(self, *_results: Result[Any, Any]) -> Err[E_co]:
        """
        Combine this ``Err`` with other ``Result`` values.

        Since this is an ``Err``, always returns ``self`` without inspecting the others.

        Examples:
            >>> Err("bad").zip(Ok(1))
            Err('bad')
            >>> Err("bad").zip(Ok(1), Ok(2))
            Err('bad')

        """
        return self

    def flatten(self) -> Err[E_co]:
        """
        Remove one level of ``Result`` nesting.

        Since this is an ``Err``, there is nothing to flatten — ``self`` is returned.

        Examples:
            >>> Err("bad").flatten()
            Err('bad')

        """
        return self

    def transpose(self) -> Err[E_co]:
        """
        Transpose a ``Result`` of an optional value into an optional ``Result``.

        Since this is an ``Err``, there is no value to inspect — ``self`` is returned.

        Examples:
            >>> Err("bad").transpose()
            Err('bad')

        """
        return self


Result: TypeAlias = Ok[T_co] | Err[E_co]
"""A simple ``Result`` type inspired by Rust.

Not all methods (https://doc.rust-lang.org/std/result/enum.Result.html)
have been implemented, only the ones that make sense in the Python context.
"""


class UnwrapError(Exception):
    """
    Exception raised from ``.unwrap_<...>`` and ``.expect_<...>`` calls.

    The original ``Result`` can be accessed via the ``.result`` attribute, but
    this is not intended for regular use, as type information is lost:
    ``UnwrapError`` doesn't know about both ``T`` and ``E``, since it's raised
    from ``Ok()`` or ``Err()`` which only knows about either ``T`` or ``E``,
    not both.
    """

    _result: Result[object, object]

    def __init__(self, result: Result[object, object], message: str) -> None:
        self._result = result
        super().__init__(message)

    @property
    def result(self) -> Result[Any, Any]:
        """Return the original result."""
        return self._result


def as_result(
    *exceptions: type[TE],
) -> Callable[[Callable[P, R]], Callable[P, Result[R, TE]]]:
    """
    Make a decorator to turn a function into one that returns a ``Result``.

    Regular return values are turned into ``Ok(return_value)``. Raised
    exceptions of the specified exception type(s) are turned into ``Err(exc)``.

    Only subclasses of ``Exception`` are accepted. ``BaseException``-only types
    (``KeyboardInterrupt``, ``SystemExit``, ``asyncio.CancelledError``) must
    propagate — swallowing them breaks interrupts and task cancellation.

    Examples:
        >>> @as_result(ValueError)
        ... def parse(s: str) -> int:
        ...     return int(s)
        >>> parse("42")
        Ok(42)
        >>> parse("x").map_err(type)
        Err(<class 'ValueError'>)

    """
    if not exceptions or not all(
        inspect.isclass(exception) and issubclass(exception, Exception) for exception in exceptions
    ):
        msg = "as_result() requires one or more exception types (subclasses of Exception)"
        raise TypeError(msg)

    def decorator(f: Callable[P, R]) -> Callable[P, Result[R, TE]]:
        @functools.wraps(f)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> Result[R, TE]:
            try:
                return Ok(f(*args, **kwargs))
            except exceptions as exc:
                return Err(exc)

        return wrapper

    return decorator


def as_async_result(
    *exceptions: type[TE],
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Coroutine[object, object, Result[R, TE]]]]:
    """
    Make a decorator to turn an async function into one that returns a ``Result``.

    Regular return values are turned into ``Ok(return_value)``. Raised
    exceptions of the specified exception type(s) are turned into ``Err(exc)``.

    Only subclasses of ``Exception`` are accepted. ``BaseException``-only types
    (``KeyboardInterrupt``, ``SystemExit``, ``asyncio.CancelledError``) must
    propagate — swallowing them breaks interrupts and task cancellation.
    """
    if not exceptions or not all(
        inspect.isclass(exception) and issubclass(exception, Exception) for exception in exceptions
    ):
        msg = "as_async_result() requires one or more exception types (subclasses of Exception)"
        raise TypeError(msg)

    def decorator(
        f: Callable[P, Awaitable[R]],
    ) -> Callable[P, Coroutine[object, object, Result[R, TE]]]:
        @functools.wraps(f)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Result[R, TE]:
            try:
                return Ok(await f(*args, **kwargs))
            except exceptions as exc:
                return Err(exc)

        # SAFETY: `async_wrapper` is declared as `async def`, so at runtime it
        # returns a `Coroutine[object, object, Result[R, TBE]]`, which satisfies
        # `Callable[P, Coroutine[...]]`. However, pyright cannot prove this
        # assignment because `ParamSpec` substitution through a `Callable` with
        # `*args: P.args, **kwargs: P.kwargs` does not propagate into the
        # inferred return type of an inner `async def`. The ignore is therefore
        # a checker limitation, not a soundness issue. mypy handles this correctly
        # and needs no suppression.
        return async_wrapper  # pyright: ignore[reportReturnType]

    return decorator


def from_optional(value: U | None, error: F) -> Result[U, F]:
    """
    Convert ``T | None`` into ``Result[T, F]``.

    ``None`` becomes ``Err(error)``; any other value becomes ``Ok(value)``.
    This is the bridge from the idiomatic Python "optional" pattern into
    ``Result``. Note that ``Ok(None)`` cannot be produced — if ``None`` is a
    valid success value for you, construct the ``Result`` explicitly.

    Examples:
        >>> from_optional(42, "missing")
        Ok(42)
        >>> from_optional(None, "missing")
        Err('missing')

    """
    if value is None:
        return Err(error)
    return Ok(value)


def from_optional_or_else(value: U | None, error_fn: Callable[[], F]) -> Result[U, F]:
    """
    Convert ``T | None`` into ``Result[T, F]``, computing the error lazily.

    Like ``from_optional``, but *error_fn* is only called when *value* is
    ``None`` — use it when constructing the error is expensive.

    Examples:
        >>> from_optional_or_else(42, lambda: "missing")
        Ok(42)
        >>> from_optional_or_else(None, lambda: "missing")
        Err('missing')

    """
    if value is None:
        return Err(error_fn())
    return Ok(value)


def is_ok(result: Result[T_co, E_co]) -> TypeIs[Ok[T_co]]:
    """
    Check whether *result* is ``Ok`` (typeguard).

    Usage::

        r: Result[int, str] = get_a_result()
        if is_ok(r):
            r  # r is of type Ok[int]
        elif is_err(r):
            r  # r is of type Err[str]

    Examples:
        >>> is_ok(Ok(1))
        True
        >>> is_ok(Err("boom"))
        False

    """
    return result.is_ok()


def is_err(result: Result[T_co, E_co]) -> TypeIs[Err[E_co]]:
    """
    Check whether *result* is ``Err`` (typeguard).

    Usage::

        r: Result[int, str] = get_a_result()
        if is_ok(r):
            r  # r is of type Ok[int]
        elif is_err(r):
            r  # r is of type Err[str]

    Examples:
        >>> is_err(Err("boom"))
        True
        >>> is_err(Ok(1))
        False

    """
    return result.is_err()


def do(gen: Generator[Result[T_co, E_co], None, None]) -> Result[T_co, E_co]:
    """
    Do notation for Result (syntactic sugar for sequence of ``and_then()`` calls).

    .. deprecated::
        **Not recommended.** Python's type system cannot infer the error type
        through generator expressions. The error types from ``for x in result``
        clauses are consumed by ``__iter__`` and never appear in the generator's
        type signature, so type checkers infer ``Result[T, Never]`` instead of
        the correct union of error types.

        You must always provide an explicit type annotation on the result,
        and that annotation is **not verified** by the type checker — writing
        a wrong error type silently passes. This defeats the type safety
        that ``Result`` exists for.

        Prefer ``match``, ``and_then()`` chains, or ``zip()`` — all of which
        are fully typed without annotations.

    Usage::

        final_result: Result[float, int] = do(
            Ok(len(x) + int(y) + 0.5) for x in Ok("hello") for y in Ok(True)
        )

    NOTE: If you exclude the type annotation e.g. ``Result[float, int]``
    your type checker might be unable to infer the return type.
    To avoid an error, you might need to help it with the type hint.
    """
    warnings.warn(
        "do() is deprecated: the required type annotation is not checked by "
        "type checkers. Prefer match, and_then() chains, or zip().",
        DeprecationWarning,
        stacklevel=2,
    )
    if isinstance(gen, AsyncGenerator):
        msg = (
            "Got async_generator but expected generator. "
            "Use do_async() — see the section on do notation in the README."
        )
        raise TypeError(msg)
    try:
        return next(gen)
    except DoError as e:
        return cast("Err[E_co]", e.err)


async def do_async(
    gen: Generator[Result[T_co, E_co], None, None] | AsyncGenerator[Result[T_co, E_co], None],
) -> Result[T_co, E_co]:
    """
    Async version of ``do()``.

    .. deprecated::
        **Not recommended.** Same limitations as ``do()`` — error types are
        not inferred and the required annotation is not checked by type
        checkers. Prefer ``match``, ``and_then_async()`` chains, or
        ``zip()`` instead.

    Usage::

        final_result: Result[float, int] = await do_async(
            Ok(len(x) + int(y) + z)
            for x in await get_async_result_1()
            for y in await get_async_result_2()
            for z in get_sync_result_3()
        )

    NOTE: Python makes generators async in a counter-intuitive way.

    ::

        # This is a regular generator:
        async def foo(): ...


        do(Ok(1) for x in await foo())

    ::

        # But this is an async generator:
        async def foo(): ...
        async def bar(): ...


        do(Ok(1) for x in await foo() for y in await bar())

    We let users try to use regular ``do()``, which works in some cases
    of awaiting async values. If we hit a case like above, we raise
    an exception telling the user to use ``do_async()`` instead.
    See ``do()``.

    However, for better usability, it's better for ``do_async()`` to also accept
    regular generators, as you get in the first case::

        async def foo(): ...


        do(Ok(1) for x in await foo())

    Furthermore, neither mypy nor pyright can infer that the second case is
    actually an async generator, so we cannot annotate ``do_async()``
    as accepting only an async generator. This is additional motivation
    to accept either.
    """
    warnings.warn(
        "do_async() is deprecated: the required type annotation is not checked "
        "by type checkers. Prefer match, and_then_async() chains, or zip().",
        DeprecationWarning,
        stacklevel=2,
    )
    try:
        if isinstance(gen, AsyncGenerator):
            return await gen.__anext__()
        return next(gen)
    except DoError as e:
        return cast("Err[E_co]", e.err)
