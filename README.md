# noctua-fan

<p align="center">
  <a href="https://www.raspberrypi.com/products/raspberry-pi-4-model-b/">
    <img src="https://img.shields.io/badge/Raspberry%20Pi-4%20Model%20B-C51A4A?logo=raspberrypi&logoColor=white&style=flat-square" alt="Raspberry Pi 4 Model B"/>
  </a>
  <a href="https://noctua.at/en/nf-a4x20-5v-pwm">
    <img src="https://img.shields.io/badge/Noctua-NF--A4x20%205V%20PWM-B8860B?style=flat-square" alt="Noctua NF-A4x20 5V PWM"/>
  </a>
  <img src="https://img.shields.io/badge/PWM-hardware%2025%20kHz-0A7BBB?style=flat-square" alt="Hardware PWM at 25 kHz"/>
  <img src="https://img.shields.io/badge/python-3-3776AB?logo=python&logoColor=white&style=flat-square" alt="Python 3"/>
  <img src="https://img.shields.io/badge/init-systemd-30B6E6?logo=systemd&logoColor=white&style=flat-square" alt="systemd"/>
  <a href="./LICENSE">
    <img src="https://img.shields.io/badge/license-BSD--2--Clause-green.svg?style=flat-square" alt="BSD 2-Clause"/>
  </a>
  <a href="https://www.paypal.me/RenaudAllard">
    <img src="https://img.shields.io/badge/PayPal-Donate-blue.svg?logo=paypal&style=flat-square" alt="PayPal"/>
  </a>
</p>

---

Temperature-driven speed control for a Noctua NF-A4x20 5V PWM fan on a
Raspberry Pi 4, using the SoC's hardware PWM. One file, no daemon framework,
no pip packages, four selectable fan curves.

Developed and measured on a Raspberry Pi 4 Model B Rev 1.5 with the fan
mounted on a Geekworm P122 cooler, running at `arm_freq=2100`. Every
temperature quoted below is a property of that particular combination of
heatsink, clock speed, enclosure and room, not of the fan on its own. Treat
the numbers as a worked example and measure your own.

| | |
| --- | --- |
| **Fan** | Noctua NF-A4x20 5V PWM |
| **Host** | Raspberry Pi 4 Model B |
| **Control signal** | Hardware PWM0 on GPIO18, 25 kHz |
| **Feedback** | Tachometer on GPIO24, 2 pulses per revolution |
| **Default curve** | `moderate` |
| **Dependencies** | `python3-gpiozero`, `python3-lgpio` |
| **Status file** | `/run/noctua-fan/status` |
| **License** | [BSD 2-Clause](LICENSE) |

---

## Table of Contents

