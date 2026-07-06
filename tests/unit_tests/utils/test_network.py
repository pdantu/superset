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
import pytest

from superset.utils.network import is_valid_port


@pytest.mark.parametrize(
    "port,expected",
    [
        (1, True),
        (80, True),
        (443, True),
        (8080, True),
        (65535, True),
        (0, False),
        (-1, False),
        (65536, False),
        (100000, False),
    ],
)
def test_is_valid_port(port: int, expected: bool) -> None:
    assert is_valid_port(port) == expected
