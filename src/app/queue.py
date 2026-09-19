"""Decouples "accept this update" from "do the (slow) agent turn for it".

`BackgroundTasksQueue` is today's implementation: it defers work until after the
webhook's HTTP response has been sent, using FastAPI's per-request `BackgroundTasks`.
A future durable queue (see SPEC.md phase 2, pgqueuer) would implement the same
`Queue` protocol by inserting a row instead of scheduling an in-process task — no
caller of `enqueue()` would need to change. See docs/DECISIONS.md.
"""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any, Protocol

from fastapi import BackgroundTasks


class Queue(Protocol):
    def enqueue(self, coro: Coroutine[Any, Any, None]) -> None: ...


async def _run_and_discard(coro: Coroutine[Any, Any, None]) -> None:
    await coro


class BackgroundTasksQueue:
    def __init__(self, background_tasks: BackgroundTasks) -> None:
        self._background_tasks = background_tasks

    def enqueue(self, coro: Coroutine[Any, Any, None]) -> None:
        self._background_tasks.add_task(_run_and_discard, coro)
