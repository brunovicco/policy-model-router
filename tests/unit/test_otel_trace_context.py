"""P1.5 integration checks for W3C context adoption via a2a-otel-kit."""

from a2a_otel_kit.adapters.propagation import continue_trace, inject_trace_context
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider


def test_router_continues_incoming_w3c_trace_context() -> None:
    parent_trace_id = "11111111111111111111111111111111"
    carrier = {
        "traceparent": f"00-{parent_trace_id}-2222222222222222-01",
    }

    with continue_trace(carrier):
        current = trace.get_current_span().get_span_context()
        assert f"{current.trace_id:032x}" == parent_trace_id


def test_trace_context_injection_uses_w3c_header_only() -> None:
    provider = TracerProvider()
    tracer = provider.get_tracer("policy-model-router-test")
    carrier: dict[str, str] = {}

    with tracer.start_as_current_span("parent"):
        inject_trace_context(carrier)

    assert set(carrier) <= {"traceparent", "tracestate"}
    assert carrier["traceparent"].startswith("00-")
    provider.shutdown()
