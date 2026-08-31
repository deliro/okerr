"""Async iterator utilities for Result."""

from __future__ import annotations

import asyncio
import operator
from collections.abc import (
    AsyncIterable,
    AsyncIterator,
    Callable,
    Coroutine,
    Iterable,
    Iterator,
)
from typing import Any, Generic, TypeVar, overload

from .result import Err, Ok, Result

T = TypeVar("T")
U = TypeVar("U")
E = TypeVar("E")

T1 = TypeVar("T1")
T2 = TypeVar("T2")
T3 = TypeVar("T3")
T4 = TypeVar("T4")
T5 = TypeVar("T5")

_R = TypeVar("_R")
_S = TypeVar("_S")
_CoroOrTask = Coroutine[object, object, _R] | asyncio.Task[_R]


_GROUP_MSG = "one or more awaitables raised exceptions"


class _Source(Generic[_R]):
    """
    Uniform lazy pull interface over a sync or async source of awaitables.

    ``aclose`` finalizes whatever the source still holds: unconsumed sync
    items are closed (or cancelled, for tasks) and an async-generator source
    is closed with ``aclose()`` so its finalizers run deterministically.
    """

    _it: Iterator[_CoroOrTask[_R]] | AsyncIterator[_CoroOrTask[_R]]

    def __init__(
        self,
        iterable: Iterable[_CoroOrTask[_R]] | AsyncIterable[_CoroOrTask[_R]],
    ) -> None:
        if isinstance(iterable, AsyncIterable):
            self._it = aiter(iterable)
        else:
            self._it = iter(iterable)

    async def next_item(self) -> _CoroOrTask[_R] | None:
        """Pull the next item, or ``None`` once the source is exhausted."""
        if isinstance(self._it, AsyncIterator):
            try:
                return await anext(self._it)
            except StopAsyncIteration:
                return None
        return next(self._it, None)

    async def aclose(self) -> list[BaseException]:
        """Finalize the source, returning non-cancellation teardown exceptions."""
        if not isinstance(self._it, AsyncIterator):
            for item in self._it:
                if isinstance(item, asyncio.Task):
                    item.cancel()
                else:
                    item.close()
            return []
        aclose = getattr(self._it, "aclose", None)
        if aclose is None:
            return []
        try:
            await aclose()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 — collected into the propagating group
            return [exc]
        return []


async def _drain(
    pending: set[asyncio.Task[_R]],
    src: _Source[_S],
) -> list[BaseException]:
    """
    Cancel *pending*, finalize *src*, collect teardown exceptions.

    Tasks that raise something other than ``CancelledError`` while being
    cancelled — and a source whose ``aclose()`` raises — would otherwise be
    silently discarded; return those exceptions so the caller can attach
    them to the propagating ``ExceptionGroup``.
    """
    for t in pending:
        t.cancel()
    results = await asyncio.gather(*pending, return_exceptions=True)
    excs = [
        r
        for r in results
        if isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError)
    ]
    excs.extend(await src.aclose())
    return excs


async def _cancel_all(
    pending: set[asyncio.Task[_R]],
    src: _Source[_S],
) -> None:
    await _drain(pending, src)


async def _pull(
    src: _Source[_R],
    pending: set[asyncio.Task[_S]],
) -> _CoroOrTask[_R] | None:
    """
    Pull the next item from *src*, or ``None`` once it is exhausted.

    A raising source is an infrastructure failure: cancel *pending*, finalize
    the source, and propagate everything as a single ``ExceptionGroup`` — the
    same contract a raising task follows.
    """
    try:
        return await src.next_item()
    except asyncio.CancelledError:
        raise
    except BaseException as exc:  # noqa: BLE001 — re-raised in the group below
        excs = [exc, *await _drain(pending, src)]
        raise BaseExceptionGroup(_GROUP_MSG, excs) from None


def _wrap_indexed(idx: int, item: _CoroOrTask[_R]) -> asyncio.Task[tuple[int, _R]]:
    entered = False

    async def _inner() -> tuple[int, _R]:
        nonlocal entered
        entered = True
        return idx, await item

    task = asyncio.ensure_future(_inner())

    def _finalize(_task: asyncio.Task[tuple[int, _R]]) -> None:
        # Cancelled before the wrapper ever ran: *item* was never started, so
        # cancelling the wrapper could not reach it — finalize it directly.
        if entered:
            return
        if isinstance(item, asyncio.Task):
            item.cancel()
        else:
            item.close()

    task.add_done_callback(_finalize)
    return task


