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
from functools import partial
from typing import Any, Generic, NoReturn, TypeVar, overload

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


def _finalize_item(item: _CoroOrTask[_R]) -> BaseException | None:
    """
    Cancel *item* if it is a task, close it if it is an unstarted coroutine.

    A raising teardown is returned rather than raised so each caller decides
    its fate: ``_Source.aclose`` collects it into the propagating group,
    while ``_wrap_indexed``'s done-callback re-raises it to the event loop's
    exception handler.
    """
    try:
        if isinstance(item, asyncio.Task):
            item.cancel()
        else:
            item.close()
    except BaseException as exc:  # noqa: BLE001 — the caller decides how to surface it
        return exc
    return None


class _Source(Generic[_R]):
    """
    Uniform lazy pull interface over a sync or async source of awaitables.

    Sync-vs-async is resolved once, at construction: for a sync source
    ``sync_next`` is a plain callable that pulls without awaiting — the hot
    paths call it directly, paying neither an ``isinstance`` check nor a
    coroutine frame per item — while for an async source it is ``None`` and
    items come from ``next_item``.

    ``aclose`` finalizes whatever the source still holds: unconsumed sync
    items are closed (or cancelled, for tasks) and an async-generator source
    is closed with ``aclose()`` so its finalizers run deterministically.
    """

    _it: Iterator[_CoroOrTask[_R]]
    _ait: AsyncIterator[_CoroOrTask[_R]]
    sync_next: Callable[[], _CoroOrTask[_R] | None] | None

    def __init__(
        self,
        iterable: Iterable[_CoroOrTask[_R]] | AsyncIterable[_CoroOrTask[_R]],
    ) -> None:
        if isinstance(iterable, AsyncIterable):
            self._ait = aiter(iterable)
            self.sync_next = None
        else:
            self._it = iter(iterable)
            self.sync_next = partial(next, self._it, None)

    async def next_item(self) -> _CoroOrTask[_R] | None:
        """
        Pull the next item from an async source, or ``None`` once exhausted.

        Sync sources are served by ``sync_next`` instead, which never awaits.
        """
        try:
            return await anext(self._ait)
        except StopAsyncIteration:
            return None

    async def aclose(self) -> list[BaseException]:
        """Finalize the source, returning non-cancellation teardown exceptions."""
        if self.sync_next is not None:
            return [exc for item in self._it if (exc := _finalize_item(item)) is not None]
        aclose = getattr(self._ait, "aclose", None)
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


async def _fail(
    exc: BaseException,
    src: _Source[_R],
    pending: set[asyncio.Task[_S]],
) -> NoReturn:
    """
    Cancel *pending*, finalize *src*, re-raise *exc* inside an ``ExceptionGroup``.

    A raising source is an infrastructure failure: everything — including any
    exception raised during the teardown — propagates as a single group, the
    same contract a raising task follows. Cancellation is not a source
    failure and passes through bare.
    """
    if isinstance(exc, asyncio.CancelledError):
        raise exc
    excs = [exc, *await _drain(pending, src)]
    raise BaseExceptionGroup(_GROUP_MSG, excs) from None


async def _pull(
    src: _Source[_R],
    pending: set[asyncio.Task[_S]],
) -> _CoroOrTask[_R] | None:
    """
    Pull the next item from an async *src*, or ``None`` once it is exhausted.

    A raising source cancels *pending*, finalizes the source, and propagates
    everything as a single ``ExceptionGroup`` (see ``_fail``). Sync sources
    take the non-awaiting ``_pull_sync`` path instead.
    """
    try:
        return await src.next_item()
    except asyncio.CancelledError:
        raise
    except BaseException as exc:  # noqa: BLE001 — re-raised in the group by _fail
        return await _fail(exc, src, pending)


