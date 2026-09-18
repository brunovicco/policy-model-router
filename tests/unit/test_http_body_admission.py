"""Deterministic ASGI behavior tests for the pre-parse body admission boundary."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from starlette.types import Message, Receive, Scope, Send

from policy_model_router.entrypoints.http import _BodySizeAndIpRateLimitMiddleware


class _Limiter:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.keys: list[str] = []

    async def allow(self, key: str) -> bool:
        self.keys.append(key)
        return self.allowed


class _Channel:
    def __init__(self, messages: tuple[Message, ...]) -> None:
        self.messages = messages
        self.reads = 0

    async def receive(self) -> Message:
        assert self.reads < len(self.messages), "unexpected read or unbounded remainder drain"
        result = self.messages[self.reads]
        self.reads += 1
        return result


def _scope(
    limiter: _Limiter,
    *,
    headers: tuple[tuple[bytes, bytes], ...] = (),
    path: str = "/route",
    method: str = "POST",
    client: tuple[str, int] | None = ("test-peer", 1234),
) -> Scope:
    return {
        "type": "http",
        "path": path,
        "method": method,
        "headers": list(headers),
        "client": client,
        "app": SimpleNamespace(
            state=SimpleNamespace(max_request_body_bytes=8, ip_rate_limiter=limiter)
        ),
    }


class _GuardHarness:
    def __init__(self) -> None:
        self.downstream_calls = 0
        self.received: list[Message] = []
        self.sent: list[Message] = []
        self.guard = _BodySizeAndIpRateLimitMiddleware(self.downstream)

    async def downstream(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.downstream_calls += 1
        self.received.append(await receive())
        self.received.append(await receive())
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def send(self, message: Message) -> None:
        self.sent.append(message)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("messages", "expected"),
    [
        (({"type": "http.request"},), b""),
        (({"type": "http.request", "body": b"12345678"},), b"12345678"),
        (({"type": "http.request", "body": "éééé".encode()},), "éééé".encode()),
        (
            (
                {"type": "http.request", "body": b"123", "more_body": True},
                {"type": "http.request", "more_body": True},
                {"type": "http.request", "body": b"45678", "more_body": True},
                {"type": "http.request", "body": b""},
            ),
            b"12345678",
        ),
        (
            (
                *({"type": "http.request", "more_body": True} for _ in range(128)),
                {"type": "http.request", "body": b"12345678"},
            ),
            b"12345678",
        ),
    ],
)
async def test_complete_body_is_identical_and_delivered_once(
    messages: tuple[Message, ...], expected: bytes
) -> None:
    limiter = _Limiter()
    harness = _GuardHarness()
    disconnect: Message = {"type": "http.disconnect"}
    channel = _Channel((*messages, disconnect))

    await harness.guard(_scope(limiter), channel.receive, harness.send)

    assert harness.received == [
        {"type": "http.request", "body": expected, "more_body": False},
        disconnect,
    ]
    assert harness.received[1] is disconnect
    assert channel.reads == len(messages) + 1
    assert harness.downstream_calls == 1
    assert limiter.keys == ["ip:test-peer"]


@pytest.mark.anyio
@pytest.mark.parametrize("declared_length", [None, b"0", b"1", b"8"])
@pytest.mark.parametrize(
    "messages",
    [
        ({"type": "http.request", "body": b"123456789"},),
        ({"type": "http.request", "body": b"x" * 1000, "more_body": True},),
        (
            {"type": "http.request", "body": b"12345", "more_body": True},
            {"type": "http.request", "body": b"6789", "more_body": True},
        ),
        (
            {"type": "http.request", "body": b"12345678", "more_body": True},
            {"type": "http.request", "more_body": True},
            {"type": "http.request", "body": b"9"},
        ),
    ],
)
async def test_actual_overflow_rejects_once_without_downstream_or_drain(
    declared_length: bytes | None, messages: tuple[Message, ...]
) -> None:
    limiter = _Limiter()
    harness = _GuardHarness()
    channel = _Channel((*messages, {"type": "http.request", "body": b"unread-remainder"}))
    headers = () if declared_length is None else ((b"content-length", declared_length),)

    await harness.guard(_scope(limiter, headers=headers), channel.receive, harness.send)

    assert harness.downstream_calls == 0
    assert channel.reads == len(messages)
    assert limiter.keys == ["ip:test-peer"]
    assert [message["status"] for message in harness.sent if message["type"].endswith("start")] == [
        413
    ]
    assert json.loads(harness.sent[-1]["body"]) == {
        "error": {"code": "payload_too_large", "message": "request body exceeds the 8-byte limit"}
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [
        ((b"content-length", b""),),
        ((b"content-length", b" \t"),),
        ((b"content-length", b"-1"),),
        ((b"content-length", b"+1"),),
        ((b"content-length", b"1.0"),),
        ((b"content-length", b"1e0"),),
        ((b"content-length", b"1, 1"),),
        ((b"content-length", b"1\n"),),
        ((b"content-length", b"\xff"),),
        ((b"content-length", b"not-a-length"),),
        ((b"content-length", b"1"), (b"Content-Length", b"1")),
        ((b"content-length", b"999"), (b"content-length", b"1")),
        ((b"content-length", b""), (b"content-length", b"1")),
    ],
)
async def test_invalid_or_repeated_length_returns_fixed_400_before_reading(
    headers: tuple[tuple[bytes, bytes], ...],
) -> None:
    limiter = _Limiter()
    harness = _GuardHarness()
    channel = _Channel(())

    await harness.guard(_scope(limiter, headers=headers), channel.receive, harness.send)

    assert channel.reads == 0
    assert harness.downstream_calls == 0
    assert limiter.keys == ["ip:test-peer"]
    assert harness.sent[0]["status"] == 400
    assert json.loads(harness.sent[-1]["body"]) == {
        "error": {"code": "invalid_request", "message": "invalid Content-Length header"}
    }


@pytest.mark.anyio
@pytest.mark.parametrize("value", [b"9", b"10", b"0009", b"9" * 5000])
async def test_declared_oversize_needs_no_body_read_or_integer_conversion(value: bytes) -> None:
    harness = _GuardHarness()
    channel = _Channel(())

    await harness.guard(
        _scope(_Limiter(), headers=((b"Content-Length", value),)), channel.receive, harness.send
    )

    assert channel.reads == 0
    assert harness.downstream_calls == 0
    assert harness.sent[0]["status"] == 413


@pytest.mark.anyio
@pytest.mark.parametrize("value", [b"0", b"1", b"8", b" \t0008 \t", b"0" * 5000 + b"8"])
async def test_valid_length_hint_still_requires_complete_actual_body(value: bytes) -> None:
    harness = _GuardHarness()
    channel = _Channel(({"type": "http.request", "body": b"12345678"}, {"type": "http.disconnect"}))

    await harness.guard(
        _scope(_Limiter(), headers=((b"CONTENT-LENGTH", value),)), channel.receive, harness.send
    )

    assert harness.received[0]["body"] == b"12345678"
    assert harness.sent[0]["status"] == 204


@pytest.mark.anyio
@pytest.mark.parametrize("value", [b"invalid", b"99999"])
async def test_blocked_ip_keeps_429_precedence_and_does_not_read(value: bytes) -> None:
    limiter = _Limiter(allowed=False)
    harness = _GuardHarness()
    channel = _Channel(())

    await harness.guard(
        _scope(limiter, headers=((b"content-length", value),)), channel.receive, harness.send
    )

    assert limiter.keys == ["ip:test-peer"]
    assert harness.sent[0]["status"] == 429
    assert harness.downstream_calls == 0
    assert channel.reads == 0


@pytest.mark.anyio
async def test_missing_peer_uses_existing_unknown_ip_key() -> None:
    limiter = _Limiter()
    harness = _GuardHarness()
    channel = _Channel(({"type": "http.request"}, {"type": "http.disconnect"}))

    await harness.guard(_scope(limiter, client=None), channel.receive, harness.send)

    assert limiter.keys == ["ip:unknown"]


@pytest.mark.anyio
async def test_disconnect_discards_partial_body_and_preserves_authentic_event() -> None:
    harness = _GuardHarness()
    disconnect: Message = {"type": "http.disconnect"}
    channel = _Channel(
        (
            {"type": "http.request", "body": b"partial", "more_body": True},
            disconnect,
            disconnect,
        )
    )

    await harness.guard(_scope(_Limiter()), channel.receive, harness.send)

    assert harness.received == [disconnect, disconnect]
    assert harness.received[0] is disconnect


@pytest.mark.anyio
@pytest.mark.parametrize("exception", [asyncio.CancelledError, ConnectionError])
async def test_cancelled_or_failed_receive_propagates_without_downstream_response(
    exception: type[BaseException],
) -> None:
    harness = _GuardHarness()
    reads = 0

    async def receive() -> Message:
        nonlocal reads
        reads += 1
        if reads == 1:
            return {"type": "http.request", "body": b"partial", "more_body": True}
        raise exception

    with pytest.raises(exception):
        await harness.guard(_scope(_Limiter()), receive, harness.send)

    assert harness.downstream_calls == 0
    assert harness.sent == []


@pytest.mark.anyio
async def test_response_send_failure_is_not_suppressed_or_reclassified() -> None:
    harness = _GuardHarness()
    channel = _Channel(({"type": "http.request", "body": b"123456789"},))
    attempts: list[Message] = []

    async def send(message: Message) -> None:
        attempts.append(message)
        raise ConnectionError

    with pytest.raises(ConnectionError):
        await harness.guard(_scope(_Limiter()), channel.receive, send)

    assert harness.downstream_calls == 0
    assert len(attempts) == 1
    assert attempts[0]["status"] == 413


@pytest.mark.anyio
async def test_unexpected_asgi_event_propagates_without_a_body_rejection() -> None:
    harness = _GuardHarness()
    channel = _Channel(({"type": "websocket.receive"},))

    with pytest.raises(RuntimeError, match="unexpected ASGI event"):
        await harness.guard(_scope(_Limiter()), channel.receive, harness.send)

    assert harness.downstream_calls == 0
    assert harness.sent == []


@pytest.mark.anyio
async def test_concurrent_requests_do_not_share_accumulator_or_delivery_state() -> None:
    first_read = {"a": asyncio.Event(), "b": asyncio.Event()}
    bodies: list[bytes] = []
    limiter = _Limiter()

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        message = await receive()
        bodies.append(message["body"])
        assert (await receive())["type"] == "http.disconnect"

    guard = _BodySizeAndIpRateLimitMiddleware(downstream)

    async def run(tag: str, other: str) -> None:
        reads = 0

        async def receive() -> Message:
            nonlocal reads
            reads += 1
            if reads == 1:
                first_read[tag].set()
                return {"type": "http.request", "body": tag.encode() * 4, "more_body": True}
            if reads == 2:
                await first_read[other].wait()
                return {"type": "http.request", "body": tag.encode() * 4}
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            raise AssertionError("this synthetic downstream does not respond")

        await guard(_scope(limiter), receive, send)

    async with asyncio.TaskGroup() as group:
        group.create_task(run("a", "b"))
        group.create_task(run("b", "a"))

    assert sorted(bodies) == [b"aaaaaaaa", b"bbbbbbbb"]
    assert limiter.keys == ["ip:test-peer", "ip:test-peer"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "method"),
    [("/health", "GET"), ("/readyz", "GET"), ("/metrics", "GET"), ("/route", "GET")],
)
async def test_other_http_paths_do_not_read_or_consult_quota(path: str, method: str) -> None:
    limiter = _Limiter()
    calls = 0
    channel = _Channel(())

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal calls
        calls += 1
        assert receive == channel.receive

    guard = _BodySizeAndIpRateLimitMiddleware(downstream)
    await guard(_scope(limiter, path=path, method=method), channel.receive, _GuardHarness().send)

    assert calls == 1
    assert limiter.keys == []
    assert channel.reads == 0


@pytest.mark.anyio
@pytest.mark.parametrize(("value", "status"), [(b"999", 413), (b"invalid", 400)])
async def test_declared_header_precheck_still_covers_other_paths(value: bytes, status: int) -> None:
    limiter = _Limiter()
    harness = _GuardHarness()
    channel = _Channel(())

    await harness.guard(
        _scope(limiter, path="/health", method="GET", headers=((b"content-length", value),)),
        channel.receive,
        harness.send,
    )

    assert harness.sent[0]["status"] == status
    assert harness.downstream_calls == 0
    assert limiter.keys == []
    assert channel.reads == 0


@pytest.mark.anyio
@pytest.mark.parametrize("scope_type", ["lifespan", "websocket"])
async def test_non_http_scopes_pass_through_without_http_state(scope_type: str) -> None:
    scope: Scope = {"type": scope_type}
    channel = _Channel(())
    harness = _GuardHarness()
    seen: list[Scope] = []

    async def downstream(actual: Scope, receive: Receive, send: Send) -> None:
        seen.append(actual)
        assert receive == channel.receive
        assert send == harness.send

    await _BodySizeAndIpRateLimitMiddleware(downstream)(scope, channel.receive, harness.send)

    assert seen == [scope]
    assert seen[0] is scope
    assert channel.reads == 0