async def _fill_indexed(
    pending: set[asyncio.Task[tuple[int, _R]]],
    src: _Source[_R],
    concurrency: int | None,
) -> int:
    """Schedule the initial window of indexed tasks into *pending*."""
    idx = 0
    while concurrency is None or idx < concurrency:
        item = await _pull(src, pending)
        if item is None:
            break
        pending.add(_wrap_indexed(idx, item))
        idx += 1
    return idx


async def _fill_unordered(
    pending: set[asyncio.Task[_R]],
    src: _Source[_R],
    concurrency: int | None,
) -> None:
    """Schedule the initial window of tasks into *pending*."""
    count = 0
    while concurrency is None or count < concurrency:
        item = await _pull(src, pending)
        if item is None:
            return
        pending.add(asyncio.ensure_future(item))
        count += 1


def _split_done(done: set[asyncio.Task[_R]]) -> tuple[list[_R], list[BaseException]]:
    """Split a finished batch into successful values and raised exceptions."""
    values: list[_R] = []
    excs: list[BaseException] = []
    for task in done:
        try:
            values.append(task.result())
        except BaseException as exc:  # noqa: BLE001 — re-raised by the caller as a group
            excs.append(exc)
    return values, excs


async def _map_source(
    iterable: AsyncIterable[T],
    f: Callable[[T], _CoroOrTask[_R]],
) -> AsyncIterator[_CoroOrTask[_R]]:
    """Lazily apply *f* over an async source, closing the source on early exit."""
    it = aiter(iterable)
    try:
        async for element in it:
            yield f(element)
    finally:
        aclose = getattr(it, "aclose", None)
        if aclose is not None:
            await aclose()


async def collect(
    iterable: Iterable[_CoroOrTask[Result[T, E]]] | AsyncIterable[_CoroOrTask[Result[T, E]]],
    *,
    concurrency: int | None = None,
) -> Result[list[T], E]:
    """
    Await an iterable of coroutines or tasks concurrently, collecting results into ``Ok[list]``.

    Accepts a plain iterable or an async iterable (e.g. an async generator)
    of awaitables — async sources are consumed lazily and closed on exit.

    Results are returned in input order.
    Returns the first ``Err`` encountered, cancelling remaining tasks.

    *concurrency* limits how many run at the same time.
    ``None`` means unlimited — all are scheduled at once.

    **Exceptions**: if any coroutine raises, all remaining tasks are cancelled and
    every exception — including any raised while those tasks were being cancelled —
    propagates as a single ``ExceptionGroup``, even when only one task failed.
    One failure is a group of one: the exception type you catch never depends
    on timing. Handle with ``except*``.
    A raising source follows the same contract: in-flight tasks are cancelled
    and the source's exception joins the group.

    Examples:
        >>> import asyncio
        >>> async def fetch(i: int) -> Result[int, str]:
        ...     return Ok(i) if i > 0 else Err("bad")
        >>> asyncio.run(collect([fetch(1), fetch(2), fetch(3)]))
        Ok([1, 2, 3])
        >>> asyncio.run(collect([fetch(1), fetch(0)], concurrency=4))
        Err('bad')

        An async generator works as a source too — items are pulled lazily
        as slots free up:

        >>> async def stream():
        ...     for i in (1, 2, 3):
        ...         yield fetch(i)
        >>> asyncio.run(collect(stream(), concurrency=2))
        Ok([1, 2, 3])

        Raised exceptions always arrive as an ``ExceptionGroup`` — one failure
        is a group of one, several failures are collected together:

        >>> async def broken(source: str) -> Result[int, str]:
        ...     raise ConnectionError(source)
        >>> async def demo(sources: list[str]) -> list[str]:
        ...     errors: list[str] = []
        ...     try:
        ...         await collect([broken(s) for s in sources])
        ...     except* ConnectionError as group:
        ...         errors = sorted(str(e) for e in group.exceptions)
        ...     return errors
        >>> asyncio.run(demo(["eu"]))
        ['eu']
        >>> asyncio.run(demo(["eu", "us"]))
        ['eu', 'us']

    """
    src: _Source[Result[T, E]] = _Source(iterable)
    pending: set[asyncio.Task[tuple[int, Result[T, E]]]] = set()
    indexed: dict[int, T] = {}

    try:
        next_idx = await _fill_indexed(pending, src, concurrency)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            batch, excs = _split_done(done)
            if excs:
                excs.extend(await _drain(pending, src))
                pending = set()
                raise BaseExceptionGroup(_GROUP_MSG, excs)
            batch.sort(key=operator.itemgetter(0))
            for idx, result in batch:
                match result:
                    case Ok(value):
                        indexed[idx] = value
                        next_item = await _pull(src, pending)
                        if next_item is not None:
                            pending.add(_wrap_indexed(next_idx, next_item))
                            next_idx += 1
                    case Err() as err:
                        return err
    finally:
        await _cancel_all(pending, src)

    return Ok([indexed[i] for i in range(len(indexed))])