def _pull_sync(
    sync_next: Callable[[], _CoroOrTask[_R] | None],
) -> tuple[_CoroOrTask[_R] | None, BaseException | None]:
    """
    Pull the next item from a sync source without awaiting.

    Returns ``(item, None)`` — ``(None, None)`` once the source is
    exhausted — or ``(None, exc)`` if the source raised: the caller forwards
    *exc* to ``_fail``, so a raising sync source keeps ``_pull``'s exception
    contract without paying a coroutine frame per pull on the happy path.
    """
    try:
        return sync_next(), None
    except BaseException as exc:  # noqa: BLE001 — forwarded to _fail by the caller
        return None, exc


def _check_concurrency(concurrency: int | None) -> None:
    """Reject a concurrency bound that would silently schedule nothing."""
    if concurrency is not None and concurrency < 1:
        msg = f"concurrency must be at least 1 or None (unlimited), got {concurrency}"
        raise ValueError(msg)


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
        exc = _finalize_item(item)
        if exc is not None:
            # A raising teardown propagates out of the done callback to the
            # event loop's exception handler, exactly as an unguarded call
            # would — it must not be silently discarded here.
            raise exc

    task.add_done_callback(_finalize)
    return task


def _wrap_plain(_idx: int, item: _CoroOrTask[_R]) -> asyncio.Task[_R]:
    """Schedule *item* as-is, ignoring its index (unordered pipelines)."""
    return asyncio.ensure_future(item)


async def _fill(
    pending: set[asyncio.Task[_S]],
    src: _Source[_R],
    concurrency: int | None,
    wrap: Callable[[int, _CoroOrTask[_R]], asyncio.Task[_S]],
) -> int:
    """Schedule the initial window of tasks (built by *wrap*) into *pending*; return its size."""
    _check_concurrency(concurrency)
    sync_next = src.sync_next
    idx = 0
    while concurrency is None or idx < concurrency:
        if sync_next is None:
            item = await _pull(src, pending)
        else:
            item, exc = _pull_sync(sync_next)
            if exc is not None:
                await _fail(exc, src, pending)
        if item is None:
            break
        pending.add(wrap(idx, item))
        idx += 1
    return idx


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
    An async source keeps listing and processing interleaved: passing an
    async generator over a paginated API means at most *concurrency* requests
    are in flight, where materialising it first would crawl every page into
    memory before the first item is processed.

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
    sync_next = src.sync_next
    pending: set[asyncio.Task[tuple[int, Result[T, E]]]] = set()
    indexed: dict[int, T] = {}

    try:
        next_idx = await _fill(pending, src, concurrency, _wrap_indexed)
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
                        if sync_next is None:
                            next_item = await _pull(src, pending)
                        else:
                            next_item, exc = _pull_sync(sync_next)
                            if exc is not None:
                                await _fail(exc, src, pending)
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
    the values are combined into a single tuple. Use it to assemble one
    response out of several unrelated calls — the case ``collect`` cannot
    cover, since it needs a homogeneous list. Unlike ``asyncio.gather``, the
    values stay typed instead of collapsing into ``list[Any]``, and an
    expected failure stays a value instead of an exception to be told apart
    from a bug.

    Values are returned in argument order, regardless of completion order.
    Returns the first ``Err`` to complete, cancelling the remaining awaitables;
    if several complete in the same event-loop iteration, the earliest by
    argument order is returned.

    All awaitables are scheduled at once — there is no concurrency limit.

    **Exceptions**: if any coroutine raises, all remaining tasks are cancelled and
    every exception — including any raised while those tasks were being cancelled —
    propagates as a single ``ExceptionGroup``, even when only one task failed.
    One failure is a group of one: the exception type you catch never depends
    on timing. Handle with ``except*``.

    Examples:
        A checkout page needs a profile, a balance and a shipping quote —
        three types, three upstreams, one round of latency:

        >>> import asyncio
        >>> async def load_profile() -> Result[str, str]:
        ...     return Ok("alice")
        >>> async def load_balance() -> Result[int, str]:
        ...     return Ok(2500)
        >>> asyncio.run(zip(load_profile(), load_balance()))
        Ok(('alice', 2500))

        The first ``Err`` to complete wins and the rest are cancelled, so a
        checkout that already failed stops charging the other upstreams:

        >>> async def load_balance_declined() -> Result[int, str]:
        ...     return Err("card declined")
        >>> asyncio.run(zip(load_profile(), load_balance_declined()))
        Err('card declined')

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
    sync_next = src.sync_next
    pending: set[asyncio.Task[tuple[int, Result[T, E]]]] = set()
    indexed: dict[int, Result[T, E]] = {}

    try:
        next_idx = await _fill(pending, src, concurrency, _wrap_indexed)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            batch, excs = _split_done(done)
            if excs:
                excs.extend(await _drain(pending, src))
                pending = set()
                raise BaseExceptionGroup(_GROUP_MSG, excs)
            for idx, result in batch:
                indexed[idx] = result
                if sync_next is None:
                    next_item = await _pull(src, pending)
                else:
                    next_item, exc = _pull_sync(sync_next)
                    if exc is not None:
                        await _fail(exc, src, pending)
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


