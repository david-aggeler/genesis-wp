"""Plant read tests: one test set for the emulator and, read-only, the real heat pump (requirement-25).

Each test applies one invariant from ``tests/plant.py`` to a snapshot of the selected target:
every packaged emulator profile by default, the real heat pump on the owner's live opt-in.
"""

from __future__ import annotations

import pytest

from .plant import CHECKS

pytestmark = pytest.mark.plant_read


# DHF-REQ: genesis-wp/requirement-25  DHF-TEST: genesis-wp/ac-94
@pytest.mark.parametrize("check", CHECKS, ids=lambda check: check.__name__)
async def test_plant(plant, check):
    assert check(plant) == []
