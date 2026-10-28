"""Heating model: supply set points from the heating curves (requirement-15).

Verified on the real controller (formal_review-1): a circuit's supply set point is the
linear interpolation of its seven curve points over the outdoor temperature (input 13),
clamped to the circuit's supply limits. HC1 (holding 6-12, limits 4/3) gives input 18;
HC2, the mixing circuit (holding 109-115, limits 107/108), gives input 147.

The measured temperatures follow with a first-order lag: T33 (input 12) toward the HC1 set
point, the buffer T31 (input 41) toward T33, and T32 (input 40) toward the HC2 set point but
never above T33, the supply line the mixer draws from (requirement-20, formal_review-2).

The compressor is stopped while its speed (input 5) and the condenser pump (input 39) are both
0. Then T33 holds its last value (the sensor sees no flow) and the buffer cools toward T32.
Heat output (input 157) is linear in compressor speed; electric power (input 158) is heat
output over a COP that falls with the lift, condenser out (input 9) minus brine out (input 11).
Both are 0 while stopped. The coefficients are least-squares fits over the 20 h series.

The mixing-valve register (input 43) is the valve's control signal, not a position, and is
served as captured. Not modeled: the room set point's shift of the curve (observed once,
change_request-17), the heating-season stop (holding 16), and when the compressor starts,
stops or changes gear (issue-14).

Passive cooling (requirement-19, design_decision-5): with coil 33 on and HC2's mode (holding
298: 0 heat, 1 cool, 2 auto) at cool, or at auto with the outdoor temperature at or above the
cooling season start (holding 303), HC2 cools. Its target (input 147) is then holding 302, T32
follows it, input 1 reports 8 (passive cooling) and discrete input 245
is on. Modeled from the Stiebel document, not yet observed on the plant.

A sensor the served profile has as not fitted (a capability switched off) is left alone: the
model does not serve it (issue-12).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

# Outdoor temperatures [°C] of the seven curve points, as the controller defines them.
CURVE_X = (17.0, 10.0, 5.0, 0.0, -5.0, -15.0, -25.0)

HC1_CURVE = tuple(range(6, 13))  # holding 6-12
HC1_MIN, HC1_MAX = 4, 3
HC2_CURVE = tuple(range(109, 116))  # holding 109-115
HC2_MIN, HC2_MAX = 107, 108
HOLDING = (*HC1_CURVE, HC1_MIN, HC1_MAX, *HC2_CURVE, HC2_MIN, HC2_MAX)

# Passive cooling
MODE_HC2 = 298  # holding, raw: 0 heat, 1 cool, 2 auto
COOLING_SET_POINT = 302  # holding, °C at scale 100
COOLING_SEASON_START = 303  # holding, outdoor °C at scale 100
COOLING_HOLDING = (COOLING_SET_POINT, COOLING_SEASON_START)
ENABLE_PASSIVE_COOLING = 33  # coil
PASSIVE_COOLING_ACTIVE = 245  # discrete input
DEMAND = 1  # input: first prioritized demand
DEMAND_PASSIVE_COOLING = 8
MODE_COOL, MODE_AUTO = 1, 2

OUTDOOR = 13
T33 = 12
SET_POINT_HC1 = 18
T32 = 40
T31 = 41
SET_POINT_HC2 = 147
HEAT_OUTPUT = 157  # kW at scale 100
ELECTRIC_POWER = 158  # kW at scale 100
#: Inputs the model serves.
INPUTS = (OUTDOOR, T33, SET_POINT_HC1, T32, T31, SET_POINT_HC2, DEMAND, HEAT_OUTPUT, ELECTRIC_POWER)

# Compressor inputs the model reads; the emulator's drift (or the profile) owns them.
SPEED = 5  # rpm at scale 1
CONDENSER_OUT = 9
BRINE_OUT = 11
CONDENSER_PUMP = 39  # % at scale 100
COMPRESSOR = (SPEED, CONDENSER_OUT, BRINE_OUT, CONDENSER_PUMP)

SCALE = 100  # °C, % or kW at scale 100 (not input 5)

# Fits over the 115 running samples of the 20 h series (requirement-20, formal_review-2).
HEAT_PER_RPM = 0.00121  # [kW/rpm]
HEAT_OFFSET = 0.007  # [kW]
COP_AT_REFERENCE = 5.44  # at the reference lift
COP_PER_K = 0.160  # COP lost per K of lift
REFERENCE_LIFT = 29.8  # [K]

LAG_SUPPLY = 0.2  # fraction of the gap a supply temperature closes per step
LAG_BUFFER = 0.05
OUTDOOR_STEP = 0.02  # [K] per step, random walk
OUTDOOR_SPAN = 3.0  # [K] the walk stays within around its start


def interpolate(points: tuple[float, ...], outdoor: float) -> float:
    """Linear between the curve points; flat beyond the first and last point."""
    if outdoor >= CURVE_X[0]:
        return points[0]
    if outdoor <= CURVE_X[-1]:
        return points[-1]
    for i in range(len(CURVE_X) - 1):
        x0, x1 = CURVE_X[i], CURVE_X[i + 1]
        if x1 <= outdoor <= x0:
            return points[i] + (points[i + 1] - points[i]) * (x0 - outdoor) / (x0 - x1)
    raise AssertionError("unreachable")  # pragma: no cover


def _signed(raw: int) -> int:
    return raw - 0x10000 if raw >= 0x8000 else raw


@dataclass
class HeatingModel:
    """State of the heating model; temperatures in °C, valve in %."""

    holding: dict[int, float]
    outdoor: float
    t33: float
    t32: float
    t31: float
    hold_outdoor: bool = False
    #: Passive cooling inputs (raw): coil 33, holding 298, and the captured input 1 / DI 245 to restore.
    passive_enabled: bool = False
    mode_hc2: int = 0
    demand_raw: int = 0
    indication_raw: int = 0
    #: Input addresses whose sensor is not fitted; the model never serves them.
    not_fitted: frozenset[int] = frozenset()
    #: Compressor speed [rpm], condenser out and brine out [°C], condenser pump [%], as served.
    speed: float = 0.0
    condenser_out: float = 0.0
    brine_out: float = 0.0
    condenser_pump: float = 0.0
    _start_outdoor: float = field(init=False)

    def __post_init__(self) -> None:
        self._start_outdoor = self.outdoor

    @classmethod
    def from_values(cls, holding_raw, input_raw, outdoor: float | None = None, coil_raw=None, dinput_raw=None,
                    not_fitted=frozenset()) -> HeatingModel:
        """Build from raw block values (callables address -> raw); ``outdoor`` holds it."""
        value = lambda read, a: _signed(read(a)) / SCALE  # noqa: E731
        coil_raw = coil_raw or (lambda a: 0)
        dinput_raw = dinput_raw or (lambda a: 0)
        return cls(
            holding={a: value(holding_raw, a) for a in (*HOLDING, *COOLING_HOLDING)},
            passive_enabled=bool(coil_raw(ENABLE_PASSIVE_COOLING)),
            mode_hc2=holding_raw(MODE_HC2),
            demand_raw=input_raw(DEMAND),
            indication_raw=dinput_raw(PASSIVE_COOLING_ACTIVE),
            outdoor=value(input_raw, OUTDOOR) if outdoor is None else float(outdoor),
            t33=value(input_raw, T33),
            t32=value(input_raw, T32),
            t31=value(input_raw, T31),
            hold_outdoor=outdoor is not None,
            not_fitted=frozenset(not_fitted),
            speed=_signed(input_raw(SPEED)),
            condenser_out=value(input_raw, CONDENSER_OUT),
            brine_out=value(input_raw, BRINE_OUT),
            condenser_pump=value(input_raw, CONDENSER_PUMP),
        )

    def observe(self, raw: dict[int, int]) -> None:
        """Take the served compressor inputs (signed raw by address) the model does not own."""
        if SPEED in raw:
            self.speed = raw[SPEED]
        for address, attribute in ((CONDENSER_OUT, "condenser_out"), (BRINE_OUT, "brine_out"),
                                   (CONDENSER_PUMP, "condenser_pump")):
            if address in raw:
                setattr(self, attribute, raw[address] / SCALE)

    @property
    def running(self) -> bool:
        """The compressor runs unless its speed and the condenser pump are both 0."""
        return self.speed != 0 or self.condenser_pump != 0

    @property
    def heat_output(self) -> float:
        """[kW]"""
        return HEAT_PER_RPM * self.speed + HEAT_OFFSET if self.speed > 0 else 0.0

    @property
    def electric_power(self) -> float:
        """[kW]"""
        if self.heat_output == 0.0:
            return 0.0
        lift = self.condenser_out - self.brine_out
        return self.heat_output / (COP_AT_REFERENCE - COP_PER_K * (lift - REFERENCE_LIFT))

    @property
    def set_point_hc1(self) -> float:
        return self._clamped(HC1_CURVE, HC1_MIN, HC1_MAX)

    @property
    def cooling(self) -> bool:
        """HC2 in passive cooling: enabled, and mode cool, or auto at or above the season start."""
        if not self.passive_enabled:
            return False
        if self.mode_hc2 == MODE_COOL:
            return True
        return self.mode_hc2 == MODE_AUTO and self.outdoor >= self.holding[COOLING_SEASON_START]

    @property
    def set_point_hc2(self) -> float:
        if self.cooling:
            return self.holding[COOLING_SET_POINT]
        return self._clamped(HC2_CURVE, HC2_MIN, HC2_MAX)

    def _clamped(self, curve, low, high) -> float:
        target = interpolate(tuple(self.holding[a] for a in curve), self.outdoor)
        return min(max(target, self.holding[low]), self.holding[high])

    def write(self, address: int, raw: int) -> None:
        """A holding write the emulator applied; curve points and limits take effect on the next step."""
        if address == MODE_HC2:
            self.mode_hc2 = raw
        elif address in self.holding:
            self.holding[address] = _signed(raw) / SCALE

    def write_coil(self, address: int, on: bool) -> None:
        """A coil write the emulator applied."""
        if address == ENABLE_PASSIVE_COOLING:
            self.passive_enabled = bool(on)

    def step(self, rng: random.Random) -> None:
        if not self.hold_outdoor:
            walk = self.outdoor + rng.choice((-1, 1)) * OUTDOOR_STEP
            if abs(walk - self._start_outdoor) > OUTDOOR_SPAN:
                walk = self.outdoor - (walk - self.outdoor)  # reflect at the bound
            self.outdoor = walk
        if self.running:
            self.t33 += LAG_SUPPLY * (self.set_point_hc1 - self.t33)
            self.t31 += LAG_BUFFER * (self.t33 - self.t31)
        # else: no flow past T33, so it holds (formal_review-2 finding 3).
        target_hc2 = self.set_point_hc2
        if self.cooling:
            # Cold from the brine heat exchanger, not from the buffer.
            self.t32 += LAG_SUPPLY * (target_hc2 - self.t32)
        else:
            # The mixer draws from the supply line: T32 never above T33 (formal_review-2 finding 2).
            self.t32 += LAG_SUPPLY * (min(target_hc2, self.t33) - self.t32)
        if not self.running:
            # HC2 keeps drawing from the buffer, which cools toward T32.
            self.t31 += LAG_SUPPLY * (self.t32 - self.t31)

    def inputs(self) -> dict[int, int]:
        """Raw input register values (unsigned 16-bit) the model owns."""
        values = {
            OUTDOOR: self.outdoor,
            T33: self.t33,
            SET_POINT_HC1: self.set_point_hc1,
            T32: self.t32,
            T31: self.t31,
            SET_POINT_HC2: self.set_point_hc2,
            HEAT_OUTPUT: self.heat_output,
            ELECTRIC_POWER: self.electric_power,
        }
        raw = {a: round(v * SCALE) & 0xFFFF for a, v in values.items() if a not in self.not_fitted}
        raw[DEMAND] = DEMAND_PASSIVE_COOLING if self.cooling else self.demand_raw
        return raw

    def discrete_inputs(self) -> dict[int, bool]:
        """Discrete inputs the model owns."""
        return {PASSIVE_COOLING_ACTIVE: True if self.cooling else bool(self.indication_raw)}