async def first_ok(
    iterable: Iterable[_CoroOrTask[Result[T, E]]] | AsyncIterable[_CoroOrTask[Result[T, E]]],
    *,
    concurrency: int | None = None,
) -> Result[T, list[E]]:
    """
    Race coroutines or tasks concurrently, returning the first ``Ok`` to complete.

    The dual of ``collect``: ``collect`` is all values or the first error,
    ``first_ok`` is the first value or all errors. Use it when the same thing
    can be fetched from several places — mirrors, replicas, cache tiers:
    ask all of them at once and pay the fastest one's latency instead of the
    preferred one's. The cost of the extra attempts is bounded, since the
    losers are cancelled rather than left running.

    Accepts a plain iterable or an async iterable (e.g. an async generator)
    of awaitables — async sources are consumed lazily and closed on exit.

    The first ``Ok`` to complete wins (if several complete in the same
    event-loop iteration, the earliest by input order is returned): remaining
    in-flight tasks are cancelled and unconsumed items are closed — slower
    alternatives are not awaited once one has succeeded. An early ``Err``
    does not end the race: it is recorded and the race continues. If every
    awaitable completes with ``Err``, returns ``Err`` of every error in input
    order (not completion order); empty input gives ``Err([])``.

    *concurrency* limits how many run at the same time.
    ``None`` means unlimited — all are scheduled at once.

    **Exceptions**: if any coroutine raises, all remaining tasks are cancelled and
    every exception — including any raised while those tasks were being cancelled —
    propagates as a single ``ExceptionGroup``, even when only one task failed.
    One failure is a group of one: the exception type you catch never depends
    on timing. Handle with ``except*``.
    A raising source follows the same contract: in-flight tasks are cancelled
    and the source's exception joins the group.
    A raise wins over the race even while other alternatives are still running:
    it is a bug or an infrastructure failure, not a candidate loser — it is
    never swallowed on the chance that another branch might still succeed.

    Examples:
        >>> import asyncio
        >>> async def download(host: str, latency: float, has_file: bool) -> Result[str, str]:
        ...     await asyncio.sleep(latency)
        ...     return Ok(f"app.tar from {host}") if has_file else Err(f"{host}: 404")

        A fast ``Err`` does not end the race — a mirror that answers "404"
        first must not beat the mirror that has the file:

        >>> asyncio.run(
        ...     first_ok(
        ...         [
        ...             download("ap.cdn", 0.0, has_file=False),
        ...             download("us.cdn", 0.01, has_file=True),
        ...         ],
        ...     ),
        ... )
        Ok('app.tar from us.cdn')

        When every alternative fails, the errors arrive in input order,
        regardless of completion order — the log says which mirror said what:

        >>> asyncio.run(
        ...     first_ok(
        ...         [
        ...             download("us.cdn", 0.01, has_file=False),
        ...             download("ap.cdn", 0.0, has_file=False),
        ...         ],
        ...     ),
        ... )
        Err(['us.cdn: 404', 'ap.cdn: 404'])

    """
    src: _Source[Result[T, E]] = _Source(iterable)
    sync_next = src.sync_next
    pending: set[asyncio.Task[tuple[int, Result[T, E]]]] = set()
    indexed: dict[int, E] = {}

    try:
        next_idx = await _fill(pending, src, concurrency, _wrap_indexed)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            batch, excs = _split_done(done)
            if excs:
                excs.extend(await _drain(pending, src))
                pending = set()
                raise BaseExceptionGroup(_GROUP_MSG, excs)
            batch.sort(key=operator.itemgetter(0))
            # The race is won by any Ok in the batch — check before recording
            # errors or refilling, so no item is pulled after the win.
            for _idx, result in batch:
                match result:
                    case Ok():
                        return result
            for idx, result in batch:
                match result:
                    case Err(e):
                        indexed[idx] = e
                        if sync_next is None:
                            next_item = await _pull(src, pending)
                        else:
                            next_item, exc = _pull_sync(sync_next)
                            if exc is not None:
                                await _fail(exc, src, pending)
                        if next_item is not None:
                            pending.add(_wrap_indexed(next_idx, next_item))
                            next_idx += 1
    finally:
        await _cancel_all(pending, src)

    return Err([indexed[i] for i in range(len(indexed))])


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
    sync_next = src.sync_next
    pending: set[asyncio.Task[Result[T, E]]] = set()

    try:
        await _fill(pending, src, concurrency, _wrap_plain)
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
                if sync_next is None:
                    next_item = await _pull(src, pending)
                else:
                    next_item, exc = _pull_sync(sync_next)
                    if exc is not None:
                        await _fail(exc, src, pending)
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
    sync_next = src.sync_next
    pending: set[asyncio.Task[Result[T, E]]] = set()

    try:
        await _fill(pending, src, concurrency, _wrap_plain)
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
                if sync_next is None:
                    next_item = await _pull(src, pending)
                else:
                    next_item, exc = _pull_sync(sync_next)
                    if exc is not None:
                        await _fail(exc, src, pending)
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
    sync_next = src.sync_next
    pending: set[asyncio.Task[tuple[int, Result[T, E]]]] = set()
    buf: dict[int, Result[T, E]] = {}
    next_yield = 0

    try:
        next_idx = await _fill(pending, src, concurrency, _wrap_indexed)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            batch, excs = _split_done(done)
            if excs:
                excs.extend(await _drain(pending, src))
                pending = set()
                raise BaseExceptionGroup(_GROUP_MSG, excs)
            for idx, result in batch:
                buf[idx] = result
                if sync_next is None:
                    next_item = await _pull(src, pending)
                else:
                    next_item, exc = _pull_sync(sync_next)
                    if exc is not None:
                        await _fail(exc, src, pending)
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
    sync_next = src.sync_next
    pending: set[asyncio.Task[tuple[int, Result[T, E]]]] = set()
    buf: dict[int, Result[T, E]] = {}
    next_yield = 0

    try:
        next_idx = await _fill(pending, src, concurrency, _wrap_indexed)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            batch, excs = _split_done(done)
            if excs:
                excs.extend(await _drain(pending, src))
                pending = set()
                raise BaseExceptionGroup(_GROUP_MSG, excs)
            for idx, result in batch:
                buf[idx] = result
                if sync_next is None:
                    next_item = await _pull(src, pending)
                else:
                    next_item, exc = _pull_sync(sync_next)
                    if exc is not None:
                        await _fail(exc, src, pending)
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
    Exceptions raised during that teardown never mask the fold's outcome: a
    propagating exception stays the exception the caller sees, a normal
    ``Ok`` / ``Err`` return is still returned, and the teardown exception
    itself is suppressed — the same policy the concurrent functions apply on
    their success path.

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
    sync_next = src.sync_next
    acc: U = initial
    try:
        while True:
            item = sync_next() if sync_next is not None else await src.next_item()
            if item is None:
                break
            value: T = await item
            match f(acc, value):
                case Ok(new_acc):
                    acc = new_acc
                case Err() as err:
                    return err
    finally:
        await src.aclose()
    return Ok(acc)
