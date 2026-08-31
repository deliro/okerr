from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Coroutine
from typing import Any, TypeVar

import pytest

from corrode import Err, Ok, async_iterator
from corrode.async_iterator import (
    collect,
    collect_all,
    filter_err,
    filter_err_unordered,
    filter_ok,
    filter_ok_unordered,
    first_ok,
    map_collect,
    map_partition,
    partition,
    try_reduce,
)


async def ok_after(value: int, delay: float = 0.0) -> Ok[int]:
    await asyncio.sleep(delay)
    return Ok(value)


async def err_after(value: str, delay: float = 0.0) -> Err[str]:
    await asyncio.sleep(delay)
    return Err(value)


_R = TypeVar("_R")


class ConcurrencyProbe:
    """Track the peak number of concurrently running awaitables."""

    def __init__(self) -> None:
        self._in_flight = 0
        self.peak = 0

    async def track(self, result: _R) -> _R:
        self._in_flight += 1
        self.peak = max(self.peak, self._in_flight)
        # yield long enough for the window to fill up; the assertion is on
        # the logical peak, not on wall-clock timing
        await asyncio.sleep(0.02)
        self._in_flight -= 1
        return result


class Rendezvous:
    """Deadlock (until timeout) unless *expected* coroutines run concurrently."""

    def __init__(self, expected: int) -> None:
        self._remaining = expected
        self._all_started = asyncio.Event()

    async def wait_all_started(self, result: _R) -> _R:
        self._remaining -= 1
        if self._remaining == 0:
            self._all_started.set()
        await self._all_started.wait()
        return result


# ---------------------------------------------------------------------------
# Basic correctness
# ---------------------------------------------------------------------------


class TestCollectBasic:
    async def test_empty(self) -> None:
        result = await collect([])
        assert result == Ok([])

    async def test_all_ok(self) -> None:
        result = await collect([ok_after(1), ok_after(2), ok_after(3)])
        assert result == Ok([1, 2, 3])

    async def test_single_ok(self) -> None:
        assert await collect([ok_after(42)]) == Ok([42])

    async def test_single_err(self) -> None:
        assert await collect([err_after("boom")]) == Err("boom")

    async def test_first_err_returned(self) -> None:
        # both fail, first to complete wins
        result = await collect(
            [
                err_after("first", delay=0.0),
                err_after("second", delay=0.1),
            ],
        )
        assert result == Err("first")

    async def test_err_among_oks(self) -> None:
        result = await collect([ok_after(1), err_after("bad"), ok_after(3)])
        assert result == Err("bad")


# ---------------------------------------------------------------------------
# concurrency=None (unlimited)
# ---------------------------------------------------------------------------


class TestConcurrencyUnlimited:
    async def test_all_scheduled_at_once(self) -> None:
        # each coroutine blocks until all 5 have started —
        # deadlocks (and hits the timeout) unless they truly run concurrently
        rendezvous = Rendezvous(5)

        async with asyncio.timeout(5):
            result = await collect(
                [rendezvous.wait_all_started(Ok(i)) for i in range(5)],
                concurrency=None,
            )
        assert result == Ok([0, 1, 2, 3, 4])

    async def test_empty(self) -> None:
        assert await collect([], concurrency=None) == Ok([])

    async def test_err_cancels_pending(self) -> None:
        cancelled: list[int] = []

        async def slow_ok(v: int) -> Ok[int]:
            try:
                await asyncio.sleep(10)
                return Ok(v)
            except asyncio.CancelledError:
                cancelled.append(v)
                raise

        result = await collect(
            [err_after("boom", delay=0.0), slow_ok(1), slow_ok(2)],
            concurrency=None,
        )
        assert result == Err("boom")
        assert sorted(cancelled) == [1, 2]


# ---------------------------------------------------------------------------
# concurrency=1 (sequential)
# ---------------------------------------------------------------------------


class TestConcurrencyOne:
    async def test_all_ok(self) -> None:
        result = await collect(
            [ok_after(1), ok_after(2), ok_after(3)],
            concurrency=1,
        )
        assert result == Ok([1, 2, 3])

    async def test_runs_sequentially(self) -> None:
        order: list[int] = []

        async def tracked(v: int) -> Ok[int]:
            order.append(v)
            await asyncio.sleep(0)
            return Ok(v)

        await collect(
            [tracked(1), tracked(2), tracked(3)],
            concurrency=1,
        )
        # with concurrency=1 tasks are consumed one by one in input order
        assert order == [1, 2, 3]

    async def test_err_short_circuits(self) -> None:
        started: list[int] = []

        async def tracked_ok(v: int) -> Ok[int]:
            started.append(v)
            await asyncio.sleep(0)
            return Ok(v)

        result = await collect(
            [err_after("stop"), tracked_ok(1), tracked_ok(2)],
            concurrency=1,
        )
        assert result == Err("stop")
        # subsequent tasks were never started
        assert started == []

    async def test_empty(self) -> None:
        assert await collect([], concurrency=1) == Ok([])


# ---------------------------------------------------------------------------
# concurrency=N (sliding window)
# ---------------------------------------------------------------------------


