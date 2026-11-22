import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import ParamSpec, TypeVar

from app.common.errors import AppError

P = ParamSpec("P")
T = TypeVar("T")


class RagExecutor:
    def __init__(self, workers: int, admission_seconds: float):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ai-rag")
        self._capacity = asyncio.BoundedSemaphore(workers)
        self._admission_seconds = admission_seconds

    async def run(self, function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        try:
            await asyncio.wait_for(self._capacity.acquire(), self._admission_seconds)
        except TimeoutError:
            raise AppError(503) from None
        try:
            work = asyncio.get_running_loop().run_in_executor(
                self._pool, partial(function, *args, **kwargs)
            )
        except BaseException:
            self._capacity.release()
            raise

        def release_when_done(result: asyncio.Future) -> None:
            self._capacity.release()
            if not result.cancelled():
                # Consume a detached worker's exception if the caller disconnected.
                result.exception()

        work.add_done_callback(release_when_done)
        return await asyncio.shield(work)

    async def close(self) -> None:
        await asyncio.to_thread(self._pool.shutdown, wait=True, cancel_futures=True)