@overload
async def zip(  # noqa: A001 — intentional builtin shadow, mirrors Result.zip
    r1: _CoroOrTask[Result[T1, E]],
    r2: _CoroOrTask[Result[T2, E]],
    /,
) -> Result[tuple[T1, T2], E]: ...


@overload
async def zip(  # noqa: A001 — intentional builtin shadow, mirrors Result.zip
    r1: _CoroOrTask[Result[T1, E]],
    r2: _CoroOrTask[Result[T2, E]],
    r3: _CoroOrTask[Result[T3, E]],
    /,
) -> Result[tuple[T1, T2, T3], E]: ...


@overload
async def zip(  # noqa: A001 — intentional builtin shadow, mirrors Result.zip
    r1: _CoroOrTask[Result[T1, E]],
    r2: _CoroOrTask[Result[T2, E]],
    r3: _CoroOrTask[Result[T3, E]],
    r4: _CoroOrTask[Result[T4, E]],
    /,
) -> Result[tuple[T1, T2, T3, T4], E]: ...


@overload
async def zip(  # noqa: A001 — intentional builtin shadow, mirrors Result.zip
    r1: _CoroOrTask[Result[T1, E]],
    r2: _CoroOrTask[Result[T2, E]],
    r3: _CoroOrTask[Result[T3, E]],
    r4: _CoroOrTask[Result[T4, E]],
    r5: _CoroOrTask[Result[T5, E]],
    /,
) -> Result[tuple[T1, T2, T3, T4, T5], E]: ...


async def zip(  # noqa: A001 — intentional builtin shadow, mirrors Result.zip
    *results: _CoroOrTask[Result[Any, Any]],
) -> Result[tuple[Any, ...], Any]:
    """
    Await two to five coroutines or tasks concurrently, zipping values into ``Ok[tuple]``.

    The heterogeneous sibling of ``collect`` and the async counterpart of
    ``Result.zip``: each awaitable may produce a different ``Ok`` type, and
    the values are combined into a single tuple.

    Values are returned in argument order, regardless of completion order.
    Returns the first ``Err`` to complete, cancelling the remaining awaitables.

    All awaitables are scheduled at once — there is no concurrency limit.

    **Exceptions**: if any coroutine raises, all remaining tasks are cancelled and
    every exception — including any raised while those tasks were being cancelled —
    propagates as a single ``ExceptionGroup``, even when only one task failed.
    One failure is a group of one: the exception type you catch never depends
    on timing. Handle with ``except*``.

    Examples:
        >>> import asyncio
        >>> async def fetch_name() -> Result[str, str]:
        ...     return Ok("alice")
        >>> async def fetch_age() -> Result[int, str]:
        ...     return Ok(30)
        >>> asyncio.run(zip(fetch_name(), fetch_age()))
        Ok(('alice', 30))

        The first ``Err`` to complete wins and the rest are cancelled:

        >>> async def broken() -> Result[int, str]:
        ...     return Err("bad")
        >>> asyncio.run(zip(fetch_name(), broken()))
        Err('bad')

    """
    collected = await collect(results)
    return collected.map(tuple)