class TestConcurrencyN:
    async def test_all_ok(self) -> None:
        result = await collect(
            [ok_after(i) for i in range(10)],
            concurrency=3,
        )
        assert result == Ok(list(range(10)))

    async def test_at_most_n_concurrent(self) -> None:
        probe = ConcurrencyProbe()
        await collect(
            [probe.track(Ok(i)) for i in range(8)],
            concurrency=3,
        )
        assert probe.peak <= 3

    async def test_err_cancels_pending_window(self) -> None:
        cancelled: list[int] = []

        async def slow_ok(v: int) -> Ok[int]:
            try:
                await asyncio.sleep(10)
                return Ok(v)
            except asyncio.CancelledError:
                cancelled.append(v)
                raise

        result = await collect(
            [slow_ok(0), slow_ok(1), err_after("boom", delay=0.0), slow_ok(3)],
            concurrency=3,
        )
        assert result == Err("boom")
        # slow_ok(3) was never started (outside initial window of 3)
        assert 3 not in cancelled
        # slow_ok(0) and slow_ok(1) were cancelled
        assert sorted(cancelled) == [0, 1]

    async def test_concurrency_larger_than_input(self) -> None:
        result = await collect(
            [ok_after(i) for i in range(3)],
            concurrency=100,
        )
        assert result == Ok([0, 1, 2])

    async def test_empty(self) -> None:
        assert await collect([], concurrency=4) == Ok([])

    async def test_remaining_coros_not_started_after_err(self) -> None:
        # 6 coroutines, concurrency=3: first window is [0, 1, 2],
        # err fires immediately, coroutines [3, 4, 5] must never start
        started: list[int] = []

        async def slow(v: int) -> Ok[int]:
            started.append(v)
            await asyncio.sleep(10)
            return Ok(v)

        result = await collect(
            [err_after("stop"), slow(1), slow(2), slow(3), slow(4), slow(5)],
            concurrency=3,
        )
        assert result == Err("stop")
        assert all(v <= 2 for v in started)


# ---------------------------------------------------------------------------
# Order guarantee
# ---------------------------------------------------------------------------


class TestOrderGuarantee:
    async def test_order_preserved_with_different_delays(self) -> None:
        # tasks complete in reverse order but result must be in input order
        result = await collect(
            [
                ok_after(1, delay=0.1),
                ok_after(2, delay=0.05),
                ok_after(3, delay=0.0),
            ],
        )
        assert result == Ok([1, 2, 3])

    async def test_order_preserved_with_concurrency(self) -> None:
        result = await collect(
            [ok_after(i, delay=(9 - i) * 0.01) for i in range(10)],
            concurrency=3,
        )
        assert result == Ok(list(range(10)))

    async def test_partition_order_preserved(self) -> None:
        # interleaved ok/err with different delays
        oks, errs = await partition(
            [
                ok_after(1, delay=0.1),
                err_after("a", delay=0.0),
                ok_after(2, delay=0.05),
                err_after("b", delay=0.08),
            ],
        )
        assert oks == [1, 2]
        assert errs == ["a", "b"]


# ---------------------------------------------------------------------------
# Exception propagation
# ---------------------------------------------------------------------------


class TestExceptionPropagation:
    async def test_exception_escapes_as_group_of_one(self) -> None:
        # one failure is a group of one — the caught type never depends on timing
        async def boom() -> Ok[int]:
            raise ValueError("oops")

        with pytest.raises(BaseExceptionGroup) as exc_info:
            await collect([boom()])
        assert exc_info.group_contains(ValueError, match="oops")
        assert len(exc_info.value.exceptions) == 1

    async def test_exception_cancels_pending(self) -> None:
        cancelled: list[int] = []

        async def slow(v: int) -> Ok[int]:
            try:
                await asyncio.sleep(10)
                return Ok(v)
            except asyncio.CancelledError:
                cancelled.append(v)
                raise

        async def boom() -> Ok[int]:
            raise RuntimeError("boom")

        with pytest.raises(BaseExceptionGroup) as exc_info:
            await collect([slow(1), slow(2), boom()])

        assert exc_info.group_contains(RuntimeError, match="boom")
        assert sorted(cancelled) == [1, 2]

    async def test_exception_closes_unconsumed_coros(self) -> None:
        started: list[int] = []

        async def boom() -> Ok[int]:
            raise RuntimeError

        async def never(v: int) -> Ok[int]:
            started.append(v)
            await asyncio.sleep(10)
            return Ok(v)

        with pytest.raises(BaseExceptionGroup):
            await collect([boom(), never(1), never(2)], concurrency=1)

        # with concurrency=1, never(1) and never(2) were never scheduled
        assert started == []

    async def test_exception_not_swallowed_when_err_also_present(self) -> None:
        # if one task raises and another returns Err in the same batch, the exception wins
        async def boom() -> Ok[int]:
            raise RuntimeError("exception")

        with pytest.raises(BaseExceptionGroup) as exc_info:
            await collect([boom(), err_after("also bad")], concurrency=None)
        assert exc_info.group_contains(RuntimeError, match="exception")

    async def test_multiple_exceptions_raise_group(self) -> None:
        # several tasks raising in the same batch propagate together, none discarded
        async def boom(msg: str) -> Ok[int]:
            raise RuntimeError(msg)

        with pytest.raises(BaseExceptionGroup) as exc_info:
            await collect([boom("one"), boom("two")], concurrency=None)

        messages = sorted(str(e) for e in exc_info.value.exceptions)
        assert messages == ["one", "two"]

    async def test_teardown_exception_joins_group(self) -> None:
        # a task that raises during cancellation must not be silently discarded
        async def bad_on_cancel(v: int) -> Ok[int]:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                msg = f"cleanup failed: {v}"
                raise RuntimeError(msg) from None
            return Ok(v)

        async def boom() -> Ok[int]:
            raise ValueError("primary")

        with pytest.raises(BaseExceptionGroup) as exc_info:
            await collect([bad_on_cancel(1), boom()])

        names = sorted(type(e).__name__ for e in exc_info.value.exceptions)
        assert names == ["RuntimeError", "ValueError"]

    async def test_cancelling_collect_cancels_children(self) -> None:
        # cancelling the caller's task must not leak the inner tasks
        cancelled: list[int] = []

        async def slow(v: int) -> Ok[int]:
            try:
                await asyncio.sleep(10)
                return Ok(v)
            except asyncio.CancelledError:
                cancelled.append(v)
                raise

        task = asyncio.create_task(collect([slow(1), slow(2)]))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert sorted(cancelled) == [1, 2]


# ---------------------------------------------------------------------------
# create_task inputs
# ---------------------------------------------------------------------------


