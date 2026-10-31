"""Derived flow estimates for the condenser loop and the brine loop (requirement-31).

No register reports a flow. A loop's volumetric flow follows from an energy balance over a
sliding window of library update dicts::

    flow [L/min] = 60 × Σ P·dt / (ρ · cp · Σ ΔT·dt)

with P the loop's heat [kW], ΔT its temperature difference [K], ρ [kg/L] and cp
[kJ/(kg·K)] its fluid. The ratio of sums weights each sample by its own ΔT, so one sample
with a small ΔT cannot drive the result toward infinity. The window must cover
``window_s`` of uninterrupted compressor running and its mean ΔT must reach
``min_delta_t``; otherwise there is no calculated value.

Each calculated value updates a calibration factor [L/min per %] (an exponential mean of
flow / pump speed). Without a calculated value, a calibrated estimator reports factor ×
pump speed ("pump_speed"); an uncalibrated one reports None ("unknown"). A value that is not
finite or lies outside 0 … ``max_flow`` is never reported.

The module performs no Modbus I/O: the consumer feeds it the dicts ``ThermiaGenesis``
returns and owns the estimator's state. It never raises on data.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass

from .const import (
    ATTR_INPUT_BRINE_CIRCULATION_PUMP_SPEED,
    ATTR_INPUT_BRINE_IN_TEMPERATURE,
    ATTR_INPUT_BRINE_OUT_TEMPERATURE,
    ATTR_INPUT_COMPRESSOR_SPEED_RPM,
    ATTR_INPUT_CONDENSER_CIRCULATION_PUMP_SPEED,
    ATTR_INPUT_CONDENSER_IN_TEMPERATURE,
    ATTR_INPUT_CONDENSER_OUT_TEMPERATURE,
    ATTR_INPUT_CURRENT_CONSUMED_POWER,
    ATTR_INPUT_CURRENT_POWER_OUTPUT,
)

_LOGGER = logging.getLogger(__name__)

CALCULATED = "calculated"
PUMP_SPEED = "pump_speed"
UNKNOWN = "unknown"

#: Weight of a new calculated window in the calibration factor's exponential mean.
CALIBRATION_WEIGHT = 0.1
#: A pump speed [%] outside this range is not trusted.
PUMP_RANGE = (0.0, 100.0)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite(value) -> float | None:
    """``value`` as a float when it is a finite number, else None."""
    if not _is_number(value) or not math.isfinite(value):
        return None
    return float(value)


@dataclass(frozen=True)
class Fluid:
    """Heat transfer fluid: specific heat capacity cp [kJ/(kg·K)] and density [kg/L]."""

    cp: float
    density: float
    source: str

    def __post_init__(self) -> None:
        for name in ("cp", "density"):
            value = getattr(self, name)
            if _finite(value) is None or value <= 0:
                raise ValueError(f"fluid {name} must be a positive finite number, got {value!r}")

    @property
    def volumetric_heat_capacity(self) -> float:
        """ρ · cp [kJ/(L·K)]"""
        return self.density * self.cp


# DHF-REQ: genesis-wp/requirement-31
WATER = Fluid(4.18, 1.00, "water, textbook values")
BRINE_MEG25 = Fluid(
    3.80, 1.04,
    "25 % ethylene glycol per WPE-I installation manual 7.7.3 (borehole); textbook values at 0-10 °C, about ±3 %",
)


@dataclass(frozen=True)
class Loop:
    """Where a loop's heat [kW], temperature difference [K] and pump speed [%] come from.

    heat = ``heat_in`` − ``heat_out`` (``heat_out`` optional); ΔT = ``warm`` − ``cold``.
    """

    name: str
    heat_in: str
    heat_out: str | None
    warm: str
    cold: str
    pump: str
    fluid: Fluid


CONDENSER = Loop(
    "condenser", ATTR_INPUT_CURRENT_POWER_OUTPUT, None,
    ATTR_INPUT_CONDENSER_OUT_TEMPERATURE, ATTR_INPUT_CONDENSER_IN_TEMPERATURE,
    ATTR_INPUT_CONDENSER_CIRCULATION_PUMP_SPEED, WATER,
)
BRINE = Loop(
    "brine", ATTR_INPUT_CURRENT_POWER_OUTPUT, ATTR_INPUT_CURRENT_CONSUMED_POWER,
    ATTR_INPUT_BRINE_IN_TEMPERATURE, ATTR_INPUT_BRINE_OUT_TEMPERATURE,
    ATTR_INPUT_BRINE_CIRCULATION_PUMP_SPEED, BRINE_MEG25,
)


@dataclass(frozen=True)
class FlowEstimate:
    """A loop's flow [L/min] or None, how it was obtained, and the calibration [L/min per %]."""

    value: float | None
    source: str
    calibration: float | None


@dataclass(frozen=True)
class _Sample:
    timestamp: float
    running: bool
    heat: float | None
    delta_t: float | None
    pump: float | None


def _difference(data: Mapping, plus: str, minus: str | None) -> float | None:
    a = _finite(data.get(plus))
    b = 0.0 if minus is None else _finite(data.get(minus))
    if a is None or b is None:
        return None
    return _finite(a - b)