async def map_collect(
    iterable: Iterable[T] | AsyncIterable[T],
    f: Callable[[T], _CoroOrTask[Result[U, E]]],
    *,
    concurrency: int | None = None,
) -> Result[list[U], E]:
    """
    Apply *f* to each element concurrently and collect into ``Ok[list]``.

    Accepts a plain iterable or an async iterable (e.g. an async generator)
    of elements — async sources are consumed lazily and closed on exit.

    Results are returned in input order.
    Returns the first ``Err`` produced by *f*, cancelling remaining tasks.

    *concurrency* limits how many calls to *f* run at the same time.
    ``None`` means unlimited — all are scheduled at once.

    **Exceptions**: if *f* raises, all remaining tasks are cancelled and
    every exception — including any raised while those tasks were being cancelled —
    propagates as a single ``ExceptionGroup``, even when only one task failed.
    One failure is a group of one: the exception type you catch never depends
    on timing. Handle with ``except*``.

    Examples:
        >>> import asyncio
        >>> async def double(x: int) -> Result[int, str]:
        ...     return Ok(x * 2)
        >>> asyncio.run(map_collect([1, 2, 3], double, concurrency=2))
        Ok([2, 4, 6])

    """
    if isinstance(iterable, AsyncIterable):
        return await collect(_map_source(iterable, f), concurrency=concurrency)
    return await collect(
        (f(element) for element in iterable),
        concurrency=concurrency,
    )


async def partition(
    iterable: Iterable[_CoroOrTask[Result[T, E]]] | AsyncIterable[_CoroOrTask[Result[T, E]]],
    *,
    concurrency: int | None = None,
) -> tuple[list[T], list[E]]:
    """
    Await an iterable of coroutines or tasks concurrently, splitting results into ``(oks, errs)``.

    Accepts a plain iterable or an async iterable (e.g. an async generator)
    of awaitables — async sources are consumed lazily and closed on exit.

    Results are collected in input order within each list.
    Unlike ``collect``, never short-circuits — all awaitables run to completion.

    *concurrency* limits how many run at the same time.
    ``None`` means unlimited — all are scheduled at once.

    **Exceptions**: if any coroutine raises, all remaining tasks are cancelled and
    every exception — including any raised while those tasks were being cancelled —
    propagates as a single ``ExceptionGroup``, even when only one task failed.
    One failure is a group of one: the exception type you catch never depends
    on timing. Handle with ``except*``.

    Examples:
        >>> import asyncio
        >>> async def fetch(i: int) -> Result[int, str]:
        ...     return Ok(i) if i > 0 else Err(f"bad: {i}")
        >>> asyncio.run(partition([fetch(1), fetch(-1), fetch(2)]))
        ([1, 2], ['bad: -1'])

    """
    src: _Source[Result[T, E]] = _Source(iterable)
    pending: set[asyncio.Task[tuple[int, Result[T, E]]]] = set()
    indexed: dict[int, Result[T, E]] = {}

    try:
        next_idx = await _fill_indexed(pending, src, concurrency)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            batch, excs = _split_done(done)
            if excs:
                excs.extend(await _drain(pending, src))
                pending = set()
                raise BaseExceptionGroup(_GROUP_MSG, excs)
            for idx, result in batch:
                indexed[idx] = result
                next_item = await _pull(src, pending)
                if next_item is not None:
                    pending.add(_wrap_indexed(next_idx, next_item))
                    next_idx += 1
    finally:
        await _cancel_all(pending, src)

    oks: list[T] = []
    errs: list[E] = []
    for result in (indexed[i] for i in range(len(indexed))):
        match result:
            case Ok(value):
                oks.append(value)
            case Err(e):
                errs.append(e)
    return oks, errs