class TestCreateTaskInputs:
    async def test_all_ok_with_tasks(self) -> None:
        tasks = [asyncio.create_task(ok_after(i)) for i in range(4)]
        result = await collect(tasks)
        assert result == Ok([0, 1, 2, 3])

    async def test_err_cancels_out_of_window_tasks(self) -> None:
        # tasks are already running before collect is called
        cancelled = []

        async def slow(v: int) -> Ok[int]:
            try:
                await asyncio.sleep(10)
                return Ok(v)
            except asyncio.CancelledError:
                cancelled.append(v)
                raise

        tasks = [asyncio.create_task(slow(i)) for i in range(4)]
        await asyncio.sleep(0)  # let tasks start

        result = await collect(
            [err_after("stop"), *tasks],
            concurrency=2,
        )
        await asyncio.sleep(0)  # let cancellations propagate
        assert result == Err("stop")
        # all tasks are already-running Tasks (not plain coroutines),
        # so _cancel_all must call .cancel() on them, not .close()
        assert sorted(cancelled) == [0, 1, 2, 3]
        assert all(t.done() for t in tasks)

    async def test_mixed_coros_and_tasks(self) -> None:
        task = asyncio.create_task(ok_after(1))
        result = await collect([ok_after(0), task, ok_after(2)])
        assert result == Ok([0, 1, 2])


# ---------------------------------------------------------------------------
# zip
# ---------------------------------------------------------------------------


async def str_after(value: str, delay: float = 0.0) -> Ok[str]:
    await asyncio.sleep(delay)
    return Ok(value)


class TestZip:
    async def test_all_ok_arity_2(self) -> None:
        result = await async_iterator.zip(ok_after(1), str_after("a"))
        assert result == Ok((1, "a"))

    async def test_all_ok_arity_5(self) -> None:
        result = await async_iterator.zip(
            ok_after(1),
            str_after("b"),
            ok_after(3),
            str_after("d"),
            ok_after(5),
        )
        assert result == Ok((1, "b", 3, "d", 5))

    async def test_argument_order_regardless_of_completion_order(self) -> None:
        # the second awaitable completes first, but the tuple follows argument order
        result = await async_iterator.zip(
            ok_after(1, delay=0.1),
            str_after("a", delay=0.0),
            ok_after(3, delay=0.05),
        )
        assert result == Ok((1, "a", 3))

    async def test_first_completing_err_wins(self) -> None:
        result = await async_iterator.zip(
            err_after("slow", delay=0.1),
            err_after("fast", delay=0.0),
        )
        assert result == Err("fast")

    async def test_err_cancels_remaining(self) -> None:
        cancelled: list[int] = []

        async def slow_ok(v: int) -> Ok[int]:
            try:
                await asyncio.sleep(10)
                return Ok(v)
            except asyncio.CancelledError:
                cancelled.append(v)
                raise

        result = await async_iterator.zip(
            slow_ok(1),
            err_after("boom", delay=0.0),
            slow_ok(2),
        )
        assert result == Err("boom")
        assert sorted(cancelled) == [1, 2]

    async def test_exception_raises_group_and_cancels_rest(self) -> None:
        cancelled: list[int] = []

        async def slow(v: int) -> Ok[int]:
            try:
                await asyncio.sleep(10)
                return Ok(v)
            except asyncio.CancelledError:
                cancelled.append(v)
                raise

        async def boom() -> Ok[int]:
            raise ValueError("oops")

        with pytest.raises(BaseExceptionGroup) as exc_info:
            await async_iterator.zip(slow(1), boom())

        assert exc_info.group_contains(ValueError, match="oops")
        assert len(exc_info.value.exceptions) == 1
        assert cancelled == [1]

    async def test_works_with_tasks(self) -> None:
        task_a = asyncio.create_task(ok_after(1))
        task_b = asyncio.create_task(str_after("a"))
        result = await async_iterator.zip(task_a, task_b)
        assert result == Ok((1, "a"))

    async def test_mixed_coros_and_tasks(self) -> None:
        task = asyncio.create_task(str_after("a"))
        result = await async_iterator.zip(ok_after(1), task, ok_after(3))
        assert result == Ok((1, "a", 3))


# ---------------------------------------------------------------------------
# partition
# ---------------------------------------------------------------------------


class TestPartition:
    async def test_empty(self) -> None:
        assert await partition([]) == ([], [])

    async def test_all_ok(self) -> None:
        oks, errs = await partition([ok_after(i) for i in range(3)])
        assert oks == [0, 1, 2]
        assert errs == []

    async def test_all_err(self) -> None:
        oks, errs = await partition([err_after(str(i)) for i in range(3)])
        assert oks == []
        assert errs == ["0", "1", "2"]

    async def test_mixed(self) -> None:
        oks, errs = await partition(
            [
                ok_after(1),
                err_after("a"),
                ok_after(2),
                err_after("b"),
            ],
        )
        assert oks == [1, 2]
        assert errs == ["a", "b"]

    async def test_consumes_all_no_short_circuit(self) -> None:
        completed = []

        async def tracked(v: int) -> Ok[int]:
            await asyncio.sleep(0)
            completed.append(v)
            return Ok(v)

        await partition(
            [err_after("e"), tracked(1), tracked(2), tracked(3)],
        )
        assert sorted(completed) == [1, 2, 3]

    async def test_concurrency_none(self) -> None:
        # deadlocks (and hits the timeout) unless all 5 run concurrently
        rendezvous = Rendezvous(5)

        async with asyncio.timeout(5):
            oks, errs = await partition(
                [rendezvous.wait_all_started(Ok(i)) for i in range(5)],
                concurrency=None,
            )
        assert oks == list(range(5))
        assert errs == []

    async def test_concurrency_one(self) -> None:
        order: list[int] = []

        async def tracked(v: int) -> Ok[int]:
            order.append(v)
            await asyncio.sleep(0)
            return Ok(v)

        await partition(
            [tracked(i) for i in range(4)],
            concurrency=1,
        )
        assert order == [0, 1, 2, 3]

    async def test_concurrency_n_at_most(self) -> None:
        probe = ConcurrencyProbe()
        await partition(
            [probe.track(Ok(i)) for i in range(8)],
            concurrency=3,
        )
        assert probe.peak <= 3

    async def test_with_tasks(self) -> None:
        tasks = [asyncio.create_task(ok_after(i)) for i in range(3)]
        oks, errs = await partition(tasks)
        assert oks == [0, 1, 2]
        assert errs == []

    async def test_exception_propagates(self) -> None:
        async def boom() -> Ok[int]:
            raise RuntimeError("oops")

        with pytest.raises(BaseExceptionGroup) as exc_info:
            await partition([boom(), ok_after(1)])
        assert exc_info.group_contains(RuntimeError, match="oops")

    async def test_exception_cancels_pending(self) -> None:
        cancelled: list[int] = []

        async def slow(v: int) -> Ok[int]:
            try:
                await asyncio.sleep(10)
                return Ok(v)
            except asyncio.CancelledError:
                cancelled.append(v)
                raise

        async def boom() -> Ok[int]:
            raise RuntimeError("boom")

        with pytest.raises(BaseExceptionGroup):
            await partition([slow(1), slow(2), boom()])

        assert sorted(cancelled) == [1, 2]

    async def test_exception_drains_multiple_done(self) -> None:
        # two tasks raise simultaneously — both are retrieved and propagate as a group
        async def boom() -> Ok[int]:
            raise RuntimeError("boom")

        with pytest.raises(BaseExceptionGroup):
            await partition([boom(), boom()], concurrency=None)

    async def test_exception_closes_unconsumed_coros(self) -> None:
        started: list[int] = []

        async def boom() -> Ok[int]:
            raise RuntimeError

        async def never(v: int) -> Ok[int]:
            started.append(v)
            await asyncio.sleep(10)
            return Ok(v)

        with pytest.raises(BaseExceptionGroup):
            await partition([boom(), never(1), never(2)], concurrency=1)

        assert started == []


