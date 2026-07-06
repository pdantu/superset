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
import socket
import subprocess

import pytest
from pytest_mock import MockerFixture

from superset.utils.network import (
    is_host_up,
    is_hostname_valid,
    is_port_open,
)


def test_is_hostname_valid_when_resolvable(mocker: MockerFixture) -> None:
    getaddrinfo = mocker.patch("superset.utils.network.socket.getaddrinfo")

    assert is_hostname_valid("example.com") is True
    getaddrinfo.assert_called_once_with("example.com", None)


def test_is_hostname_valid_when_unresolvable(mocker: MockerFixture) -> None:
    mocker.patch(
        "superset.utils.network.socket.getaddrinfo",
        side_effect=socket.gaierror,
    )

    assert is_hostname_valid("does-not-resolve.invalid") is False


def test_is_port_open_when_connection_succeeds(mocker: MockerFixture) -> None:
    mocker.patch(
        "superset.utils.network.socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 80))],
    )
    sock = mocker.MagicMock()
    mocker.patch("superset.utils.network.socket.socket", return_value=sock)

    assert is_port_open("127.0.0.1", 80) is True
    sock.connect.assert_called_once_with(("127.0.0.1", 80))
    sock.close.assert_called_once()


def test_is_port_open_when_connection_refused(mocker: MockerFixture) -> None:
    mocker.patch(
        "superset.utils.network.socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 80))],
    )
    sock = mocker.MagicMock()
    sock.connect.side_effect = OSError
    mocker.patch("superset.utils.network.socket.socket", return_value=sock)

    assert is_port_open("127.0.0.1", 80) is False
    sock.close.assert_called_once()


@pytest.mark.parametrize(
    "return_code,expected",
    [
        (0, True),
        (1, False),
    ],
)
def test_is_host_up(mocker: MockerFixture, return_code: int, expected: bool) -> None:
    mocker.patch(
        "superset.utils.network.subprocess.call",
        return_value=return_code,
    )

    assert is_host_up("example.com") is expected


def test_is_host_up_when_ping_times_out(mocker: MockerFixture) -> None:
    mocker.patch(
        "superset.utils.network.subprocess.call",
        side_effect=subprocess.TimeoutExpired(cmd="ping", timeout=5),
    )

    assert is_host_up("example.com") is False
