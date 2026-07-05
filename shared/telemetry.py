# Lead Architect: PipeForge
# Shared Utility: OpenTelemetry Distributed Tracing (v4)
# Emits spans to any OTLP-compatible backend: Jaeger, Grafana Tempo, Datadog, Honeycomb.
# Zero-change to existing nodes -- wrap with @trace_node decorator.

import os, time, functools
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import Status, StatusCode

# Optional exporters -- imported lazily so missing packages don't crash other nodes
_OTLP_ENDPOINT  = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")   # e.g. http://jaeger:4317
_JAEGER_HOST    = os.getenv("OTEL_JAEGER_HOST", "")               # e.g. jaeger
_JAEGER_PORT    = int(os.getenv("OTEL_JAEGER_PORT", "6831"))
_SERVICE_NAME   = os.getenv("OTEL_SERVICE_NAME", "pipeforge")
_CONSOLE_EXPORT = os.getenv("OTEL_CONSOLE_EXPORT", "false").lower() == "true"

def _build_provider() -> TracerProvider:
    resource = Resource.create({
        "service.name":    _SERVICE_NAME,
        "service.version": "4.0.0",
        "deployment.env":  os.getenv("DEPLOYMENT_ENV", "development"),
    })
    provider = TracerProvider(resource=resource)

    # 1. OTLP (Jaeger, Grafana Tempo, Datadog, Honeycomb, etc.)
    if _OTLP_ENDPOINT:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=_OTLP_ENDPOINT, insecure=True))
            )
            print(f"[Telemetry] OTLP exporter -> {_OTLP_ENDPOINT}")
        except Exception as e:
            print(f"[Telemetry] OTLP exporter failed: {e}")

    # 2. Jaeger Thrift (legacy, no collector needed)
    if _JAEGER_HOST and not _OTLP_ENDPOINT:
        try:
            from opentelemetry.exporter.jaeger.thrift import JaegerExporter
            provider.add_span_processor(
                BatchSpanProcessor(JaegerExporter(
                    agent_host_name=_JAEGER_HOST,
                    agent_port=_JAEGER_PORT,
                ))
            )
            print(f"[Telemetry] Jaeger exporter -> {_JAEGER_HOST}:{_JAEGER_PORT}")
        except Exception as e:
            print(f"[Telemetry] Jaeger exporter failed: {e}")

    # 3. Console (dev mode / fallback)
    if _CONSOLE_EXPORT or (not _OTLP_ENDPOINT and not _JAEGER_HOST):
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        if _CONSOLE_EXPORT:
            print("[Telemetry] Console exporter active (dev mode)")

    return provider

# -- Global tracer (initialised once per process) -------------------------
_provider = _build_provider()
trace.set_tracer_provider(_provider)
tracer = trace.get_tracer("pipeforge.pipeline", "4.0.0")


# -- Decorator: wrap any agent function with a span -----------------------
def trace_node(node_name: str):
    """
    Decorator that wraps an agent function in an OTel span.

    Usage:
        @trace_node("worker")
        def process(sid, state):
            ...
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with tracer.start_as_current_span(f"pipeforge.{node_name}") as span:
                # Pull sid from first positional arg if available
                sid = args[0] if args else kwargs.get("sid", "unknown")
                span.set_attribute("session.id",   str(sid))
                span.set_attribute("node.name",    node_name)
                span.set_attribute("service.name", _SERVICE_NAME)
                try:
                    result = fn(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise
        return wrapper
    return decorator


# -- Context manager: fine-grained sub-spans ------------------------------
@contextmanager
def span(name: str, **attributes):
    """
    Context manager for a child span inside a node.

    Usage:
        with span("llm_call", model="gpt-4o-mini", tokens=320) as s:
            ...
            s.set_attribute("cost_usd", 0.00042)
    """
    with tracer.start_as_current_span(name) as s:
        for k, v in attributes.items():
            s.set_attribute(k, v)
        try:
            yield s
            s.set_status(Status(StatusCode.OK))
        except Exception as e:
            s.set_status(Status(StatusCode.ERROR, str(e)))
            s.record_exception(e)
            raise


# -- Standalone helper: record a session event ----------------------------
def record_session_event(sid: str, event: str, **attrs):
    """Fire-and-forget span for discrete session events (budget warn, security block, etc.)."""
    with tracer.start_as_current_span(f"pipeforge.event.{event}") as s:
        s.set_attribute("session.id", sid)
        s.set_attribute("event.type", event)
        for k, v in attrs.items():
            s.set_attribute(k, str(v))
        s.set_status(Status(StatusCode.OK))