# ---------------------------------------------------------------------------
# map_collect
# ---------------------------------------------------------------------------


class TestMapCollect:
    async def test_all_ok(self) -> None:
        async def double(x: int) -> Ok[int]:
            await asyncio.sleep(0)
            return Ok(x * 2)

        result = await map_collect(range(4), double)
        assert result == Ok([0, 2, 4, 6])

    async def test_empty(self) -> None:
        async def double(x: int) -> Ok[int]:
            return Ok(x * 2)

        assert await map_collect([], double) == Ok([])

    async def test_first_err_short_circuits(self) -> None:
        called: list[int] = []

        async def maybe_fail(x: int) -> Ok[int] | Err[str]:
            called.append(x)
            await asyncio.sleep(0.01 * x)
            if x == 0:
                return Err("zero")
            return Ok(x)

        result = await map_collect(range(4), maybe_fail, concurrency=1)
        assert result == Err("zero")
        assert called == [0]

    async def test_concurrency_respected(self) -> None:
        probe = ConcurrencyProbe()
        await map_collect(range(8), lambda x: probe.track(Ok(x)), concurrency=3)
        assert probe.peak <= 3

    async def test_exception_propagates(self) -> None:
        async def boom(_x: int) -> Ok[int]:
            raise RuntimeError("oops")

        with pytest.raises(BaseExceptionGroup) as exc_info:
            await map_collect([1, 2, 3], boom, concurrency=1)
        assert exc_info.group_contains(RuntimeError, match="oops")

    async def test_simultaneous_exceptions_raise_group(self) -> None:
        async def boom(_x: int) -> Ok[int]:
            raise RuntimeError("oops")

        with pytest.raises(BaseExceptionGroup):
            await map_collect([1, 2, 3], boom)

    async def test_order_preserved(self) -> None:
        async def fetch(x: int) -> Ok[int]:
            await asyncio.sleep((10 - x) * 0.01)
            return Ok(x * 10)

        result = await map_collect(range(5), fetch)
        assert result == Ok([0, 10, 20, 30, 40])


# ---------------------------------------------------------------------------
# filter_ok_unordered
# ---------------------------------------------------------------------------


class TestFilterOkUnordered:
    async def test_yields_ok_values(self) -> None:
        results = [
            v async for v in filter_ok_unordered([ok_after(1), err_after("x"), ok_after(2)])
        ]
        assert sorted(results) == [1, 2]

    async def test_empty(self) -> None:
        assert [v async for v in filter_ok_unordered([])] == []

    async def test_all_err_yields_nothing(self) -> None:
        assert [v async for v in filter_ok_unordered([err_after("a"), err_after("b")])] == []

    async def test_yields_as_completed(self) -> None:
        # fast completes before slow even though slow comes first in input
        order: list[int] = []
        async for v in filter_ok_unordered([ok_after(1, delay=0.1), ok_after(2, delay=0.0)]):
            order.append(v)
        assert order == [2, 1]

    async def test_concurrency_respected(self) -> None:
        probe = ConcurrencyProbe()
        async for _ in filter_ok_unordered([probe.track(Ok(i)) for i in range(8)], concurrency=3):
            pass
        assert probe.peak <= 3

    async def test_exception_propagates(self) -> None:
        async def boom() -> Ok[int]:
            raise RuntimeError("oops")

        with pytest.raises(BaseExceptionGroup) as exc_info:
            async for _ in filter_ok_unordered([boom(), ok_after(1)]):
                pass
        assert exc_info.group_contains(RuntimeError, match="oops")


# ---------------------------------------------------------------------------
# filter_err_unordered
# ---------------------------------------------------------------------------


class TestFilterErrUnordered:
    async def test_yields_err_values(self) -> None:
        results = [
            e async for e in filter_err_unordered([ok_after(1), err_after("x"), err_after("y")])
        ]
        assert sorted(results) == ["x", "y"]

    async def test_empty(self) -> None:
        assert [e async for e in filter_err_unordered([])] == []

    async def test_all_ok_yields_nothing(self) -> None:
        assert [e async for e in filter_err_unordered([ok_after(1), ok_after(2)])] == []

    async def test_yields_as_completed(self) -> None:
        order: list[str] = []
        async for e in filter_err_unordered(
            [err_after("slow", delay=0.1), err_after("fast", delay=0.0)],
        ):
            order.append(e)
        assert order == ["fast", "slow"]

    async def test_concurrency_respected(self) -> None:
        probe = ConcurrencyProbe()
        async for _ in filter_err_unordered(
            [probe.track(Err(i)) for i in range(8)],
            concurrency=3,
        ):
            pass
        assert probe.peak <= 3

    async def test_exception_propagates(self) -> None:
        async def boom() -> Err[str]:
            raise RuntimeError("oops")

        with pytest.raises(BaseExceptionGroup) as exc_info:
            async for _ in filter_err_unordered([boom(), err_after("x")]):
                pass
        assert exc_info.group_contains(RuntimeError, match="oops")