# DHF-REQ: genesis-wp/requirement-31
class FlowEstimator:
    """Windowed energy-balance flow estimate for one loop; feed it with :meth:`add`."""

    def __init__(self, loop: Loop, fluid: Fluid | None = None, window_s: float = 300,
                 min_delta_t: float = 2.0, max_flow: float = 60.0) -> None:
        if not isinstance(loop, Loop):
            raise ValueError(f"loop must be a Loop, got {loop!r}")
        if fluid is not None and not isinstance(fluid, Fluid):
            raise ValueError(f"fluid must be a Fluid, got {fluid!r}")
        for name, value in (("window_s", window_s), ("min_delta_t", min_delta_t), ("max_flow", max_flow)):
            if _finite(value) is None or value <= 0:
                raise ValueError(f"{name} must be a positive finite number, got {value!r}")
        self.loop = loop
        self.fluid = fluid or loop.fluid
        self.window_s = float(window_s)
        self.min_delta_t = float(min_delta_t)
        self.max_flow = float(max_flow)
        self._window: deque[_Sample] = deque()
        self._last_timestamp: float | None = None
        self._calibration: float | None = None
        self._estimate = FlowEstimate(None, UNKNOWN, None)

    @property
    def calibration(self) -> float | None:
        """Learned flow per pump speed [L/min per %], or None before the first calculated window."""
        return self._calibration

    def add(self, timestamp_s: float, data: Mapping) -> FlowEstimate:
        """Add one update dict taken at ``timestamp_s`` [s]; returns the current estimate.

        A sample whose timestamp is not finite or not after the previous one is dropped and
        the previous estimate returned.
        """
        timestamp = _finite(timestamp_s)
        if timestamp is None or (self._last_timestamp is not None and timestamp <= self._last_timestamp):
            _LOGGER.debug("%s flow: sample at %r dropped, timestamp not increasing", self.loop.name, timestamp_s)
            return self._estimate
        self._last_timestamp = timestamp
        sample = self._sample(timestamp, data if isinstance(data, Mapping) else {})
        self._update_window(sample)
        flow = self._calculated()
        if flow is not None:
            self._calibrate(flow, sample.pump)
            self._estimate = FlowEstimate(flow, CALCULATED, self._calibration)
        else:
            self._estimate = self._fallback(sample.pump)
        return self._estimate

    def _sample(self, timestamp: float, data: Mapping) -> _Sample:
        loop = self.loop
        speed = _finite(data.get(ATTR_INPUT_COMPRESSOR_SPEED_RPM))
        heat = _difference(data, loop.heat_in, loop.heat_out)
        delta_t = _difference(data, loop.warm, loop.cold)
        pump = _finite(data.get(loop.pump))
        if pump is not None and not PUMP_RANGE[0] <= pump <= PUMP_RANGE[1]:
            pump = None
        running = speed is not None and speed > 0 and heat is not None and heat > 0
        return _Sample(timestamp, running, heat, delta_t, pump)

    def _update_window(self, sample: _Sample) -> None:
        """Keep an uninterrupted run of valid running samples that just covers ``window_s``."""
        if not sample.running or sample.delta_t is None:
            if self._window:
                _LOGGER.debug("%s flow: window cleared (running=%s, ΔT=%s)", self.loop.name, sample.running,
                              sample.delta_t)
            self._window.clear()
            return
        self._window.append(sample)
        cutoff = sample.timestamp - self.window_s
        while len(self._window) > 1 and self._window[1].timestamp <= cutoff:
            self._window.popleft()

    def _calculated(self) -> float | None:
        """The windowed energy-balance flow [L/min], or None when the window does not support one."""
        window = self._window
        if len(window) < 2 or window[-1].timestamp - window[0].timestamp < self.window_s:
            return None
        energy = duration = delta_t_time = 0.0
        for previous, current in zip(window, list(window)[1:]):
            dt = current.timestamp - previous.timestamp
            energy += current.heat * dt
            delta_t_time += current.delta_t * dt
            duration += dt
        mean_delta_t = _finite(delta_t_time / duration)
        if mean_delta_t is None or mean_delta_t < self.min_delta_t:
            _LOGGER.debug("%s flow: mean ΔT %s K below %.2f K, no calculated value", self.loop.name, mean_delta_t,
                          self.min_delta_t)
            return None
        flow = _finite(60.0 * energy / (self.fluid.volumetric_heat_capacity * delta_t_time))
        return self._plausible(flow, CALCULATED)

    def _plausible(self, flow: float | None, source: str) -> float | None:
        if flow is None or not 0.0 <= flow <= self.max_flow:
            _LOGGER.debug("%s flow: %s value %s rejected, outside 0 … %.1f L/min", self.loop.name, source, flow,
                          self.max_flow)
            return None
        _LOGGER.debug("%s flow: %s %.3f L/min", self.loop.name, source, flow)
        return flow

    def _calibrate(self, flow: float, pump: float | None) -> None:
        if pump is None or pump <= 0:
            return
        factor = _finite(flow / pump)
        if factor is None:
            return
        if self._calibration is None:
            self._calibration = factor
        else:
            self._calibration += CALIBRATION_WEIGHT * (factor - self._calibration)
        _LOGGER.debug("%s flow: calibration %.4f L/min per %%", self.loop.name, self._calibration)

    def _fallback(self, pump: float | None) -> FlowEstimate:
        if self._calibration is None or pump is None:
            _LOGGER.debug("%s flow: unknown (calibration %s, pump %s)", self.loop.name, self._calibration, pump)
            return FlowEstimate(None, UNKNOWN, self._calibration)
        flow = self._plausible(_finite(self._calibration * pump), PUMP_SPEED)
        if flow is None:
            return FlowEstimate(None, UNKNOWN, self._calibration)
        return FlowEstimate(flow, PUMP_SPEED, self._calibration)
