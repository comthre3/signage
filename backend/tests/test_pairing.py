import re

import pytest
from fastapi.testclient import TestClient

from main import (
    PAIR_CODE_CHARSET,
    PAIR_CODE_LENGTH,
    app,
    generate_pair_code_v2,
)


VALID_CODE = re.compile(f"^[{re.escape(PAIR_CODE_CHARSET)}]{{{PAIR_CODE_LENGTH}}}$")


@pytest.fixture
def pair_client() -> TestClient:
    return TestClient(app)


def test_pair_code_charset_excludes_confusables():
    for ch in "O0I1L":
        assert ch not in PAIR_CODE_CHARSET


def test_generate_pair_code_v2_shape():
    code = generate_pair_code_v2()
    assert VALID_CODE.match(code), code