# ---------------------------------------------------------------------------
# filter_ok (ordered)
# ---------------------------------------------------------------------------


class TestFilterOk:
    async def test_yields_ok_values(self) -> None:
        results = [
            v
            async for v in filter_ok(
                [ok_after(1), err_after("x"), ok_after(2)],
                concurrency=4,
            )
        ]
        assert results == [1, 2]

    async def test_empty(self) -> None:
        assert [v async for v in filter_ok([], concurrency=4)] == []

    async def test_all_err_yields_nothing(self) -> None:
        assert [
            v
            async for v in filter_ok(
                [err_after("a"), err_after("b")],
                concurrency=4,
            )
        ] == []

    async def test_order_preserved(self) -> None:
        # fast completes before slow, but output must follow input order
        order: list[int] = []
        async for v in filter_ok(
            [ok_after(1, delay=0.1), ok_after(2, delay=0.0)],
            concurrency=4,
        ):
            order.append(v)
        assert order == [1, 2]

    async def test_err_skipped_in_order(self) -> None:
        results = [
            v
            async for v in filter_ok(
                [ok_after(1), err_after("x"), ok_after(3)],
                concurrency=4,
            )
        ]
        assert results == [1, 3]

    async def test_concurrency_respected(self) -> None:
        probe = ConcurrencyProbe()
        async for _ in filter_ok([probe.track(Ok(i)) for i in range(8)], concurrency=3):
            pass
        assert probe.peak <= 3

    async def test_exception_propagates(self) -> None:
        async def boom() -> Ok[int]:
            raise RuntimeError("oops")

        with pytest.raises(BaseExceptionGroup) as exc_info:
            async for _ in filter_ok([boom(), ok_after(1)], concurrency=4):
                pass
        assert exc_info.group_contains(RuntimeError, match="oops")

    async def test_concurrency_one_sequential(self) -> None:
        order: list[int] = []

        async def tracked(v: int) -> Ok[int]:
            order.append(v)
            await asyncio.sleep(0)
            return Ok(v)

        async for _ in filter_ok([tracked(i) for i in range(4)], concurrency=1):
            pass
        assert order == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# filter_err (ordered)
# ---------------------------------------------------------------------------


class TestFilterErr:
    async def test_yields_err_values(self) -> None:
        results = [
            e
            async for e in filter_err(
                [ok_after(1), err_after("x"), err_after("y")],
                concurrency=4,
            )
        ]
        assert results == ["x", "y"]

    async def test_empty(self) -> None:
        assert [e async for e in filter_err([], concurrency=4)] == []

    async def test_all_ok_yields_nothing(self) -> None:
        assert [
            e
            async for e in filter_err(
                [ok_after(1), ok_after(2)],
                concurrency=4,
            )
        ] == []

    async def test_order_preserved(self) -> None:
        order: list[str] = []
        async for e in filter_err(
            [err_after("slow", delay=0.1), err_after("fast", delay=0.0)],
            concurrency=4,
        ):
            order.append(e)
        assert order == ["slow", "fast"]

    async def test_ok_skipped_in_order(self) -> None:
        results = [
            e
            async for e in filter_err(
                [err_after("a"), ok_after(1), err_after("b")],
                concurrency=4,
            )
        ]
        assert results == ["a", "b"]

    async def test_concurrency_respected(self) -> None:
        probe = ConcurrencyProbe()
        async for _ in filter_err([probe.track(Err(i)) for i in range(8)], concurrency=3):
            pass
        assert probe.peak <= 3

    async def test_exception_propagates(self) -> None:
        async def boom() -> Err[str]:
            raise RuntimeError("oops")

        with pytest.raises(BaseExceptionGroup) as exc_info:
            async for _ in filter_err([boom(), err_after("x")], concurrency=4):
                pass
        assert exc_info.group_contains(RuntimeError, match="oops")


# ---------------------------------------------------------------------------
# Early consumer exit (break / aclose) must not leak tasks
# ---------------------------------------------------------------------------


class TestEarlyConsumerExit:
    async def _slow(self, v: int, cancelled: list[int]) -> Ok[int]:
        try:
            await asyncio.sleep(10)
            return Ok(v)
        except asyncio.CancelledError:
            cancelled.append(v)
            raise

    async def test_filter_ok_unordered_break_cancels_pending(self) -> None:
        cancelled: list[int] = []
        agen = filter_ok_unordered(
            [
                ok_after(0),
                self._slow(1, cancelled),
                self._slow(2, cancelled),
            ],
        )
        async for _ in agen:
            break
        await agen.aclose()
        assert sorted(cancelled) == [1, 2]
        assert len(asyncio.all_tasks()) == 1  # only the test's own task remains

    async def test_filter_err_unordered_break_cancels_pending(self) -> None:
        cancelled: list[int] = []
        agen = filter_err_unordered(
            [
                err_after("x"),
                self._slow(1, cancelled),
                self._slow(2, cancelled),
            ],
        )
        async for _ in agen:
            break
        await agen.aclose()
        assert sorted(cancelled) == [1, 2]

    async def test_filter_ok_break_cancels_pending(self) -> None:
        cancelled: list[int] = []
        agen = filter_ok(
            [ok_after(0), self._slow(1, cancelled), self._slow(2, cancelled)],
            concurrency=4,
        )
        async for _ in agen:
            break
        await agen.aclose()
        assert sorted(cancelled) == [1, 2]

    async def test_filter_err_break_cancels_pending(self) -> None:
        cancelled: list[int] = []
        agen = filter_err(
            [err_after("x"), self._slow(1, cancelled), self._slow(2, cancelled)],
            concurrency=4,
        )
        async for _ in agen:
            break
        await agen.aclose()
        assert sorted(cancelled) == [1, 2]

    async def test_break_closes_unconsumed_coros(self) -> None:
        # coroutines outside the concurrency window are closed, not leaked
        started: list[int] = []

        async def tracked(v: int) -> Ok[int]:
            started.append(v)
            await asyncio.sleep(10)
            return Ok(v)

        unconsumed = tracked(9)
        agen = filter_ok_unordered([ok_after(0), unconsumed], concurrency=1)
        async for _ in agen:
            break
        await agen.aclose()
        assert started == []
        assert inspect.getcoroutinestate(unconsumed) == "CORO_CLOSED"


