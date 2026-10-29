import pytest

from genesis_wp import ThermiaGenesis
from genesis_wp.const import *

pytestmark = pytest.mark.unit

def test_legacy_model_identifiers_unchanged():
    assert MODEL_MEGA == "mega"
    assert MODEL_INVERTER == "inverter"

def test_new_models_and_aliases():
    assert MODEL_ALIASES["thermia calibra rxt"] == MODEL_CALIBRA_RXT
    assert MODEL_ALIASES["stiebel-eltron wpe-i 07.1"] == MODEL_STIEBEL_WPE_I_071
    # The WPE-I covers every address the Calibra reads, except addresses a WPE-I firmware
    # profile does not implement (change_request-14; includes inputs 31-35, not in the
    # Stiebel document), plus its own KSM and fan-test ranges.
    from genesis_wp.emulator import get_profile

    from .harness import PROFILES

    blocks = {REG_COIL: "coils", REG_DISCRETE_INPUT: "discrete_inputs", REG_INPUT: "input_registers", REG_HOLDING: "holding_registers"}
    profiles = [get_profile(p) for p in PROFILES]

    def covered(ranges):
        return {a for start, end in ranges for a in range(start, end + 1)}

    for regtype, ranges in REGISTER_RANGES[MODEL_CALIBRA_RXT].items():
        missing = covered(ranges) - covered(REGISTER_RANGES[MODEL_STIEBEL_WPE_I_071][regtype])
        served = {a for a in missing if all(p.blocks[blocks[regtype]].implemented(a) for p in profiles)}
        assert served == set(), regtype
    assert set(range(31, 36)) <= covered(REGISTER_RANGES[MODEL_CALIBRA_RXT][REG_INPUT]) - covered(REGISTER_RANGES[MODEL_STIEBEL_WPE_I_071][REG_INPUT])

def test_v17_register_flags():
    assert REGISTERS[ATTR_INPUT_CONTROL_SOFTWARE_VERSION_MAJOR][MODEL_CALIBRA_RXT]
    assert REGISTERS[ATTR_INPUT_OUTDOOR_TEMPERATURE][MODEL_CALIBRA_RXT]
    assert not REGISTERS[ATTR_INPUT_CONTROL_SOFTWARE_VERSION_MAJOR][MODEL_INVERTER]
