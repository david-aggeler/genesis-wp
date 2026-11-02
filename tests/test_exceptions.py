"""Public exception message contract."""

import pytest

from genesis_wp import (
    GenesisInvalidWriteError,
    GenesisNotWritableError,
    GenesisReadOnlyError,
    ThermiaConnectionError,
    ThermiaException,
)


# DHF-TEST: genesis-wp/requirement-37
# DHF-TEST: genesis-wp/ac-146 genesis-wp/ac-149
@pytest.mark.parametrize(
    "exception_type",
    [
        ThermiaException,
        ThermiaConnectionError,
        GenesisReadOnlyError,
        GenesisNotWritableError,
        GenesisInvalidWriteError,
    ],
)
def test_exception_exposes_message(exception_type):
    err = exception_type("refusing x")

    assert err.message == "refusing x"
    assert err.code == "refusing x"
    assert str(err) == "refusing x"
    assert err.args == ("refusing x",)
    assert repr(err) == f"{exception_type.__name__}('refusing x')"