# ---------------------------------------------------------------------------
# collect_all
# ---------------------------------------------------------------------------


class TestCollectAll:
    async def test_all_ok(self) -> None:
        assert await collect_all([ok_after(1), ok_after(2)]) == Ok([1, 2])

    async def test_empty(self) -> None:
        assert await collect_all([]) == Ok([])

    async def test_accumulates_all_errors_in_input_order(self) -> None:
        result = await collect_all(
            [
                ok_after(1),
                err_after("a", delay=0.05),
                ok_after(2),
                err_after("b", delay=0.0),
            ],
        )
        assert result == Err(["a", "b"])

    async def test_never_short_circuits(self) -> None:
        completed: list[int] = []

        async def tracked(v: int) -> Ok[int]:
            await asyncio.sleep(0)
            completed.append(v)
            return Ok(v)

        await collect_all([err_after("e"), tracked(1), tracked(2)])
        assert sorted(completed) == [1, 2]

    async def test_concurrency_respected(self) -> None:
        probe = ConcurrencyProbe()
        await collect_all([probe.track(Ok(i)) for i in range(8)], concurrency=3)
        assert probe.peak <= 3


# ---------------------------------------------------------------------------
# first_ok
# ---------------------------------------------------------------------------


class TestFirstOk:
    async def test_empty(self) -> None:
        assert await first_ok([]) == Err([])

    async def test_first_ok_to_complete_wins(self) -> None:
        # completion order is inverted: the slow first element loses the race
        result = await first_ok([ok_after(1, delay=0.1), ok_after(2, delay=0.0)])
        assert result == Ok(2)

    async def test_early_err_does_not_end_the_race(self) -> None:
        # the fast Err completes first, but the slow Ok still wins
        result = await first_ok([err_after("fast", delay=0.0), ok_after(1, delay=0.05)])
        assert result == Ok(1)

    async def test_all_err_gives_input_order(self) -> None:
        # completion order is shuffled, but the errors follow input order
        result = await first_ok(
            [
                err_after("a", delay=0.05),
                err_after("b", delay=0.0),
                err_after("c", delay=0.02),
            ],
        )
        assert result == Err(["a", "b", "c"])

    async def test_winner_cancels_losers(self) -> None:
        cancelled: list[int] = []

        async def slow_ok(v: int) -> Ok[int]:
            try:
                await asyncio.sleep(10)
                return Ok(v)
            except asyncio.CancelledError:
                cancelled.append(v)
                raise

        result = await first_ok([slow_ok(1), ok_after(0, delay=0.0), slow_ok(2)])
        assert result == Ok(0)
        assert sorted(cancelled) == [1, 2]

    async def test_concurrency_respected(self) -> None:
        probe = ConcurrencyProbe()
        # all Err so the race is never won early and every item runs
        result = await first_ok([probe.track(Err(i)) for i in range(8)], concurrency=3)
        assert result == Err(list(range(8)))
        assert probe.peak <= 3

    async def test_early_ok_stops_consuming_source(self) -> None:
        pulled: list[int] = []

        async def source() -> AsyncIterator[ResultCoro]:
            for i in range(100):
                pulled.append(i)
                if i == 0:
                    yield ok_after(0)
                else:
                    yield ok_after(i, delay=10)

        result = await first_ok(source(), concurrency=3)
        assert result == Ok(0)
        # only the initial window of 3 was ever pulled from the source
        assert pulled == [0, 1, 2]

    async def test_async_generator_source_closed_on_early_win(self) -> None:
        closed = False

        async def source() -> AsyncIterator[ResultCoro]:
            nonlocal closed
            try:
                yield ok_after(0)
                yield ok_after(1, delay=10)
                yield ok_after(2, delay=10)
            finally:
                closed = True

        result = await first_ok(source(), concurrency=2)
        assert result == Ok(0)
        assert closed

    async def test_exception_wins_over_the_race_as_group_of_one(self) -> None:
        # a raise is not a candidate loser: it cancels the race even though
        # a slow alternative might still have succeeded
        cancelled: list[int] = []

        async def slow_ok(v: int) -> Ok[int]:
            try:
                await asyncio.sleep(10)
                return Ok(v)
            except asyncio.CancelledError:
                cancelled.append(v)
                raise

        async def boom() -> Ok[int]:
            raise ValueError("oops")

        with pytest.raises(BaseExceptionGroup) as exc_info:
            await first_ok([slow_ok(1), boom()])

        assert exc_info.group_contains(ValueError, match="oops")
        assert len(exc_info.value.exceptions) == 1
        assert cancelled == [1]


# ---------------------------------------------------------------------------
# map_partition
# ---------------------------------------------------------------------------


class TestMapPartition:
    async def test_mixed(self) -> None:
        async def check(v: int) -> Ok[int] | Err[str]:
            await asyncio.sleep(0)
            return Ok(v) if v > 0 else Err(f"bad: {v}")

        oks, errs = await map_partition([1, -1, 2, -2], check)
        assert oks == [1, 2]
        assert errs == ["bad: -1", "bad: -2"]

    async def test_empty(self) -> None:
        async def check(v: int) -> Ok[int]:
            return Ok(v)

        assert await map_partition([], check) == ([], [])

    async def test_concurrency_respected(self) -> None:
        probe = ConcurrencyProbe()
        await map_partition(range(8), lambda x: probe.track(Ok(x)), concurrency=3)
        assert probe.peak <= 3


