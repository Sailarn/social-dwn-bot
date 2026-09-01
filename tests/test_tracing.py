"""The request id: the link from a Telegram /errors line to the Pi log."""

import asyncio
import logging

from src.core.tracing import (
    NO_REQUEST,
    RequestIdFilter,
    current_request_id,
    start_request,
)


def make_record() -> logging.LogRecord:
    return logging.LogRecord("t", logging.INFO, "f", 1, "msg", None, None)


def test_filter_injects_a_placeholder_when_idle():
    record = make_record()
    RequestIdFilter().filter(record)
    assert record.request_id == NO_REQUEST


def test_filter_injects_the_current_request_id():
    token = current_request_id.set("abc123")
    try:
        record = make_record()
        RequestIdFilter().filter(record)
        assert record.request_id == "abc123"
    finally:
        current_request_id.reset(token)


def test_ids_are_short_and_distinct():
    first, second = start_request(), start_request()
    assert first != second
    assert len(first) == 6 and first.isalnum()


def test_the_id_reaches_blocking_work_in_a_thread():
    """The download runs via asyncio.to_thread; its logs must carry the id too."""
    seen = {}

    def blocking_work():
        record = make_record()
        RequestIdFilter().filter(record)
        seen["id"] = record.request_id

    async def handle():
        request_id = start_request()
        await asyncio.to_thread(blocking_work)
        return request_id

    request_id = asyncio.run(handle())
    assert seen["id"] == request_id


def test_concurrent_requests_do_not_share_an_id():
    async def one():
        request_id = start_request()
        await asyncio.sleep(0)          # let the other task interleave
        return request_id, current_request_id.get()

    async def both():
        return await asyncio.gather(one(), one())

    (a_set, a_seen), (b_set, b_seen) = asyncio.run(both())
    assert a_set == a_seen and b_set == b_seen
    assert a_set != b_set
