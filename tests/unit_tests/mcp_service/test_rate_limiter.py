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
Unit tests for InMemoryRateLimiter cleanup behaviour under high traffic.
"""

import time
from unittest.mock import patch

import pytest

from superset.mcp_service.middleware import InMemoryRateLimiter


@pytest.fixture
def limiter():
    """Create a limiter and ensure its background thread is stopped after each test."""
    rl = InMemoryRateLimiter()
    yield rl
    rl.shutdown()


class TestInMemoryRateLimiterCleanup:
    """Tests for proactive cleanup, hard cap, and observability logging."""

    def test_time_based_cleanup_removes_stale_entries(
        self, limiter: InMemoryRateLimiter
    ) -> None:
        """Entries older than 1 hour should be evicted on cleanup."""
        old_ts = time.time() - 7200  # 2 hours ago
        limiter._requests["old_key"] = [(old_ts, 1)]
        limiter._last_cleanup = 0  # force time-based trigger
        limiter.cleanup()
        assert "old_key" not in limiter._requests

    def test_cleanup_preserves_recent_entries(
        self, limiter: InMemoryRateLimiter
    ) -> None:
        """Recent entries within the 1-hour window should survive cleanup."""
        recent_ts = time.time() - 60  # 1 minute ago
        limiter._requests["recent_key"] = [(recent_ts, 1)]
        limiter._last_cleanup = 0
        limiter.cleanup()
        assert "recent_key" in limiter._requests
        assert len(limiter._requests["recent_key"]) == 1

    def test_soft_threshold_triggers_per_key_trim(
        self, limiter: InMemoryRateLimiter
    ) -> None:
        """When total entries exceed _SIZE_THRESHOLD, per-key lists are trimmed."""
        now = time.time()
        # Create entries that exceed the soft threshold with recent timestamps
        entries_per_key = 200
        num_keys = (limiter._SIZE_THRESHOLD // entries_per_key) + 1
        for i in range(num_keys):
            limiter._requests[f"key_{i}"] = [
                (now - j, 1) for j in range(entries_per_key)
            ]
        limiter._last_cleanup = 0

        with patch("superset.mcp_service.middleware.logger") as mock_logger:
            limiter.cleanup()
            mock_logger.warning.assert_called()
            # Verify the log message mentions "soft threshold"
            call_args = mock_logger.warning.call_args_list[0]
            assert "soft threshold" in call_args[0][0]

        # Each key should be trimmed to _MAX_ENTRIES_PER_KEY
        for key in limiter._requests:
            assert len(limiter._requests[key]) <= limiter._MAX_ENTRIES_PER_KEY

    def test_hard_cap_evicts_oldest_keys(self, limiter: InMemoryRateLimiter) -> None:
        """When total entries exceed _MAX_ENTRIES, oldest keys are evicted."""
        now = time.time()
        # Temporarily lower the hard cap for testing
        original_max = InMemoryRateLimiter._MAX_ENTRIES
        original_threshold = InMemoryRateLimiter._SIZE_THRESHOLD
        try:
            InMemoryRateLimiter._MAX_ENTRIES = 100
            InMemoryRateLimiter._SIZE_THRESHOLD = 50
            InMemoryRateLimiter._MAX_ENTRIES_PER_KEY = 10

            # Create keys with distinct timestamps so we can verify eviction order
            # "old" keys have older timestamps, "new" keys have recent ones
            for i in range(20):
                limiter._requests[f"old_key_{i}"] = [
                    (now - 3000 + j, 1) for j in range(10)
                ]
            for i in range(20):
                limiter._requests[f"new_key_{i}"] = [
                    (now - 100 + j, 1) for j in range(10)
                ]

            limiter._last_cleanup = 0

            with patch("superset.mcp_service.middleware.logger") as mock_logger:
                limiter.cleanup()
                # Should have logged both soft threshold and hard cap warnings
                warning_messages = [
                    call[0][0] for call in mock_logger.warning.call_args_list
                ]
                assert any("hard cap" in msg for msg in warning_messages)

            # Total entries should be at or below the hard cap
            total = sum(len(v) for v in limiter._requests.values())
            assert total <= 100

            # New keys should be preserved over old keys
            new_key_count = sum(
                1 for k in limiter._requests if k.startswith("new_key_")
            )
            old_key_count = sum(
                1 for k in limiter._requests if k.startswith("old_key_")
            )
            assert new_key_count > old_key_count
        finally:
            InMemoryRateLimiter._MAX_ENTRIES = original_max
            InMemoryRateLimiter._SIZE_THRESHOLD = original_threshold
            InMemoryRateLimiter._MAX_ENTRIES_PER_KEY = 100

    def test_cleanup_skips_when_not_needed(self, limiter: InMemoryRateLimiter) -> None:
        """Cleanup should be a no-op when interval hasn't elapsed and size is low."""
        now = time.time()
        limiter._requests["key"] = [(now, 1)]
        limiter._last_cleanup = now  # just cleaned up

        limiter.cleanup()

        # Entry should remain untouched
        assert len(limiter._requests["key"]) == 1

    def test_background_thread_is_daemon(self, limiter: InMemoryRateLimiter) -> None:
        """The background cleanup thread should be a daemon thread."""
        assert limiter._cleanup_thread.daemon is True
        assert limiter._cleanup_thread.is_alive()

    def test_shutdown_stops_background_thread(self) -> None:
        """shutdown() should signal the background thread to exit."""
        rl = InMemoryRateLimiter()
        assert rl._cleanup_thread.is_alive()
        rl.shutdown()
        assert not rl._cleanup_thread.is_alive()

    def test_thread_safety_under_concurrent_access(
        self, limiter: InMemoryRateLimiter
    ) -> None:
        """Rate limiter should not corrupt state under concurrent access."""
        import threading

        errors: list[Exception] = []

        def writer() -> None:
            try:
                for i in range(200):
                    limiter.is_rate_limited(f"concurrent_key_{i % 10}", limit=1000)
            except Exception as e:
                errors.append(e)

        def cleaner() -> None:
            try:
                for _ in range(10):
                    limiter._last_cleanup = 0
                    limiter.cleanup()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(4)]
        threads += [threading.Thread(target=cleaner) for _ in range(2)]

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Concurrent access raised errors: {errors}"

    def test_observability_log_on_soft_threshold(
        self, limiter: InMemoryRateLimiter
    ) -> None:
        """A warning log should be emitted when the soft threshold is hit."""
        now = time.time()
        # Spread entries across many keys so they all survive time-based eviction
        # but total exceeds the soft threshold
        entries_per_key = 50
        num_keys = (limiter._SIZE_THRESHOLD // entries_per_key) + 1
        for i in range(num_keys):
            limiter._requests[f"obs_key_{i}"] = [
                (now - j * 0.01, 1) for j in range(entries_per_key)
            ]
        limiter._last_cleanup = 0

        with patch("superset.mcp_service.middleware.logger") as mock_logger:
            limiter.cleanup()
            mock_logger.warning.assert_called()
            call_args = mock_logger.warning.call_args_list[0]
            assert "soft threshold" in call_args[0][0]

    def test_observability_no_log_below_threshold(
        self, limiter: InMemoryRateLimiter
    ) -> None:
        """No warning should be emitted when entry count is below the soft threshold."""
        now = time.time()
        limiter._requests["small_key"] = [(now - i, 1) for i in range(10)]
        limiter._last_cleanup = 0

        with patch("superset.mcp_service.middleware.logger") as mock_logger:
            limiter.cleanup()
            mock_logger.warning.assert_not_called()

    def test_high_traffic_simulation(self, limiter: InMemoryRateLimiter) -> None:
        """Simulate high traffic: many unique keys with many entries each.

        After cleanup, total entries should be bounded.
        """
        now = time.time()
        original_max = InMemoryRateLimiter._MAX_ENTRIES
        original_threshold = InMemoryRateLimiter._SIZE_THRESHOLD
        try:
            InMemoryRateLimiter._MAX_ENTRIES = 500
            InMemoryRateLimiter._SIZE_THRESHOLD = 200

            # Simulate 100 unique clients each making 50 requests
            for i in range(100):
                limiter._requests[f"client_{i}"] = [
                    (now - j * 0.1, 1) for j in range(50)
                ]

            limiter._last_cleanup = 0
            limiter.cleanup()

            total = sum(len(v) for v in limiter._requests.values())
            assert total <= 500
        finally:
            InMemoryRateLimiter._MAX_ENTRIES = original_max
            InMemoryRateLimiter._SIZE_THRESHOLD = original_threshold

    def test_is_rate_limited_basic(self, limiter: InMemoryRateLimiter) -> None:
        """Basic rate limiting should work correctly."""
        limited, info = limiter.is_rate_limited("test_key", limit=2, window=60)
        assert not limited
        assert info["remaining"] == 1

        limited, info = limiter.is_rate_limited("test_key", limit=2, window=60)
        assert not limited
        assert info["remaining"] == 0

        limited, info = limiter.is_rate_limited("test_key", limit=2, window=60)
        assert limited
        assert info["remaining"] == 0

    def test_empty_keys_removed_on_cleanup(self, limiter: InMemoryRateLimiter) -> None:
        """Keys with no entries after time-based eviction should be deleted."""
        old_ts = time.time() - 7200
        limiter._requests["empty_after_cleanup"] = [(old_ts, 1)]
        limiter._requests["still_has_entries"] = [(time.time(), 1)]
        limiter._last_cleanup = 0

        limiter.cleanup()

        assert "empty_after_cleanup" not in limiter._requests
        assert "still_has_entries" in limiter._requests