# ---------------------------------------------------------------------------
# try_reduce
# ---------------------------------------------------------------------------


async def int_after(value: int, delay: float = 0.0) -> int:
    await asyncio.sleep(delay)
    return value


class TestTryReduce:
    async def test_all_ok(self) -> None:
        def add(acc: int, x: int) -> Ok[int]:
            return Ok(acc + x)

        result = await try_reduce([int_after(1), int_after(2), int_after(3)], 0, add)
        assert result == Ok(6)

    async def test_empty(self) -> None:
        def add(acc: int, x: int) -> Ok[int]:
            return Ok(acc + x)

        result = await try_reduce([], 42, add)
        assert result == Ok(42)

    async def test_short_circuits_on_err(self) -> None:
        called: list[int] = []

        def maybe_fail(acc: int, x: int) -> Ok[int] | Err[str]:
            called.append(x)
            if x < 0:
                return Err(f"negative: {x}")
            return Ok(acc + x)

        result = await try_reduce(
            [int_after(1), int_after(-1), int_after(3)],
            0,
            maybe_fail,
        )
        assert result == Err("negative: -1")
        assert called == [1, -1]

    async def test_accumulator_threads_through(self) -> None:
        def multiply(acc: int, x: int) -> Ok[int]:
            return Ok(acc * x)

        result = await try_reduce(
            [int_after(2), int_after(3), int_after(4)],
            1,
            multiply,
        )
        assert result == Ok(24)

    async def test_exception_propagates(self) -> None:
        async def boom() -> int:
            raise RuntimeError("oops")

        def f(acc: int, x: int) -> Ok[int]:
            return Ok(acc + x)

        with pytest.raises(RuntimeError, match="oops"):
            await try_reduce([boom()], 0, f)

    async def test_sequential_execution(self) -> None:
        # try_reduce must be sequential: each item is awaited before calling f
        order: list[int] = []

        async def tracked(v: int) -> int:
            order.append(v)
            await asyncio.sleep(0)
            return v

        def f(acc: int, x: int) -> Ok[int]:
            return Ok(acc + x)

        await try_reduce([tracked(1), tracked(2), tracked(3)], 0, f)
        assert order == [1, 2, 3]

    async def test_exception_closes_remaining_coros(self) -> None:
        async def boom() -> int:
            raise RuntimeError("oops")

        def f(acc: int, x: int) -> Ok[int]:
            return Ok(acc + x)

        remaining = int_after(1)
        with pytest.raises(RuntimeError, match="oops"):
            await try_reduce([boom(), remaining], 0, f)
        assert inspect.getcoroutinestate(remaining) == "CORO_CLOSED"

    async def test_err_cancels_remaining_tasks(self) -> None:
        def fail(_acc: int, x: int) -> Err[str]:
            return Err(f"nope: {x}")

        remaining = asyncio.create_task(int_after(2, delay=10))
        result = await try_reduce([int_after(1), remaining], 0, fail)
        await asyncio.sleep(0)
        assert result == Err("nope: 1")
        assert remaining.cancelled()

    async def test_err_closes_remaining_coros(self) -> None:
        def fail(_acc: int, x: int) -> Err[str]:
            return Err(f"nope: {x}")

        remaining = int_after(2)
        result = await try_reduce([int_after(1), remaining], 0, fail)
        assert result == Err("nope: 1")
        assert inspect.getcoroutinestate(remaining) == "CORO_CLOSED"


# ---------------------------------------------------------------------------
# Async iterable sources
# ---------------------------------------------------------------------------

ResultCoro = Coroutine[Any, Any, Ok[int] | Err[str]]
IntCoro = Coroutine[Any, Any, int]


