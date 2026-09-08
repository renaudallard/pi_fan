#!/usr/bin/env python3
"""Noctua NF-A4x20 5V PWM controller for Raspberry Pi 4.

Wiring (Noctua NF-A4x20 5V PWM):
    Yellow  +5V    -> physical pin 4
    Black   GND    -> physical pin 6
    Blue    PWM    -> physical pin 12  (GPIO18, hardware PWM0)
    Green   Tach   -> physical pin 18  (GPIO24, internal pull-up)

Requires 'dtoverlay=pwm-2chan' in /boot/firmware/config.txt.
Requires: sudo apt install -y python3-gpiozero python3-lgpio
"""

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

# Fan is completely off at or below this temperature, in both directions.
OFF_AT_OR_BELOW_C = 30.0

# (CPU temp in C, duty cycle %) -- ascending. 100% at 45 C.
CURVE = [(0, 0), (33, 20), (36, 40), (39, 60), (42, 80), (45, 100)]

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


def target_duty(temp, current):
    if temp <= OFF_AT_OR_BELOW_C:
        return 0
    rising = 0
    for threshold, duty in CURVE:
        if temp >= threshold:
            rising = duty
    if rising >= current:
        return rising
    falling = 0
    for threshold, duty in CURVE:
        if temp >= threshold - HYSTERESIS_C:
            falling = duty
    return min(current, falling)


def main():
    pwm = Pwm(find_pwmchip(), PWM_CHANNEL, PWM_PERIOD_NS)
    tach = Tach(TACH_GPIO)
    duty = 100
    pwm.set_duty(duty)

    def bail(signum, frame):
        pwm.set_duty(100)  # fail safe: full speed, never a stopped fan
        sys.exit(0)

    signal.signal(signal.SIGTERM, bail)
    signal.signal(signal.SIGINT, bail)

    while True:
        time.sleep(INTERVAL)
        temp = cpu_temp()
        duty = target_duty(temp, duty)
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
