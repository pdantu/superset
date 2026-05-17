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

"""
Unit tests for InMemoryRateLimiter cleanup and high-traffic behaviour.
"""

import time
from unittest.mock import patch

import pytest

from superset.mcp_service.middleware import InMemoryRateLimiter


@pytest.fixture
def limiter():
    rl = InMemoryRateLimiter()
    yield rl
    rl.shutdown()


class TestInMemoryRateLimiterBasic:
    """Basic rate-limiting behaviour."""

    def test_allows_requests_under_limit(self, limiter: InMemoryRateLimiter) -> None:
        limited, info = limiter.is_rate_limited("user:1", limit=5, window=60)
        assert limited is False
        assert info["remaining"] == 4

    def test_blocks_requests_at_limit(self, limiter: InMemoryRateLimiter) -> None:
        for _ in range(5):
            limiter.is_rate_limited("user:1", limit=5, window=60)
        limited, info = limiter.is_rate_limited("user:1", limit=5, window=60)
        assert limited is True
        assert info["remaining"] == 0

    def test_separate_keys_independent(self, limiter: InMemoryRateLimiter) -> None:
        for _ in range(5):
            limiter.is_rate_limited("user:1", limit=5, window=60)
        limited, _ = limiter.is_rate_limited("user:2", limit=5, window=60)
        assert limited is False


class TestCleanupTimeBased:
    """Time-based cleanup removes stale entries."""

    def test_cleanup_removes_old_entries(self, limiter: InMemoryRateLimiter) -> None:
        now = time.time()
        with limiter._lock:
            limiter._requests["old_key"] = [(now - 7200, 1)]
            limiter._requests["fresh_key"] = [(now, 1)]

        limiter._do_cleanup(force=True)

        with limiter._lock:
            assert "old_key" not in limiter._requests
            assert "fresh_key" in limiter._requests

    def test_reactive_cleanup_skipped_before_interval(
        self, limiter: InMemoryRateLimiter
    ) -> None:
        with limiter._lock:
            limiter._requests["k"] = [(time.time(), 1)]
            limiter._last_cleanup = time.time()

        limiter.cleanup()

        with limiter._lock:
            assert "k" in limiter._requests


class TestSizeBasedCleanup:
    """Size-based cleanup triggers when entry count exceeds SIZE_THRESHOLD."""

    def test_size_threshold_triggers_cleanup(
        self, limiter: InMemoryRateLimiter
    ) -> None:
        now = time.time()
        with limiter._lock:
            for i in range(limiter.SIZE_THRESHOLD + 100):
                limiter._requests[f"key:{i}"].append((now, 1))

        with patch("superset.mcp_service.middleware.logger") as mock_logger:
            limiter._do_cleanup(force=False)
            mock_logger.info.assert_called()
            log_msg = mock_logger.info.call_args[0][0]
            assert "size-based cleanup triggered" in log_msg

    def test_aggressive_trim_when_still_over_threshold(
        self, limiter: InMemoryRateLimiter
    ) -> None:
        now = time.time()
        with limiter._lock:
            for i in range(200):
                limiter._requests[f"key:{i}"] = [(now - j, 1) for j in range(150)]

        with patch("superset.mcp_service.middleware.logger") as mock_logger:
            limiter._do_cleanup(force=True)
            mock_logger.warning.assert_called()
            any_trim_msg = any(
                "aggressive per-key trim" in str(c)
                for c in mock_logger.warning.call_args_list
            )
            assert any_trim_msg

        with limiter._lock:
            for key in limiter._requests:
                assert len(limiter._requests[key]) <= limiter.MAX_ENTRIES_PER_KEY


class TestHardCap:
    """Hard cap prevents unbounded memory growth."""

    def test_hard_cap_forces_cleanup_on_access(
        self, limiter: InMemoryRateLimiter
    ) -> None:
        now = time.time()
        with limiter._lock:
            for i in range(limiter.HARD_CAP + 10):
                limiter._requests[f"key:{i}"].append((now, 1))

        with patch("superset.mcp_service.middleware.logger") as mock_logger:
            limiter.is_rate_limited("trigger", limit=100, window=60)
            any_hard_cap_msg = any(
                "hard cap exceeded" in str(c)
                for c in mock_logger.warning.call_args_list
            )
            assert any_hard_cap_msg

    def test_hard_cap_evicts_oldest_keys(self, limiter: InMemoryRateLimiter) -> None:
        now = time.time()
        with limiter._lock:
            for i in range(limiter.HARD_CAP + 500):
                limiter._requests[f"key:{i}"] = [(now - (i * 0.001), 1)]

        limiter._do_cleanup(force=True)

        with limiter._lock:
            total = sum(len(v) for v in limiter._requests.values())
            assert total <= limiter.HARD_CAP