async def map_partition(
    iterable: Iterable[T] | AsyncIterable[T],
    f: Callable[[T], _CoroOrTask[Result[U, E]]],
    *,
    concurrency: int | None = None,
) -> tuple[list[U], list[E]]:
    """
    Apply *f* to each element concurrently and split the results into ``(oks, errs)``.

    Accepts a plain iterable or an async iterable (e.g. an async generator)
    of elements — async sources are consumed lazily and closed on exit.

    Results are collected in input order within each list.
    Never short-circuits — all calls run to completion.

    *concurrency* limits how many calls to *f* run at the same time.
    ``None`` means unlimited — all are scheduled at once.

    **Exceptions**: if *f* raises, all remaining tasks are cancelled and
    every exception — including any raised while those tasks were being cancelled —
    propagates as a single ``ExceptionGroup``, even when only one task failed.
    One failure is a group of one: the exception type you catch never depends
    on timing. Handle with ``except*``.

    Examples:
        >>> import asyncio
        >>> async def check(i: int) -> Result[int, str]:
        ...     return Ok(i) if i > 0 else Err(f"bad: {i}")
        >>> asyncio.run(map_partition([1, -1, 2], check, concurrency=2))
        ([1, 2], ['bad: -1'])

    """
    if isinstance(iterable, AsyncIterable):
        return await partition(_map_source(iterable, f), concurrency=concurrency)
    return await partition(
        (f(element) for element in iterable),
        concurrency=concurrency,
    )


async def collect_all(
    iterable: Iterable[_CoroOrTask[Result[T, E]]] | AsyncIterable[_CoroOrTask[Result[T, E]]],
    *,
    concurrency: int | None = None,
) -> Result[list[T], list[E]]:
    """
    Await coroutines or tasks concurrently, accumulating **all** errors.

    Accepts a plain iterable or an async iterable (e.g. an async generator)
    of awaitables — async sources are consumed lazily and closed on exit.

    Returns ``Ok`` of all success values (in input order) only if every result
    is ``Ok``; otherwise returns ``Err`` of every error (in input order).
    Unlike ``collect``, never short-circuits — all awaitables run to
    completion, so the caller gets a complete error report.

    *concurrency* limits how many run at the same time.
    ``None`` means unlimited — all are scheduled at once.

    **Exceptions**: if any coroutine raises, all remaining tasks are cancelled and
    every exception — including any raised while those tasks were being cancelled —
    propagates as a single ``ExceptionGroup``, even when only one task failed.
    One failure is a group of one: the exception type you catch never depends
    on timing. Handle with ``except*``.

    Examples:
        >>> import asyncio
        >>> async def fetch(i: int) -> Result[int, str]:
        ...     return Ok(i) if i > 0 else Err(f"bad: {i}")
        >>> asyncio.run(collect_all([fetch(1), fetch(2)]))
        Ok([1, 2])
        >>> asyncio.run(collect_all([fetch(1), fetch(-1), fetch(-2)]))
        Err(['bad: -1', 'bad: -2'])

    """
    oks, errs = await partition(iterable, concurrency=concurrency)
    if errs:
        return Err(errs)
    return Ok(oks)


async def filter_ok_unordered(
    iterable: Iterable[_CoroOrTask[Result[T, E]]] | AsyncIterable[_CoroOrTask[Result[T, E]]],
    *,
    concurrency: int | None = None,
) -> AsyncIterator[T]:
    """
    Await coroutines or tasks concurrently, yielding ``Ok`` values as they complete.

    Accepts a plain iterable or an async iterable (e.g. an async generator)
    of awaitables — async sources are consumed lazily and closed on exit.

    ``Err`` values are silently skipped.
    Values are yielded in completion order, not input order.

    *concurrency* limits how many run at the same time.
    ``None`` means unlimited — all are scheduled at once.

    If the consumer stops iterating early (``break``, exception, ``aclose()``),
    all in-flight tasks are cancelled and unconsumed coroutines are closed.

    **Exceptions**: if any coroutine raises, all remaining tasks are cancelled and
    every exception — including any raised while those tasks were being cancelled —
    propagates as a single ``ExceptionGroup``, even when only one task failed.
    One failure is a group of one: the exception type you catch never depends
    on timing. Handle with ``except*``.

    Examples:
        ```python
        async for user in filter_ok_unordered([fetch(1), fetch(2), fetch(3)]):
            print(user)
        ```

    """
    src: _Source[Result[T, E]] = _Source(iterable)
    pending: set[asyncio.Task[Result[T, E]]] = set()

    try:
        await _fill_unordered(pending, src, concurrency)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            results, excs = _split_done(done)
            if excs:
                excs.extend(await _drain(pending, src))
                pending = set()
                raise BaseExceptionGroup(_GROUP_MSG, excs)
            for result in results:
                match result:
                    case Ok(value):
                        yield value
                    case Err():
                        pass
                next_item = await _pull(src, pending)
                if next_item is not None:
                    pending.add(asyncio.ensure_future(next_item))
    finally:
        await _cancel_all(pending, src)