- [Why hardware PWM](#why-hardware-pwm)
- [Wiring](#wiring)
- [Boot configuration](#boot-configuration)
- [Install](#install)
- [Fan curves](#fan-curves)
- [Choosing a mode](#choosing-a-mode)
- [What the fan actually buys](#what-the-fan-actually-buys)
- [Status output](#status-output)
- [tmux](#tmux)
- [Verify](#verify)
- [Setting the speed by hand](#setting-the-speed-by-hand)
- [Files](#files)
- [License](#license)

---

## Why hardware PWM

The fan expects a 25 kHz control signal; 21-28 kHz is supported and outside
that range it behaves unpredictably. That rules out the two obvious shortcuts:

- **`dtoverlay=pwm-gpio-fan`** sets a 20 ms period, which is 50 Hz. It is meant
  for simple on/off fans, not a Noctua.
- **`RPi.GPIO` software PWM** tops out far below 25 kHz and jitters under load.

So the blue wire goes to a pin backed by the PWM peripheral, driven through
the kernel's PWM sysfs interface.

No pip packages: the script writes to PWM sysfs directly, which also avoids
Bookworm's `externally-managed-environment` refusal.

---

## Wiring

| Fan wire | Function | Pi 4 physical pin | BCM |
| -------- | -------- | ----------------- | --- |
| Yellow   | +5 V     | 4                 | -   |
| Black    | GND      | 6                 | -   |
| Blue     | PWM in   | 12                | 18  |
| Green    | Tach out | 18                | 24  |

<p align="center">
  <img src="images/wiring.jpg" alt="NF-A4x20 mounted on the Geekworm P122, with the four leads run to the GPIO header" width="420"/>
</p>

On this fan yellow is +5 V power, not a signal. Current draw is 0.1 A max.

No level shifter is needed on the blue wire: Noctua fans read both 3.3 V and
5 V as logical high. The green wire is an open-collector output and needs a
pull-up; the script enables the pin's internal one. Do not pull it up to 5 V,
Pi 4 GPIOs are 3.3 V logic and are not 5 V tolerant.

The fan's 4-pin Molex plug does not mate with the header. Use female-to-male
jumpers, or cut into the supplied NA-EC1 extension rather than the fan lead.

---

## Boot configuration

Append to the end of `/boot/firmware/config.txt` (`/boot/config.txt` before
Bookworm):

```
[all]
# Noctua NF-A4x20 5V PWM on GPIO18 (physical pin 12)
dtoverlay=pwm-2chan
```

Two things the `[all]` line and the placement guard against:

- A `dtparam=` after a `dtoverlay=` binds to that overlay rather than to the
  base device tree, so the overlay must come after every `dtparam` line.
- The stock file often ends in a model filter such as `[cm4]`. Anything
  appended below it would never load on a Pi 4 B. `[all]` resets the filter.

`dtoverlay=vc4-kms-v3d` does not conflict: it claims no GPIO. The 3.5 mm
analogue jack does share the PWM channels, so that output is lost. HDMI and
USB audio are unaffected.

If the stock `#dtoverlay=gpio-ir-tx,gpio_pin=18` line has been uncommented at
some point, it collides with GPIO18 directly.

---

## Install

```sh
sudo apt install -y python3-gpiozero python3-lgpio

sudo install -m 755 noctua-fan.py /usr/local/bin/noctua-fan.py
sudo install -m 644 noctua-fan.service /etc/systemd/system/noctua-fan.service

sudo systemctl daemon-reload
sudo systemctl enable noctua-fan.service
sudo reboot
```

To run a curve other than the built-in default, drop the mode into
`/etc/default/noctua-fan`:

```sh
printf 'NOCTUA_FAN_ARGS="--mode quiet"\n' | sudo tee /etc/default/noctua-fan
sudo systemctl restart noctua-fan.service
```

The unit reads that file through `EnvironmentFile=-/etc/default/noctua-fan`.
The leading `-` makes it optional, and an unset `$NOCTUA_FAN_ARGS` expands to
zero arguments rather than an empty one, so with no file at all the service
starts on the default curve.

Confirm which curve is live:

```sh
journalctl -u noctua-fan.service | grep '^mode'
```

---

## Fan curves

Four curves ship in the `MODES` table at the top of `noctua-fan.py`. Pick one
with `-m` / `--mode`; `noctua-fan.py --help` prints the same table, rendered
from `MODES` so it cannot drift from the code.

Every curve moves in 10% steps of duty, 1.5 C apart. The table reads as the
temperature at which each mode first asks for a given speed, so find your own
idle temperature in the rpm column's neighbourhood and read across.

| Duty | rpm | `quiet` | `moderate` | `aggressive` | `maximum` |
| ---- | ---- | ------- | ---------- | ------------ | --------- |
| off | 0 | <=30 C | <=30 C | <=29 C | <=28 C |
| 20% | 1140 | 33 C | - | - | - |
| 30% | 1818 | 34.5 C | - | - | - |
| 40% | 2418 | 36 C | 33 C | - | - |
| 50% | 2976 | 37.5 C | 34.5 C | - | - |
| 60% | 3504 | 39 C | 36 C | 32 C | - |
| 70% | 3978 | 40.5 C | 37.5 C | 33.5 C | - |
| 80% | 4422 | 42 C | 39 C | 35 C | - |
| 90% | 4812 | 43.5 C | 40.5 C | 36.5 C | - |
| 100% | 5208 | 45 C | 42 C | 38 C | 31 C |

`moderate` is the default. The rpm column was measured on this fan with the
tachometer, holding each duty in turn; expect your own to differ.

Four rules hold across every curve, and a new one should keep them:

- **Nothing lands between 0 and 20% duty.** Above 20% this fan is linear in
  duty, near enough 50 rpm per percent. Below it the fan leaves that line
  entirely: 10% duty turns at about 410 rpm where the fit predicts 840. It does
  still start and run steadily there on this unit, but Noctua specifies nothing
  below 20%, so no curve relies on it.
- **Steps are 10% of duty and 1.5 C apart.** One step is worth about 500 rpm,
  which is a change you can hear as a new speed rather than as a jump.
- **The off point sits 3 C below the first step.** That dead band is what stops
  the fan cycling on and off at idle.
- **`HYSTERESIS_C` applies on cooldown only.** Rising temperature steps the
  speed up at once; falling temperature has to drop 2 C below a threshold
  before the speed steps back down. At 2 C it is wider than the 1.5 C between
  steps, so a cooling CPU gives up more than one step at a time. `target_duty()`
  only ever uses it to lower the duty, and it holds a separate guard at the
  mode's off point, so widening it can never quietly keep the fan spinning
  below that point.

`maximum` keeps a single step on purpose: it means full speed whenever the fan
turns at all, and subdividing it would make it something other than maximum.

To run at a fixed speed with no thermal control, add a mode whose curve is a
single entry such as `[(0, 40)]`.

---

## Choosing a mode

The choice only ever changes behaviour in the idle band. Replaying all four
curves against the measured duty-to-temperature map below shows every one of
them settling at 100% under sustained full load, reached in a single step from
the lowest duty the sweep measured. Under load the curves are
indistinguishable, so pick on how loud you want the machine at rest.

Read the curve table above against your own idle temperature, which is the
figure that decides how the machine sounds nearly all the time. Note that these
thresholds are far below where a Pi 4 needs help: the Arm cores throttle
progressively between 80 C and 85 C, and there is no soft limit below that.
How much headroom a stopped fan leaves is no longer a settled question, so
treat the curves as trading noise against margin rather than as decoration;
see [What the fan actually buys](#what-the-fan-actually-buys).

---

## What the fan actually buys

Measured with `stress-ng --cpu 4` running unchanged across the whole sweep,
descending through the duty steps in 10% increments. Each point was held until
the temperature stopped moving rather than for a fixed time: a least squares
slope over a trailing 90 second window had to stay under 0.15 C per minute
twice in succession before the reading was taken, and the reading itself is a
90 second average. The CPU held 2100 MHz for all 6868 samples, so the heat
input was identical throughout and nothing was thermally capped.

The heatsink is a Geekworm P122 and the board is clocked at `arm_freq=2100`,
so these figures describe that pairing. A different cooler moves every row.

| Duty | rpm  | Temp    | vs 100% | Step gain | C per 1000 rpm |
| ---- | ---- | ------- | ------- | --------- | -------------- |
| 20%  | 1088 | 63.36 C | +15.59  |           |                |
| 30%  | 1670 | 56.86 C | +9.10   | -6.50     | 11.16          |
| 40%  | 2231 | 53.77 C | +6.00   | -3.10     | 5.52           |
| 50%  | 2808 | 51.79 C | +4.02   | -1.98     | 3.43           |
| 60%  | 3358 | 50.86 C | +3.09   | -0.93     | 1.69           |
| 70%  | 3874 | 49.87 C | +2.10   | -0.99     | 1.92           |
| 80%  | 4386 | 48.82 C | +1.05   | -1.05     | 2.05           |
| 90%  | 4849 | 48.48 C | +0.71   | -0.34     | 0.74           |
| 100% | 5323 | 47.77 C | 0       | -0.71     | 1.50           |

The rpm column is measured on one fan with the tachometer, not interpolated
from the datasheet. Noctua's anchors are 1100 rpm at 20% and 5000 rpm at 100%,
so this unit runs slightly faster than spec at the top of its range. Expect
your own to differ and measure it rather than copying these numbers.

Three things follow.

**The range is at least 15.6 C, not the 8 C first published.** Between 20% and
100% duty, 1088 to 5323 rpm, the CPU moves 15.59 C. An earlier sweep put the
same span at 8 C because it gave every point a fixed 150 seconds to settle,
which is not long enough where it matters: 20% took 616 seconds to flatten
here, and the figure the old method produced at that duty was 7.14 C too low.

**The top of the curve is close to free to give up.** 50% duty gives away
4.02 C against 100% while turning at little over half the speed. The bottom is
where the temperature is decided: the single step from 30% to 20% costs 6.50 C,
more than the whole span from 50% up to 100%. Cooling efficiency falls by
roughly a factor of six across the range, 11.16 C per 1000 rpm on the lowest
step against 1.69 to 2.05 in the 60 to 80% band. Capping a curve below 100%
costs very little, and letting one fall below 30% costs a great deal.

**What a stopped fan does is no longer known.** The earlier sweep recorded
63.8 C at 0% duty, and this README concluded from it that the fan was a comfort
device rather than a protective one. That conclusion is withdrawn. The same
63.8 C is within half a degree of what 20% duty holds here with the fan turning
at 1088 rpm, which cannot both be right, and the old 0% row carries the same
under-settling error as its 20% row. The run that would have replaced it was
cut short by a reboot during the 10% step, by which point the CPU had reached
72.06 C and was still climbing at 0.93 C per minute. Where a stopped fan
settles is unmeasured, materially higher than the figure previously published,
and possibly close enough to the 80 C throttle point to matter.

Settle time is what the first sweep got wrong, and it grows sharply as duty
falls: 100 seconds at 100% duty, 189 at 70%, 320 at 30% and 616 at 20%. Less
airflow means a longer thermal time constant. The sweep runs descending, so
every step heats toward a higher equilibrium and a reading taken too early sits
below the true value, never above, which is the direction the original figures
erred in.

These readings are better settled than the first sweep's rather than fully
settled, and the table should be read with that in mind. The criterion accepted
trailing slopes between 0.091 and 0.141 C per minute, and then every one of the
nine points drifted upward again during its 90 second measurement, by 0.29 to
0.47 C per minute and 0.36 on average. Stopping as soon as two consecutive
windows read flat biases the test toward stopping on a downward fluctuation, so
every figure here is still low by an unknown amount, in the same direction as
before and far smaller than the 7.14 C the fixed timer cost at 20%. One
consequence is worth naming: the 90% to 80% step of 0.34 C is smaller than the
0.55 C standard deviation within either point, so those two duties are not
resolved from each other. Every other step in the table is larger than the
noise inside it.

To repeat it: stop the service, drive the duty by hand as shown under
[Setting the speed by hand](#setting-the-speed-by-hand), hold a constant load,
and average `/sys/class/thermal/thermal_zone0/temp` once the reading has
stopped drifting rather than after a fixed wait. Watch the temperature while
the fan is slow and give yourself an abort threshold below 80 C, where the
firmware starts throttling and the load stops being constant.

The 10% and 0% rows are still open. Everything from 20% up settled to the
criterion above.

### Why the steps are 10% and stop at 20%

Holding each duty in turn and reading the tachometer gives a straight line from
20% upwards: `rpm = 50.4 x duty + 338`, with an R2 of 0.9916 over the nine
points from 20 to 100%. A 10% step is therefore worth about 504 rpm anywhere in
that range, which is why the curves can afford steps that fine and why each one
is audible as a distinct speed rather than as a jump.

Below 20% the line stops describing the fan. At 10% duty it settles at 408 rpm
where the fit predicts 842, roughly half the speed the trend calls for. It runs
steadily there rather than erratically, and on this unit it does start from a
standstill, but it is outside the range Noctua specifies and another fan need
not behave the same way. That is the reason no curve puts a step between 0
and 20%.

Started from a full stop rather than coasted down to, the low duties measure
426 rpm at 10%, 762 rpm at 15%, 1092 rpm at 20% and 1397 rpm at 25%. The fan
started every time, so 20% is a specification floor here rather than an observed
failure.

These rpm figures come from a shorter run than the thermal sweep above, five
seconds per point rather than a sixty second average, and at idle rather than
under load. They sit within about 8% of the sweep's numbers, which is what the
shorter window and the lighter load account for. Where a single coherent set of
duty, rpm and temperature matters, use the sweep table.

---

## Status output

Every interval the script writes one line to `/run/noctua-fan/status`, on
tmpfs, replaced atomically so a reader never sees a partial line:

```
3913rpm 38C
off 28C
```

`RuntimeDirectory=noctua-fan` in the unit creates `/run/noctua-fan` at start
with mode 0755, so an unprivileged reader needs no udev rule. The same
setting removes the directory when the service stops, so a missing file means
a stopped service. Add `RuntimeDirectoryPreserve=restart` if the last reading
should survive a restart.

Set `STATUS_FILE = None` to turn the feature off.

The journal carries a fuller line, plus the active mode at startup:

```
mode moderate: off <=30C  33C:40% 34.5C:50% 36C:60% 37.5C:70% 39C:80% 40.5C:90% 42C:100%
38.0C  duty= 70%   3913 rpm
```

---

## tmux

`tmux-status.conf` holds the status-bar lines; append them to `~/.tmux.conf`
or source the file from it, then `tmux source-file ~/.tmux.conf`.

```tmux
set -g status-interval 2
set -g status-right-length 60
set -g status-right "#(uname -n) #(cat /run/noctua-fan/status 2>/dev/null || echo n/a)"
```

`status-right-length` has to be raised: it defaults to 40 and tmux truncates
past it silently. `status-interval 2` matches the script's own interval;
the default of 15 would leave the reading stale. Below 1 second is pointless,
tmux will not run a `#()` command more than once a second.

The published line carries no percent sign on purpose. tmux expands status
templates through `strftime`, and whether that expansion also covers the
output of a `#()` job is untested here. To find out:

```sh
sudo sh -c 'echo "test 60% ok" > /run/noctua-fan/status'
```

If the status bar shows it intact, the duty cycle can be added to the
published line. The service overwrites the file on its next tick either way.

---

## Verify

```sh
raspi-gpio get 18                        # expect func=PWM0
ls /sys/class/pwm                        # expect pwmchip0
lsmod | grep pwm                         # expect pwm_bcm2835
dmesg | grep -i -e pwm -e "already requested"
systemctl status noctua-fan.service
journalctl -u noctua-fan.service -f
cat /run/noctua-fan/status
```

If `raspi-gpio get 18` still reports `OUTPUT`, another overlay has claimed
the pin. The failure looks like
`pinctrl-bcm2835 fe20a000.gpio: pin GPIO18 already requested by fe215080.spi`.

Two behaviours that are correct rather than faults: the fan runs at full
speed from power-on until the service takes over, and it returns to full
speed when the service stops, which is the deliberate fail-safe in `bail()`.

Force a full-speed transition to test the top of the curve:

```sh
sudo apt install -y stress-ng
stress-ng --cpu 4 --timeout 180s
```

---

## Setting the speed by hand

Stop the service first or the two fight over the same file.

```sh
sudo systemctl stop noctua-fan.service

C=/sys/class/pwm/pwmchip0
sudo sh -c "echo 0 > $C/export"           # skip if pwm0/ exists
sudo sh -c "echo 0 > $C/pwm0/duty_cycle"  # zero before shrinking the period
sudo sh -c "echo 40000 > $C/pwm0/period"  # 40 us == 25 kHz
sudo sh -c "echo 1 > $C/pwm0/enable"
sudo sh -c "echo 20000 > $C/pwm0/duty_cycle"   # 50%
```

`duty_cycle` may never exceed `period`, which is why the order matters.

---

## Files

| File                 | Installed as                             | Purpose                          |
| -------------------- | ---------------------------------------- | -------------------------------- |
| `noctua-fan.py`      | `/usr/local/bin/noctua-fan.py`           | the controller                   |
| `noctua-fan.service` | `/etc/systemd/system/noctua-fan.service` | unit, reads the file below       |
| -                    | `/etc/default/noctua-fan`                | optional, sets `NOCTUA_FAN_ARGS` |
| `tmux-status.conf`   | appended to `~/.tmux.conf`               | status-bar lines                 |

---

## License

BSD 2-Clause. Copyright (c) 2026 Renaud Allard <renaud@allard.it>.
See [LICENSE](LICENSE).
