# PipeForge -- processing-lock tests
# The old processor used a 24h idempotency key that was never released and was
# keyed on retry_count, so a processor error or timeout (which do not bump
# retry_count) could never be retried: the requeued task hit the still-set key
# and skipped until the Sentinel gave up to the DLQ. These tests drive the real
# BlackboardClient lock and prove a completed worker frees the session again.

import pytest

fakeredis = pytest.importorskip("fakeredis")
import redis


@pytest.fixture
def bb(monkeypatch):
    server = fakeredis.FakeServer()
    monkeypatch.setattr(
        redis, "Redis",
        lambda *a, **k: fakeredis.FakeStrictRedis(
            server=server, decode_responses=k.get("decode_responses", False)
        ),
    )
    from shared.redis_utils import BlackboardClient
    return BlackboardClient(tenant="t")


def test_lock_is_mutually_exclusive(bb):
    assert bb.acquire_processing_lock("pf_1") is True
    assert bb.acquire_processing_lock("pf_1") is False  # second worker blocked


def test_release_allows_retry(bb):
    assert bb.acquire_processing_lock("pf_1") is True
    bb.release_processing_lock("pf_1")
    # A later retry of the same session must be able to run again.
    assert bb.acquire_processing_lock("pf_1") is True


def test_lock_expires(bb):
    assert bb.acquire_processing_lock("pf_1", ttl_sec=1) is True
    # TTL bounds a crashed worker: the lock must carry an expiry.
    assert bb.raw().ttl("lock:pf:t:pf_1") > 0


def test_lock_is_tenant_scoped(bb, monkeypatch):
    from shared.redis_utils import BlackboardClient
    other = BlackboardClient(tenant="other")
    assert bb.acquire_processing_lock("pf_1") is True
    # Same sid, different tenant, must not be blocked by this tenant's lock.
    assert other.acquire_processing_lock("pf_1") is True
