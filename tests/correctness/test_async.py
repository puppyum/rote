"""Tests for @cache on async functions."""

from __future__ import annotations

import asyncio

import pytest

import rote


@pytest.fixture(autouse=True)
def _isolated(tmp_path):
    rote.configure(cache_dir=tmp_path / ".rote", telemetry=False, min_duration_s=0.0)
    rote.clear()


def test_async_function_caches():
    @rote.cache
    async def slow_double(x):
        await asyncio.sleep(0)  # yield to event loop
        return x * 2

    async def main():
        return await slow_double(5), await slow_double(5)

    a, b = asyncio.run(main())
    assert a == b == 10
    assert rote.stats()["hits"] >= 1


def test_async_function_with_args():
    @rote.cache
    async def compute(n):
        await asyncio.sleep(0)
        # Enough work that encode-time doesn't trigger the perf-guard
        # blacklist (paper §3.3.2 — skip caching if save > run).
        total = 0
        for i in range(n * 200_000):
            total += i % 7
        return total

    async def main():
        results = []
        for n in [4, 8, 4, 8]:
            results.append(await compute(n))
        return results

    results = asyncio.run(main())
    assert results[0] == results[2] and results[1] == results[3]
    stats = rote.stats()
    assert stats["hits"] >= 2  # at least two repeated args


def test_async_function_raises_propagates():
    @rote.cache
    async def boom(x):
        await asyncio.sleep(0)
        raise ValueError(f"bad: {x}")

    async def main():
        await boom(5)

    with pytest.raises(ValueError, match="bad: 5"):
        asyncio.run(main())


def test_async_function_stdout_replay():
    @rote.cache
    async def chatty(x):
        await asyncio.sleep(0)
        print(f"computing {x}")
        return x + 1

    async def main():
        for _ in range(3):
            await chatty(7)

    import contextlib as _contextlib
    import io as _io

    buf = _io.StringIO()
    with _contextlib.redirect_stdout(buf):
        asyncio.run(main())
    out = buf.getvalue()
    # All 3 calls should produce the printed line (first writes real, rest replay).
    assert out.count("computing 7") == 3
