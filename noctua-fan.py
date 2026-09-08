#!/usr/bin/env python3
# Copyright (c) 2026 Renaud Allard <renaud@allard.it>
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""Noctua NF-A4x20 5V PWM controller for Raspberry Pi 4.

Wiring (Noctua NF-A4x20 5V PWM):
    Yellow  +5V    -> physical pin 4
    Black   GND    -> physical pin 6
    Blue    PWM    -> physical pin 12  (GPIO18, hardware PWM0)
    Green   Tach   -> physical pin 18  (GPIO24, internal pull-up)

Requires 'dtoverlay=pwm-2chan' in /boot/firmware/config.txt.
Requires: sudo apt install -y python3-gpiozero python3-lgpio
"""

import argparse
import glob
import os
import signal
import sys
import time

from gpiozero import DigitalInputDevice  # type: ignore[import-untyped]

# ---- configuration ---------------------------------------------------------
PWM_CHANNEL = 0  # channel 0 == GPIO18 under dtoverlay=pwm-2chan
PWM_PERIOD_NS = 40_000  # 40 us == 25 kHz (Noctua target frequency)
TACH_GPIO = 24  # BCM numbering
PULSES_PER_REV = 2
INTERVAL = 2.0  # seconds between decisions

# Fan curves, in order of increasing aggressiveness. Each entry is the
# temperature at or below which the fan stops completely, in both directions,
# and the (CPU temp in C, duty cycle %) steps in ascending order.
#
# Every mode holds its off point 3 C below its first step. That dead band is
# what keeps the fan from chattering on and off at idle, so a new mode should
# preserve it. Duty below 20% is undefined for these fans, so no curve lands
# between 0 and 20.
MODES = {
    "quiet": (30.0, [(0, 0), (33, 20), (36, 40), (39, 60), (42, 80), (45, 100)]),
    "moderate": (30.0, [(0, 0), (33, 40), (36, 60), (39, 80), (42, 100)]),
    "aggressive": (29.0, [(0, 0), (32, 60), (35, 80), (38, 100)]),
    "maximum": (28.0, [(0, 0), (31, 100)]),
}
DEFAULT_MODE = "moderate"

# Only applied on cooldown, to stop the fan chattering at a step boundary.
HYSTERESIS_C = 2.0

# Written every INTERVAL for tmux and friends. Set to None to disable.
STATUS_FILE = "/run/noctua-fan/status"

# BCM2711 (Pi 4) PWM0 peripheral address; used to pick the right pwmchip
# rather than assuming a chip number, which has changed between kernels.
PWM_DEVICE_SUFFIX = "fe20c000.pwm"
# ---------------------------------------------------------------------------


def find_pwmchip():
    """Return the sysfs path of the SoC PWM block, not a guessed number."""
    for chip in sorted(glob.glob("/sys/class/pwm/pwmchip*")):
        try:
            dev = os.path.realpath(os.path.join(chip, "device"))
        except OSError:
            continue
        if dev.endswith(PWM_DEVICE_SUFFIX):
            return chip
    fallback = "/sys/class/pwm/pwmchip0"
    if os.path.isdir(fallback):
        return fallback
    sys.exit("No PWM chip found. Is 'dtoverlay=pwm-2chan' in config.txt?")


def write(path, value):
    with open(path, "w") as f:
        f.write(str(value))


class Pwm:
    def __init__(self, chip, channel, period_ns):
        self.dir = os.path.join(chip, f"pwm{channel}")
        if not os.path.isdir(self.dir):
            write(os.path.join(chip, "export"), channel)
            for _ in range(50):  # udev needs a moment to chmod
                if os.access(os.path.join(self.dir, "period"), os.W_OK):
                    break
                time.sleep(0.02)
        write(os.path.join(self.dir, "enable"), 0)
        write(os.path.join(self.dir, "duty_cycle"), 0)  # must precede period
        write(os.path.join(self.dir, "period"), period_ns)
        try:
            write(os.path.join(self.dir, "polarity"), "normal")
        except OSError:
            pass  # not writable on some kernels
        self.period = period_ns
        write(os.path.join(self.dir, "enable"), 1)

    def set_duty(self, percent):
        percent = max(0, min(100, int(percent)))
        write(os.path.join(self.dir, "duty_cycle"), int(self.period * percent / 100))


class Tach:
    def __init__(self, gpio):
        self._count = 0
        self._t0 = time.monotonic()
        # pull_up=True -> internal pull-up; "activated" == line pulled low
        self._dev = DigitalInputDevice(gpio, pull_up=True)
        self._dev.when_activated = self._tick

    def _tick(self):
        self._count += 1

    def read_rpm(self):
        now = time.monotonic()
        elapsed, self._t0 = now - self._t0, now
        pulses, self._count = self._count, 0
        if elapsed <= 0:
            return 0.0
        return (pulses / elapsed) * 60.0 / PULSES_PER_REV


def cpu_temp():
    with open("/sys/class/thermal/thermal_zone0/temp") as f:
        return int(f.read().strip()) / 1000.0


def publish(path, text):
    """Write the status line atomically so readers never see a partial line."""
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(text + "\n")
        os.replace(tmp, path)
    except OSError:
        pass  # never let a status-file problem kill fan control


def target_duty(temp, current, off_at, curve):
    if temp <= off_at:
        return 0
    rising = 0
    for threshold, duty in curve:
        if temp >= threshold:
            rising = duty
    if rising >= current:
        return rising
    falling = 0
    for threshold, duty in curve:
        if temp >= threshold - HYSTERESIS_C:
            falling = duty
    return min(current, falling)


def mode_summary(name):
    """One line describing a curve, shared by --help and the startup log."""
    off_at, curve = MODES[name]
    steps = " ".join(f"{t}C:{d}%" for t, d in curve if d)
    return f"off <={off_at:.0f}C  {steps}"


def mode_help():
    """Render the curve table so --help can never drift from MODES."""
    lines = ["fan curves:"]
    lines += [f"  {name:<11} {mode_summary(name)}" for name in MODES]
    return "\n".join(lines)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="noctua-fan",
        description="Temperature-driven speed control for a Noctua NF-A4x20 "
        "5V PWM fan on a Raspberry Pi 4.",
        epilog=mode_help(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=list(MODES),
        default=DEFAULT_MODE,
        help="fan curve to run (default: %(default)s)",
    )
    return parser.parse_args(argv)


def main():
    args = parse_args()
    off_at, curve = MODES[args.mode]

    pwm = Pwm(find_pwmchip(), PWM_CHANNEL, PWM_PERIOD_NS)
    tach = Tach(TACH_GPIO)
    duty = 100
    pwm.set_duty(duty)

    def bail(signum, frame):
        pwm.set_duty(100)  # fail safe: full speed, never a stopped fan
        sys.exit(0)

    signal.signal(signal.SIGTERM, bail)
    signal.signal(signal.SIGINT, bail)

    print(f"mode {args.mode}: {mode_summary(args.mode)}", flush=True)

    while True:
        time.sleep(INTERVAL)
        temp = cpu_temp()
        duty = target_duty(temp, duty, off_at, curve)
        pwm.set_duty(duty)
        rpm = tach.read_rpm()

        if duty == 0:
            line = f"off {temp:.0f}C"
        else:
            line = f"{rpm:.0f}rpm {temp:.0f}C"
        publish(STATUS_FILE, line)

        print(f"{temp:.1f}C  duty={duty:3d}%  {rpm:5.0f} rpm", flush=True)


if __name__ == "__main__":
    main()