async def filter_err_unordered(
    iterable: Iterable[_CoroOrTask[Result[T, E]]] | AsyncIterable[_CoroOrTask[Result[T, E]]],
    *,
    concurrency: int | None = None,
) -> AsyncIterator[E]:
    """
    Await coroutines or tasks concurrently, yielding ``Err`` values as they complete.

    Accepts a plain iterable or an async iterable (e.g. an async generator)
    of awaitables — async sources are consumed lazily and closed on exit.

    ``Ok`` values are silently skipped.
    Values are yielded in completion order, not input order.

    *concurrency* limits how many run at the same time.
    ``None`` means unlimited — all are scheduled at once.

    If the consumer stops iterating early (``break``, exception, ``aclose()``),
    all in-flight tasks are cancelled and unconsumed coroutines are closed.

    **Exceptions**: if any coroutine raises, all remaining tasks are cancelled and
    every exception — including any raised while those tasks were being cancelled —
    propagates as a single ``ExceptionGroup``, even when only one task failed.
    One failure is a group of one: the exception type you catch never depends
    on timing. Handle with ``except*``.

    Examples:
        ```python
        async for err in filter_err_unordered([fetch(1), fetch(2), fetch(3)]):
            print(err)
        ```

    """
    src: _Source[Result[T, E]] = _Source(iterable)
    pending: set[asyncio.Task[Result[T, E]]] = set()

    try:
        await _fill_unordered(pending, src, concurrency)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            results, excs = _split_done(done)
            if excs:
                excs.extend(await _drain(pending, src))
                pending = set()
                raise BaseExceptionGroup(_GROUP_MSG, excs)
            for result in results:
                match result:
                    case Ok():
                        pass
                    case Err(e):
                        yield e
                next_item = await _pull(src, pending)
                if next_item is not None:
                    pending.add(asyncio.ensure_future(next_item))
    finally:
        await _cancel_all(pending, src)


async def filter_ok(
    iterable: Iterable[_CoroOrTask[Result[T, E]]] | AsyncIterable[_CoroOrTask[Result[T, E]]],
    *,
    concurrency: int,
) -> AsyncIterator[T]:
    """
    Await coroutines or tasks concurrently, yielding ``Ok`` values in input order.

    Accepts a plain iterable or an async iterable (e.g. an async generator)
    of awaitables — async sources are consumed lazily and closed on exit.

    ``Err`` values are silently skipped.
    Values are yielded in input order — later-completing tasks are buffered until
    all earlier ones have been yielded.

    *concurrency* controls the size of the sliding window of in-flight tasks.
    Unlike ``filter_ok_unordered``, ``None`` is not accepted because the
    reorder buffer would be unbounded.

    If the consumer stops iterating early (``break``, exception, ``aclose()``),
    all in-flight tasks are cancelled and unconsumed coroutines are closed.

    **Exceptions**: if any coroutine raises, all remaining tasks are cancelled and
    every exception — including any raised while those tasks were being cancelled —
    propagates as a single ``ExceptionGroup``, even when only one task failed.
    One failure is a group of one: the exception type you catch never depends
    on timing. Handle with ``except*``.

    Examples:
        ```python
        async for user in filter_ok([fetch(1), fetch(2), fetch(3)], concurrency=4):
            print(user)
        ```

    """
    src: _Source[Result[T, E]] = _Source(iterable)
    pending: set[asyncio.Task[tuple[int, Result[T, E]]]] = set()
    buf: dict[int, Result[T, E]] = {}
    next_yield = 0

    try:
        next_idx = await _fill_indexed(pending, src, concurrency)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            batch, excs = _split_done(done)
            if excs:
                excs.extend(await _drain(pending, src))
                pending = set()
                raise BaseExceptionGroup(_GROUP_MSG, excs)
            for idx, result in batch:
                buf[idx] = result
                next_item = await _pull(src, pending)
                if next_item is not None:
                    pending.add(_wrap_indexed(next_idx, next_item))
                    next_idx += 1

            while next_yield in buf:
                match buf.pop(next_yield):
                    case Ok(value):
                        yield value
                    case Err():
                        pass
                next_yield += 1
    finally:
        await _cancel_all(pending, src)


