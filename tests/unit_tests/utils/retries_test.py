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
from unittest.mock import MagicMock

import pytest

from superset.utils.retries import retry_call


def test_retry_call_returns_value_on_first_success() -> None:
    """A function that succeeds immediately returns its value and is called once."""
    func = MagicMock(return_value="success")

    result = retry_call(func, interval=0)

    assert result == "success"
    func.assert_called_once_with()


def test_retry_call_retries_until_success() -> None:
    """A function that raises before succeeding is retried until it returns."""
    counter = {"calls": 0}

    def flaky() -> str:
        counter["calls"] += 1
        if counter["calls"] < 3:
            raise ValueError("boom")
        return "ok"

    result = retry_call(flaky, exception=ValueError, interval=0, max_tries=5)

    assert result == "ok"
    assert counter["calls"] == 3


def test_retry_call_gives_up_after_max_tries() -> None:
    """The wrapped exception is re-raised once max_tries is exhausted."""
    counter = {"calls": 0}

    def always_fails() -> None:
        counter["calls"] += 1
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        retry_call(always_fails, exception=ValueError, interval=0, max_tries=3)

    assert counter["calls"] == 3


def test_retry_call_does_not_retry_unlisted_exception() -> None:
    """Exceptions other than the configured type propagate without retrying."""
    counter = {"calls": 0}

    def raises_key_error() -> None:
        counter["calls"] += 1
        raise KeyError("nope")

    with pytest.raises(KeyError):
        retry_call(raises_key_error, exception=ValueError, interval=0, max_tries=3)

    assert counter["calls"] == 1


def test_retry_call_forwards_fargs_and_fkwargs() -> None:
    """fargs and fkwargs are forwarded to the wrapped function."""
    func = MagicMock(return_value="result")

    result = retry_call(
        func,
        interval=0,
        fargs=[1, "two"],
        fkwargs={"three": 3, "four": "4"},
    )

    assert result == "result"
    func.assert_called_once_with(1, "two", three=3, four="4")


def test_retry_call_forwards_fargs_across_retries() -> None:
    """fargs/fkwargs are passed on every retry, not just the first attempt."""
    counter = {"calls": 0}

    def flaky(value: int, *, label: str) -> str:
        counter["calls"] += 1
        if counter["calls"] < 3:
            raise ValueError("retry me")
        return f"{label}={value}"

    result = retry_call(
        flaky,
        exception=ValueError,
        interval=0,
        max_tries=5,
        fargs=[42],
        fkwargs={"label": "answer"},
    )

    assert result == "answer=42"
    assert counter["calls"] == 3
