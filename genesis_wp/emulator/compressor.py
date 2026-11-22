"""Compressor cycling: an opt-in state machine for the emulator (requirement-46, issue-14).

Rules from the owner's DEV Home Assistant recorder, 2026-11-16 20:57Z to 2026-11-21 21:05Z (one-minute
grid, 7 complete cycles, read-only export):

- The compressor stops when the heating demand integral (input 30) reaches +3.8 while running and
  starts when it reaches -3.9 while stopped.
- After a stop, input 60 counts a 300 s block down to 0, with OK to start (discrete input 225)
  off until it ends. Discrete input 225 is also off for the first 20 minutes of every run.
- Input 1 / input 2 follow demand: 4 / 8 (heat) while running, 99 / 1024 (no demand) while stopped.
- A start runs 2 minutes at 2408 rpm, then 1400 rpm. Gears step one at a time when input 30 is
  beyond +-1.0, at most once per 5 minutes. After 122 minutes below 2400 rpm, a 2-minute boost to
  2408 rpm follows (likely oil return).
- Input 54 = rpm / 72. Condenser pump 35 % up to 1600 rpm, 48 % at 2408 rpm, 0 while stopped.

The input 30 law is a placeholder, not physics: as an integral of any set point error the
series fits with R² <= 0.11. The phase law below reproduces the median run (189 min) and stop
(127 min) at the series' 10-12 °C outdoor temperature. The plant modulates without stopping below
about 9.7 °C, and this law always cycles. Hence opt-in (``Emulator(compressor_cycling=True)``).
Cycling also enables the heating model's buffer heat balance and supply temperature response
to compressor output and stops (requirement-20).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

# Registers the model owns.
INTEGRAL = 30  # input, scale 100
SPEED = 5  # input, rpm, scale 1
SPEED_PERCENT = 54  # input, %, scale 100
CONDENSER_PUMP = 39  # input, %, scale 100
BLOCK = 60  # input, seconds, scale 1
STATUS = 2  # input, status bits
INPUTS = (INTEGRAL, SPEED, SPEED_PERCENT, CONDENSER_PUMP, BLOCK, STATUS)
COMPRESSOR_SIGNAL = 199  # discrete input
OK_TO_START = 225  # discrete input
DISCRETE_INPUTS = (COMPRESSOR_SIGNAL, OK_TO_START)

DEMAND_HEAT, DEMAND_NONE = 4, 99  # input 1 raw
STATUS_RUNNING, STATUS_STOPPED = 8, 1024  # input 2 raw

STOP_AT = 3.8
START_AT = -3.9
GEARS = (1400, 1600, 1800, 2000, 2400, 2700, 3200)  # [rpm]
GEAR_BAND = 1.0
GEAR_DWELL = 5  # [min]
START_RPM = 2408
START_MINUTES = 2
BOOST_RPM = 2408
BOOST_AFTER = 122  # [min] below BOOST_BELOW
BOOST_BELOW = 2400
BOOST_MINUTES = 2
BLOCK_SECONDS = 300
OK_DELAY_MINUTES = 20  # discrete input 225 off for the first minutes of a run
RPM_PER_PERCENT = 72.0
PUMP_LOW, PUMP_HIGH = 35.0, 48.0  # [%]
PUMP_LOW_RPM, PUMP_HIGH_RPM = 1600, 2408

# Placeholder input 30 phase law (requirement-46).
RUN_START = -1.6
RUN_RATE = 0.0286  # per minute
STOP_START = 2.3
STOP_HOLD = 57  # [min]
STOP_RATE = 0.089  # per minute


def pump_percent(rpm: int) -> float:
    """Condenser pump speed [%] at a compressor speed: 35 % to 1600 rpm, linear to 48 % at 2408 rpm, then held."""
    if rpm <= 0:
        return 0.0
    if rpm <= PUMP_LOW_RPM:
        return PUMP_LOW
    if rpm >= PUMP_HIGH_RPM:
        return PUMP_HIGH
    return PUMP_LOW + (PUMP_HIGH - PUMP_LOW) * (rpm - PUMP_LOW_RPM) / (PUMP_HIGH_RPM - PUMP_LOW_RPM)


def _nearest_gear(rpm: int) -> int:
    return min(range(len(GEARS)), key=lambda i: abs(GEARS[i] - rpm))


@dataclass
class CompressorModel:
    """Compressor state; one ``step`` is one simulated minute."""

    running: bool
    integral: float
    #: Minutes since the last start or stop.
    minutes: int = 0
    gear: int = 0
    since_gear_change: int = GEAR_DWELL
    #: Running minutes at a gear below BOOST_BELOW since the last start or boost.
    below: int = 0
    boost_left: int = 0
    block: int = 0
    #: Placeholder law rates, per minute; a test may set run_rate to 0 for a load that matches capacity.
    run_rate: float = RUN_RATE
    stop_rate: float = STOP_RATE

    @classmethod
    def from_values(cls, input_raw) -> CompressorModel:
        """Start from the served profile: running when input 5 is above 0, mid-run or mid-stop."""
        speed = _signed(input_raw(SPEED))
        integral = _signed(input_raw(INTEGRAL)) / 100
        if speed > 0:
            return cls(True, integral, minutes=OK_DELAY_MINUTES + 1, gear=_nearest_gear(speed))
        return cls(False, integral, minutes=STOP_HOLD)

    @property
    def rpm(self) -> int:
        if not self.running:
            return 0
        if self.minutes <= START_MINUTES:
            return START_RPM
        if self.boost_left > 0:
            return BOOST_RPM
        return GEARS[self.gear]

    @property
    def demand(self) -> int:
        """Input 1 raw: heat while running, no demand while stopped."""
        return DEMAND_HEAT if self.running else DEMAND_NONE

    def step(self, cooling: bool = False) -> None:
        """One simulated minute. Transitions use input 30 as last served, then the phase advances."""
        if cooling:
            if self.running:
                _LOGGER.debug("compressor stopped: HC2 passively cools")
                self._stop()
            else:
                self._advance_block()
            return
        if self.running and self.integral >= STOP_AT:
            _LOGGER.debug("compressor stop at input 30 = %.2f", self.integral)
            self._stop()
        elif not self.running and self.integral <= START_AT:
            _LOGGER.debug("compressor start at input 30 = %.2f", self.integral)
            self._start()
        elif self.running:
            self._run_minute()
        else:
            self._stop_minute()

    def _start(self) -> None:
        self.running = True
        self.integral = RUN_START
        self.minutes = 1
        self.gear = 0
        self.since_gear_change = 0
        self.below = 0
        self.boost_left = 0
        self.block = 0

    def _stop(self) -> None:
        self.running = False
        self.integral = STOP_START
        self.minutes = 1
        self.block = BLOCK_SECONDS
        self.boost_left = 0

    def _run_minute(self) -> None:
        demand = self.integral  # the value the last read showed
        self.minutes += 1
        self.since_gear_change += 1
        self.integral += self.run_rate
        if self.minutes <= START_MINUTES:
            return
        if self.boost_left > 0:
            self.boost_left -= 1
            return
        self._step_gear(demand)
        if GEARS[self.gear] < BOOST_BELOW:
            self.below += 1
            if self.below >= BOOST_AFTER:
                _LOGGER.debug("compressor boost after %d min below %d rpm", self.below, BOOST_BELOW)
                self.below = 0
                self.boost_left = BOOST_MINUTES
        else:
            self.below = 0

    def _step_gear(self, demand: float) -> None:
        if self.since_gear_change < GEAR_DWELL:
            return
        if demand <= -GEAR_BAND and self.gear < len(GEARS) - 1:
            self.gear += 1
        elif demand >= GEAR_BAND and self.gear > 0:
            self.gear -= 1
        else:
            return
        self.since_gear_change = 0
        _LOGGER.debug("compressor gear %d rpm at input 30 = %.2f", GEARS[self.gear], demand)

    def _stop_minute(self) -> None:
        self.minutes += 1
        self._advance_block()
        if self.minutes > STOP_HOLD:
            self.integral -= self.stop_rate

    def _advance_block(self) -> None:
        self.block = max(self.block - 60, 0)

    def inputs(self) -> dict[int, int]:
        """Raw input register values (unsigned 16-bit) the model owns."""
        rpm = self.rpm
        return {
            INTEGRAL: round(self.integral * 100) & 0xFFFF,
            SPEED: rpm & 0xFFFF,
            SPEED_PERCENT: round(rpm / RPM_PER_PERCENT * 100) & 0xFFFF,
            CONDENSER_PUMP: round(pump_percent(rpm) * 100) & 0xFFFF,
            BLOCK: self.block,
            STATUS: STATUS_RUNNING if self.running else STATUS_STOPPED,
        }

    def discrete_inputs(self) -> dict[int, bool]:
        ok = self.block == 0 and not (self.running and self.minutes <= OK_DELAY_MINUTES)
        return {COMPRESSOR_SIGNAL: self.running, OK_TO_START: ok}


def _signed(raw: int) -> int:
    return raw - 0x10000 if raw >= 0x8000 else raw