class TestAsyncIterableSources:
    async def _slow(self, v: int, cancelled: list[int]) -> Ok[int]:
        try:
            await asyncio.sleep(10)
            return Ok(v)
        except asyncio.CancelledError:
            cancelled.append(v)
            raise

    async def test_collect_all_ok(self) -> None:
        async def source() -> AsyncIterator[ResultCoro]:
            for i in range(5):
                yield ok_after(i)

        assert await collect(source()) == Ok(list(range(5)))

    async def test_collect_first_err_short_circuits(self) -> None:
        async def source() -> AsyncIterator[ResultCoro]:
            yield ok_after(1)
            yield err_after("bad")
            yield ok_after(3)

        assert await collect(source(), concurrency=1) == Err("bad")

    async def test_collect_err_stops_consuming_source(self) -> None:
        yielded: list[int] = []

        async def source() -> AsyncIterator[ResultCoro]:
            for i in range(100):
                yielded.append(i)
                if i == 0:
                    yield err_after("stop")
                else:
                    yield ok_after(i, delay=10)

        result = await collect(source(), concurrency=3)
        assert result == Err("stop")
        # only the initial window of 3 was ever pulled from the source
        assert yielded == [0, 1, 2]

    async def test_collect_concurrency_respected(self) -> None:
        probe = ConcurrencyProbe()

        async def source() -> AsyncIterator[ResultCoro]:
            for i in range(8):
                yield probe.track(Ok(i))

        result = await collect(source(), concurrency=3)
        assert result == Ok(list(range(8)))
        assert probe.peak <= 3

    async def test_collect_all_accumulates_errors(self) -> None:
        async def source() -> AsyncIterator[ResultCoro]:
            yield ok_after(1)
            yield err_after("a")
            yield err_after("b")

        assert await collect_all(source()) == Err(["a", "b"])

    async def test_collect_all_ok_values(self) -> None:
        async def source() -> AsyncIterator[ResultCoro]:
            yield ok_after(1)
            yield ok_after(2)

        assert await collect_all(source()) == Ok([1, 2])

    async def test_partition_mixed(self) -> None:
        async def source() -> AsyncIterator[ResultCoro]:
            yield ok_after(1)
            yield err_after("x")
            yield ok_after(2)

        assert await partition(source()) == ([1, 2], ["x"])

    async def test_map_collect(self) -> None:
        async def elements() -> AsyncIterator[int]:
            for i in range(4):
                yield i

        async def double(x: int) -> Ok[int]:
            await asyncio.sleep(0)
            return Ok(x * 2)

        assert await map_collect(elements(), double) == Ok([0, 2, 4, 6])

    async def test_map_collect_err_short_circuits_and_closes_source(self) -> None:
        consumed: list[int] = []
        closed = False

        async def elements() -> AsyncIterator[int]:
            nonlocal closed
            try:
                for i in range(100):
                    consumed.append(i)
                    yield i
            finally:
                closed = True

        async def check(x: int) -> Ok[int] | Err[str]:
            if x == 0:
                return Err("zero")
            await asyncio.sleep(10)
            return Ok(x)

        result = await map_collect(elements(), check, concurrency=2)
        assert result == Err("zero")
        assert consumed == [0, 1]
        assert closed

    async def test_map_partition(self) -> None:
        async def elements() -> AsyncIterator[int]:
            for i in (1, -1, 2):
                yield i

        async def check(v: int) -> Ok[int] | Err[str]:
            await asyncio.sleep(0)
            return Ok(v) if v > 0 else Err(f"bad: {v}")

        assert await map_partition(elements(), check) == ([1, 2], ["bad: -1"])

    async def test_filter_ok_ordered(self) -> None:
        async def source() -> AsyncIterator[ResultCoro]:
            yield ok_after(1, delay=0.05)
            yield err_after("x")
            yield ok_after(2, delay=0.0)

        results = [v async for v in filter_ok(source(), concurrency=3)]
        assert results == [1, 2]

    async def test_filter_err_ordered(self) -> None:
        async def source() -> AsyncIterator[ResultCoro]:
            yield err_after("slow", delay=0.05)
            yield ok_after(1)
            yield err_after("fast", delay=0.0)

        results = [e async for e in filter_err(source(), concurrency=3)]
        assert results == ["slow", "fast"]

    async def test_filter_ok_concurrency_respected(self) -> None:
        probe = ConcurrencyProbe()

        async def source() -> AsyncIterator[ResultCoro]:
            for i in range(8):
                yield probe.track(Ok(i))

        async for _ in filter_ok(source(), concurrency=3):
            pass
        assert probe.peak <= 3

    async def test_filter_ok_unordered_completion_order(self) -> None:
        async def source() -> AsyncIterator[ResultCoro]:
            yield ok_after(1, delay=0.05)
            yield err_after("x")
            yield ok_after(2, delay=0.0)

        results = [v async for v in filter_ok_unordered(source())]
        assert results == [2, 1]

    async def test_filter_err_unordered_completion_order(self) -> None:
        async def source() -> AsyncIterator[ResultCoro]:
            yield err_after("slow", delay=0.05)
            yield ok_after(1)
            yield err_after("fast", delay=0.0)

        results = [e async for e in filter_err_unordered(source())]
        assert results == ["fast", "slow"]

    async def test_filter_ok_break_closes_source_and_cancels_tasks(self) -> None:
        cancelled: list[int] = []
        created: list[Coroutine[Any, Any, Ok[int]]] = []
        closed = False

        async def source() -> AsyncIterator[ResultCoro]:
            nonlocal closed
            i = 0
            try:
                yield ok_after(0)
                while True:
                    i += 1
                    coro = self._slow(i, cancelled)
                    created.append(coro)
                    yield coro
            finally:
                closed = True

        agen = filter_ok(source(), concurrency=2)
        async for _ in agen:
            break
        await agen.aclose()
        assert closed
        # slow(1) was in flight and got cancelled; slow(2) was pulled during a
        # refill but never started, so it is closed rather than cancelled
        assert cancelled == [1]
        assert [inspect.getcoroutinestate(c) for c in created] == ["CORO_CLOSED", "CORO_CLOSED"]
        assert len(asyncio.all_tasks()) == 1  # only the test's own task remains

    async def test_raising_source_wraps_in_group_and_cancels(self) -> None:
        # the source raises during a refill — in-flight tasks are cancelled
        # and the source's exception joins the group
        cancelled: list[int] = []

        async def source() -> AsyncIterator[ResultCoro]:
            yield ok_after(0)
            yield self._slow(1, cancelled)
            raise RuntimeError("source broke")

        with pytest.raises(BaseExceptionGroup) as exc_info:
            await collect(source(), concurrency=2)
        assert exc_info.group_contains(RuntimeError, match="source broke")
        assert cancelled == [1]

    async def test_raising_source_filter_ok_unordered(self) -> None:
        # the source raises during the initial fill
        async def source() -> AsyncIterator[ResultCoro]:
            yield ok_after(1)
            raise RuntimeError("source broke")

        with pytest.raises(BaseExceptionGroup) as exc_info:
            async for _ in filter_ok_unordered(source()):
                pass
        assert exc_info.group_contains(RuntimeError, match="source broke")

    async def test_try_reduce(self) -> None:
        async def source() -> AsyncIterator[IntCoro]:
            for i in (1, 2, 3):
                yield int_after(i)

        def add(acc: int, x: int) -> Ok[int]:
            return Ok(acc + x)

        assert await try_reduce(source(), 0, add) == Ok(6)

    async def test_try_reduce_short_circuit_closes_source(self) -> None:
        closed = False
        pulled: list[int] = []

        async def source() -> AsyncIterator[IntCoro]:
            nonlocal closed
            try:
                for i in (1, -1, 3):
                    pulled.append(i)
                    yield int_after(i)
            finally:
                closed = True

        def maybe_fail(acc: int, x: int) -> Ok[int] | Err[str]:
            return Err(f"negative: {x}") if x < 0 else Ok(acc + x)

        assert await try_reduce(source(), 0, maybe_fail) == Err("negative: -1")
        assert pulled == [1, -1]
        assert closed

    async def test_try_reduce_source_exception_propagates_bare(self) -> None:
        async def source() -> AsyncIterator[IntCoro]:
            yield int_after(1)
            raise RuntimeError("source broke")

        def add(acc: int, x: int) -> Ok[int]:
            return Ok(acc + x)

        with pytest.raises(RuntimeError, match="source broke"):
            await try_reduce(source(), 0, add)
