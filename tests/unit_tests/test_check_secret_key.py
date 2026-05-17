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
from unittest.mock import MagicMock, patch

import pytest

from superset.initialization import SupersetAppInitializer


def _make_initializer(
    secret_key: str,
    debug: bool = False,
    testing: bool = False,
) -> SupersetAppInitializer:
    app = MagicMock()
    app.debug = debug
    app.config = {
        "SECRET_KEY": secret_key,
        "TESTING": testing,
    }
    init = object.__new__(SupersetAppInitializer)
    init.superset_app = app
    init.config = app.config
    return init


def test_insecure_placeholder_rejected_in_non_debug_mode() -> None:
    """The old placeholder value must cause sys.exit in non-debug mode."""
    init = _make_initializer(
        secret_key="CHANGE_ME_TO_A_COMPLEX_RANDOM_SECRET",  # noqa: S106
        debug=False,
    )
    with pytest.raises(SystemExit):
        init.check_secret_key()


def test_insecure_placeholder_rejected_in_debug_mode() -> None:
    """Debug mode must NOT silently accept the old placeholder value."""
    init = _make_initializer(
        secret_key="CHANGE_ME_TO_A_COMPLEX_RANDOM_SECRET",  # noqa: S106
        debug=True,
    )
    with pytest.raises(SystemExit):
        init.check_secret_key()


def test_empty_secret_key_rejected() -> None:
    """An empty SECRET_KEY must cause sys.exit."""
    init = _make_initializer(secret_key="", debug=False)
    with pytest.raises(SystemExit):
        init.check_secret_key()


def test_empty_secret_key_rejected_in_debug_mode() -> None:
    """An empty SECRET_KEY must cause sys.exit even in debug mode."""
    init = _make_initializer(secret_key="", debug=True)
    with pytest.raises(SystemExit):
        init.check_secret_key()


def test_valid_secret_key_accepted() -> None:
    """A proper SECRET_KEY must pass validation without error."""
    init = _make_initializer(
        secret_key="a-very-long-and-complex-random-secret-key-1234567890",  # noqa: S106
        debug=False,
    )
    init.check_secret_key()


def test_testing_mode_bypasses_check() -> None:
    """TESTING mode should bypass the secret key check."""
    init = _make_initializer(
        secret_key="",
        debug=False,
        testing=True,
    )
    init.check_secret_key()


@patch("superset.initialization.is_test", return_value=True)
def test_is_test_bypasses_check(mock_is_test: MagicMock) -> None:
    """is_test() returning True should bypass the secret key check."""
    init = _make_initializer(
        secret_key="",
        debug=False,
    )
    init.check_secret_key()