class TestBackgroundCleanup:
    """Background cleanup thread runs proactively."""

    def test_background_thread_is_alive(self, limiter: InMemoryRateLimiter) -> None:
        assert limiter._cleanup_thread.is_alive()
        assert limiter._cleanup_thread.daemon is True

    def test_shutdown_stops_thread(self) -> None:
        rl = InMemoryRateLimiter()
        assert rl._cleanup_thread.is_alive()
        rl.shutdown()
        assert not rl._cleanup_thread.is_alive()

    def test_background_cleanup_runs_periodically(self) -> None:
        rl = InMemoryRateLimiter()
        try:
            now = time.time()
            with rl._lock:
                rl._requests["stale"] = [(now - 7200, 1)]

            rl.BACKGROUND_CLEANUP_INTERVAL = 0.1  # type: ignore[assignment]
            rl._shutdown_event.set()
            rl._cleanup_thread.join(timeout=2)

            rl._shutdown_event.clear()
            rl._cleanup_thread = __import__("threading").Thread(
                target=rl._background_cleanup_loop, daemon=True
            )
            rl._cleanup_thread.start()
            time.sleep(0.5)

            with rl._lock:
                assert "stale" not in rl._requests
        finally:
            rl.shutdown()


class TestThreadSafety:
    """Concurrent access does not corrupt state."""

    def test_concurrent_rate_limiting(self, limiter: InMemoryRateLimiter) -> None:
        import threading

        errors: list[Exception] = []

        def hammer(key: str) -> None:
            try:
                for _ in range(200):
                    limiter.is_rate_limited(key, limit=1000, window=60)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=hammer, args=(f"user:{i}",)) for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors

    def test_concurrent_cleanup_and_access(self, limiter: InMemoryRateLimiter) -> None:
        import threading

        errors: list[Exception] = []

        def access_loop() -> None:
            try:
                for _ in range(200):
                    limiter.is_rate_limited("user:x", limit=1000, window=60)
            except Exception as exc:
                errors.append(exc)

        def cleanup_loop() -> None:
            try:
                for _ in range(50):
                    limiter._do_cleanup(force=True)
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=access_loop)
        t2 = threading.Thread(target=cleanup_loop)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors


class TestObservabilityLogging:
    """Cleanup emits the expected log messages."""

    def test_size_threshold_logs_info(self, limiter: InMemoryRateLimiter) -> None:
        now = time.time()
        with limiter._lock:
            for i in range(limiter.SIZE_THRESHOLD + 1):
                limiter._requests[f"k:{i}"] = [(now, 1)]

        with patch("superset.mcp_service.middleware.logger") as mock_logger:
            limiter._do_cleanup(force=True)
            info_calls = [str(c) for c in mock_logger.info.call_args_list]
            assert any("size-based cleanup triggered" in c for c in info_calls)

    def test_aggressive_trim_logs_warning(self, limiter: InMemoryRateLimiter) -> None:
        now = time.time()
        with limiter._lock:
            for i in range(200):
                limiter._requests[f"k:{i}"] = [(now - j, 1) for j in range(200)]

        with patch("superset.mcp_service.middleware.logger") as mock_logger:
            limiter._do_cleanup(force=True)
            warn_calls = [str(c) for c in mock_logger.warning.call_args_list]
            assert any("aggressive per-key trim" in c for c in warn_calls)

    def test_hard_cap_eviction_logs_warning(self, limiter: InMemoryRateLimiter) -> None:
        now = time.time()
        with limiter._lock:
            for i in range(limiter.HARD_CAP + 500):
                limiter._requests[f"k:{i}"] = [(now, 1)]

        with patch("superset.mcp_service.middleware.logger") as mock_logger:
            limiter._do_cleanup(force=True)
            warn_calls = [str(c) for c in mock_logger.warning.call_args_list]
            assert any("hard cap enforced" in c for c in warn_calls)
