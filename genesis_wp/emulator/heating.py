"""Heating model: supply set points from the heating curves (requirement-15).

Verified on the real controller (formal_review-1): a circuit's supply set point is the
linear interpolation of its seven curve points over the outdoor temperature (input 13),
clamped to the circuit's supply limits. HC1 (holding 6-12, limits 4/3) gives input 18;
HC2, the mixing circuit (holding 109-115, limits 107/108), gives input 147.

The measured temperatures follow with a first-order lag: T33 (input 12) toward the HC1 set
point, the buffer T31 (input 41) toward T33, and T32 (input 40) toward the HC2 set point but
never above the buffer it mixes from. The mixing valve (input 43) is fully open while the
buffer is below the HC2 set point. Not modeled: the room set point's shift of the curve
(observed once, change_request-17) and the heating-season stop (holding 16).

Passive cooling (requirement-19, design_decision-5): with coil 33 on and HC2's mode (holding
298: 0 heat, 1 cool, 2 auto) at cool, or at auto with the outdoor temperature at or above the
cooling season start (holding 303), HC2 cools. Its target (input 147) is then holding 302, T32
follows it, the valve is fully open, input 1 reports 8 (passive cooling) and discrete input 245
is on. Modeled from the Stiebel document, not yet observed on the plant.
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
VALVE = 43
SET_POINT_HC2 = 147
INPUTS = (OUTDOOR, T33, SET_POINT_HC1, T32, T31, VALVE, SET_POINT_HC2, DEMAND)

SCALE = 100  # all of them: °C or % at scale 100

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
    valve: float
    hold_outdoor: bool = False
    #: Passive cooling inputs (raw): coil 33, holding 298, and the captured input 1 / DI 245 to restore.
    passive_enabled: bool = False
    mode_hc2: int = 0
    demand_raw: int = 0
    indication_raw: int = 0
    _start_outdoor: float = field(init=False)

    def __post_init__(self) -> None:
        self._start_outdoor = self.outdoor

    @classmethod
    def from_values(cls, holding_raw, input_raw, outdoor: float | None = None, coil_raw=None, dinput_raw=None) -> HeatingModel:
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
            valve=value(input_raw, VALVE),
            hold_outdoor=outdoor is not None,
        )

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
        self.t33 += LAG_SUPPLY * (self.set_point_hc1 - self.t33)
        self.t31 += LAG_BUFFER * (self.t33 - self.t31)
        target_hc2 = self.set_point_hc2
        if self.cooling:
            # Cold from the brine heat exchanger, not from the buffer.
            self.t32 += LAG_SUPPLY * (target_hc2 - self.t32)
            self.valve = 100.0
            return
        self.t32 += LAG_SUPPLY * (min(target_hc2, self.t31) - self.t32)
        # Fully open while the buffer cannot reach the HC2 set point; otherwise close 10 %/K above it.
        self.valve = 100.0 if self.t31 <= target_hc2 else max(0.0, 100.0 - 10.0 * (self.t31 - target_hc2))

    def inputs(self) -> dict[int, int]:
        """Raw input register values (unsigned 16-bit) the model owns."""
        values = {
            OUTDOOR: self.outdoor,
            T33: self.t33,
            SET_POINT_HC1: self.set_point_hc1,
            T32: self.t32,
            T31: self.t31,
            VALVE: self.valve,
            SET_POINT_HC2: self.set_point_hc2,
        }
        raw = {a: round(v * SCALE) & 0xFFFF for a, v in values.items()}
        raw[DEMAND] = DEMAND_PASSIVE_COOLING if self.cooling else self.demand_raw
        return raw

    def discrete_inputs(self) -> dict[int, bool]:
        """Discrete inputs the model owns."""
        return {PASSIVE_COOLING_ACTIVE: True if self.cooling else bool(self.indication_raw)}
