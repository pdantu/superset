# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Unit tests for InMemoryRateLimiter cleanup behaviour."""

import time
from collections.abc import Generator
from unittest.mock import patch

import pytest

from superset.mcp_service.middleware import InMemoryRateLimiter


@pytest.fixture
def limiter() -> Generator[InMemoryRateLimiter, None, None]:
    """Create a limiter and ensure its background thread is stopped after the test."""
    rl = InMemoryRateLimiter()
    yield rl
    rl.shutdown()


# ------------------------------------------------------------------
# Basic rate-limiting behaviour
# ------------------------------------------------------------------


def test_allows_requests_under_limit(limiter: InMemoryRateLimiter) -> None:
    """Requests below the limit should not be blocked."""
    limited, info = limiter.is_rate_limited("user:1", limit=5, window=60)
    assert limited is False
    assert info["remaining"] == 4


def test_blocks_requests_over_limit(limiter: InMemoryRateLimiter) -> None:
    """Requests at or above the limit should be blocked."""
    for _ in range(5):
        limiter.is_rate_limited("user:1", limit=5, window=60)

    limited, info = limiter.is_rate_limited("user:1", limit=5, window=60)
    assert limited is True
    assert info["remaining"] == 0


# ------------------------------------------------------------------
# Time-based cleanup
# ------------------------------------------------------------------


def test_time_based_cleanup_evicts_old_entries(
    limiter: InMemoryRateLimiter,
) -> None:
    """Entries older than the TTL should be removed during cleanup."""
    now = time.time()

    with limiter._lock:
        limiter._requests["old_key"] = [(now - 7200, 1)]  # 2 hours ago
        limiter._requests["new_key"] = [(now, 1)]
        # Force cleanup by backdating _last_cleanup
        limiter._last_cleanup = now - limiter._CLEANUP_INTERVAL_SECS - 1

    limiter.cleanup()

    with limiter._lock:
        assert "old_key" not in limiter._requests
        assert "new_key" in limiter._requests


# ------------------------------------------------------------------
# Size-threshold cleanup & observability logging
# ------------------------------------------------------------------


def test_size_threshold_triggers_aggressive_cleanup(
    limiter: InMemoryRateLimiter,
) -> None:
    """When total entries exceed the size threshold, per-key trimming fires."""
    now = time.time()

    with limiter._lock:
        # All entries are recent (within TTL) so time-based eviction keeps them
        limiter._requests["bulk"] = [(now - i * 0.1, 1) for i in range(12_000)]
        limiter._last_cleanup = now - limiter._CLEANUP_INTERVAL_SECS - 1

    with patch("superset.mcp_service.middleware.logger") as mock_logger:
        limiter.cleanup()
        mock_logger.warning.assert_called()
        log_msg = mock_logger.warning.call_args_list[0][0][0]
        assert "size threshold exceeded" in log_msg

    with limiter._lock:
        remaining = len(limiter._requests.get("bulk", []))
        assert remaining <= limiter._MAX_ENTRIES_PER_KEY


# ------------------------------------------------------------------
# Hard-cap enforcement
# ------------------------------------------------------------------


def test_hard_cap_evicts_oldest_keys(limiter: InMemoryRateLimiter) -> None:
    """When total entries exceed the hard cap, oldest keys are dropped."""
    now = time.time()

    with limiter._lock:
        # Each key gets recent entries (spaced 0.01s apart) so they survive
        # both time-based eviction and per-key aggressive trimming.
        entries_per_key = 100  # at or below _MAX_ENTRIES_PER_KEY
        num_keys = (limiter._HARD_CAP // entries_per_key) + 200
        for i in range(num_keys):
            limiter._requests[f"key_{i}"] = [
                (now - j * 0.01, 1) for j in range(entries_per_key)
            ]
        limiter._last_cleanup = now - limiter._CLEANUP_INTERVAL_SECS - 1

    with patch("superset.mcp_service.middleware.logger") as mock_logger:
        limiter.cleanup()
        warning_calls = mock_logger.warning.call_args_list
        hard_cap_logged = any("hard cap exceeded" in str(c) for c in warning_calls)
        assert hard_cap_logged

    with limiter._lock:
        total = sum(len(v) for v in limiter._requests.values())
        assert total <= limiter._HARD_CAP


# ------------------------------------------------------------------
# No-op when thresholds are not met
# ------------------------------------------------------------------


def test_cleanup_noop_when_thresholds_not_met(
    limiter: InMemoryRateLimiter,
) -> None:
    """Cleanup should be a no-op when neither time nor size thresholds are met."""
    limiter.is_rate_limited("user:1", limit=100, window=60)

    with limiter._lock:
        limiter._last_cleanup = time.time()  # just cleaned

    limiter.cleanup()

    with limiter._lock:
        assert "user:1" in limiter._requests


# ------------------------------------------------------------------
# Thread-safety under concurrent access
# ------------------------------------------------------------------


def test_concurrent_access_does_not_corrupt_state(
    limiter: InMemoryRateLimiter,
) -> None:
    """Concurrent is_rate_limited calls should not raise or corrupt data."""
    import concurrent.futures

    def hit(key: str) -> tuple[bool, dict[str, int]]:
        return limiter.is_rate_limited(key, limit=1000, window=60)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(hit, f"user:{i % 4}") for i in range(200)]
        results = [f.result() for f in futures]

    assert all(isinstance(r, tuple) for r in results)

    with limiter._lock:
        total = sum(len(v) for v in limiter._requests.values())
        assert total == 200


# ------------------------------------------------------------------
# Background cleanup thread
# ------------------------------------------------------------------


def test_background_thread_runs_cleanup(
    limiter: InMemoryRateLimiter,
) -> None:
    """The background thread should invoke cleanup periodically."""
    now = time.time()

    with limiter._lock:
        limiter._requests["stale"] = [(now - 7200, 1)]
        limiter._last_cleanup = now - limiter._CLEANUP_INTERVAL_SECS - 1

    # Temporarily shorten the interval so the thread fires quickly
    original_interval = limiter._CLEANUP_INTERVAL_SECS
    try:
        InMemoryRateLimiter._CLEANUP_INTERVAL_SECS = 0.1  # type: ignore[assignment]
        # Wake the thread by signaling then unsetting
        limiter._shutdown_event.set()
        limiter._cleanup_thread.join(timeout=2)
        # Restart a short-lived thread
        limiter._shutdown_event.clear()
        import threading

        t = threading.Thread(target=limiter._background_cleanup_loop, daemon=True)
        t.start()
        time.sleep(0.5)
        limiter._shutdown_event.set()
        t.join(timeout=2)
    finally:
        InMemoryRateLimiter._CLEANUP_INTERVAL_SECS = original_interval

    with limiter._lock:
        assert "stale" not in limiter._requests


# ------------------------------------------------------------------
# Shutdown
# ------------------------------------------------------------------


def test_shutdown_stops_background_thread(
    limiter: InMemoryRateLimiter,
) -> None:
    """shutdown() should signal the background thread to exit."""
    assert limiter._cleanup_thread.is_alive()
    limiter.shutdown()
    assert not limiter._cleanup_thread.is_alive()
