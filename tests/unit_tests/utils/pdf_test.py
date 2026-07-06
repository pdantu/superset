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
from io import BytesIO

import pytest
from PIL import Image, UnidentifiedImageError

from superset.commands.report.exceptions import ReportSchedulePdfFailedError
from superset.utils.pdf import build_pdf_from_screenshots


def _make_png(color: str = "red", mode: str = "RGB") -> bytes:
    buf = BytesIO()
    Image.new(mode, (10, 10), color).save(buf, "PNG")
    return buf.getvalue()


def test_build_pdf_from_single_screenshot() -> None:
    pdf = build_pdf_from_screenshots([_make_png()])

    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF")


def test_build_pdf_from_multiple_screenshots() -> None:
    pdf = build_pdf_from_screenshots([_make_png("red"), _make_png("blue")])

    assert pdf.startswith(b"%PDF")


def test_build_pdf_converts_rgba_screenshots() -> None:
    rgba_png = _make_png(mode="RGBA")

    pdf = build_pdf_from_screenshots([rgba_png])

    assert pdf.startswith(b"%PDF")


def test_build_pdf_with_no_screenshots_raises() -> None:
    with pytest.raises(ReportSchedulePdfFailedError):
        build_pdf_from_screenshots([])


def test_build_pdf_with_invalid_image_bytes_raises() -> None:
    with pytest.raises(UnidentifiedImageError):
        build_pdf_from_screenshots([b"not an image"])