async def filter_err(
    iterable: Iterable[_CoroOrTask[Result[T, E]]] | AsyncIterable[_CoroOrTask[Result[T, E]]],
    *,
    concurrency: int,
) -> AsyncIterator[E]:
    """
    Await coroutines or tasks concurrently, yielding ``Err`` values in input order.

    Accepts a plain iterable or an async iterable (e.g. an async generator)
    of awaitables — async sources are consumed lazily and closed on exit.

    ``Ok`` values are silently skipped.
    Values are yielded in input order — later-completing tasks are buffered until
    all earlier ones have been yielded.

    *concurrency* controls the size of the sliding window of in-flight tasks.
    Unlike ``filter_err_unordered``, ``None`` is not accepted because the
    reorder buffer would be unbounded.

    If the consumer stops iterating early (``break``, exception, ``aclose()``),
    all in-flight tasks are cancelled and unconsumed coroutines are closed.

    **Exceptions**: if any coroutine raises, all remaining tasks are cancelled and
    every exception — including any raised while those tasks were being cancelled —
    propagates as a single ``ExceptionGroup``, even when only one task failed.
    One failure is a group of one: the exception type you catch never depends
    on timing. Handle with ``except*``.

    Examples:
        ```python
        async for err in filter_err([fetch(1), fetch(2), fetch(3)], concurrency=4):
            print(err)
        ```

    """
    src: _Source[Result[T, E]] = _Source(iterable)
    pending: set[asyncio.Task[tuple[int, Result[T, E]]]] = set()
    buf: dict[int, Result[T, E]] = {}
    next_yield = 0

    try:
        next_idx = await _fill_indexed(pending, src, concurrency)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            batch, excs = _split_done(done)
            if excs:
                excs.extend(await _drain(pending, src))
                pending = set()
                raise BaseExceptionGroup(_GROUP_MSG, excs)
            for idx, result in batch:
                buf[idx] = result
                next_item = await _pull(src, pending)
                if next_item is not None:
                    pending.add(_wrap_indexed(next_idx, next_item))
                    next_idx += 1

            while next_yield in buf:
                match buf.pop(next_yield):
                    case Ok():
                        pass
                    case Err(e):
                        yield e
                next_yield += 1
    finally:
        await _cancel_all(pending, src)


async def try_reduce(
    iterable: Iterable[_CoroOrTask[T]] | AsyncIterable[_CoroOrTask[T]],
    initial: U,
    f: Callable[[U, T], Result[U, E]],
) -> Result[U, E]:
    """
    Await each coroutine or task sequentially, folding with *f* and short-circuiting on ``Err``.

    Accepts a plain iterable or an async iterable (e.g. an async generator)
    of awaitables — async sources are consumed lazily and closed on exit.

    Unlike the async ``collect`` / ``partition`` family, tasks run one at a time —
    each awaited value is passed to *f* before the next is awaited, because the
    accumulator depends on the previous step.

    On short-circuit — and on any exception — remaining tasks are cancelled,
    unconsumed coroutines are closed, and an async-generator source is closed.

    **Exceptions**: unlike the concurrent functions, execution is sequential —
    only one coroutine can fail — so exceptions propagate bare, without an
    ``ExceptionGroup``.

    Examples:
        >>> import asyncio
        >>> async def fetch(i: int) -> int:
        ...     return i
        >>> def safe_add(acc: int, x: int) -> Result[int, str]:
        ...     return Err(f"negative: {x}") if x < 0 else Ok(acc + x)
        >>> asyncio.run(try_reduce([fetch(1), fetch(2)], 0, safe_add))
        Ok(3)
        >>> asyncio.run(try_reduce([fetch(1), fetch(-1), fetch(3)], 0, safe_add))
        Err('negative: -1')

    """
    src: _Source[T] = _Source(iterable)
    acc: U = initial
    try:
        while True:
            item = await src.next_item()
            if item is None:
                break
            value: T = await item
            match f(acc, value):
                case Ok(new_acc):
                    acc = new_acc
                case Err() as err:
                    return err
    finally:
        teardown_excs = await src.aclose()
        if teardown_excs:
            raise teardown_excs[0]
    return Ok(acc)
