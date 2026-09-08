# noctua-fan

Temperature-driven speed control for a Noctua NF-A4x20 5V PWM fan on a
Raspberry Pi 4, using the SoC's hardware PWM.

## Why hardware PWM

The fan expects a 25 kHz control signal; 21-28 kHz is supported and outside
that range it behaves unpredictably. That rules out the two obvious shortcuts:

- `dtoverlay=pwm-gpio-fan` sets a 20 ms period, which is 50 Hz. It is meant
  for simple on/off fans, not a Noctua.
- `RPi.GPIO` software PWM tops out far below 25 kHz and jitters under load.

So the blue wire goes to a pin backed by the PWM peripheral, driven through
the kernel's PWM sysfs interface.

## Wiring

| Fan wire | Function | Pi 4 physical pin | BCM |
| -------- | -------- | ----------------- | --- |
| Yellow   | +5 V     | 4                 | -   |
| Black    | GND      | 6                 | -   |
| Blue     | PWM in   | 12                | 18  |
| Green    | Tach out | 18                | 24  |

On this fan yellow is +5 V power, not a signal. Current draw is 0.1 A max.

No level shifter is needed on the blue wire: Noctua fans read both 3.3 V and
5 V as logical high. The green wire is an open-collector output and needs a
pull-up; the script enables the pin's internal one. Do not pull it up to 5 V,
Pi 4 GPIOs are 3.3 V logic and are not 5 V tolerant.

The fan's 4-pin Molex plug does not mate with the header. Use female-to-male
jumpers, or cut into the supplied NA-EC1 extension rather than the fan lead.

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

## Install

```sh
sudo apt install -y python3-gpiozero python3-lgpio

sudo install -m 755 noctua-fan.py /usr/local/bin/noctua-fan.py
sudo install -m 644 noctua-fan.service /etc/systemd/system/noctua-fan.service

sudo systemctl daemon-reload
sudo systemctl enable noctua-fan.service
sudo reboot
```

No pip packages: the script writes to PWM sysfs directly, which also avoids
Bookworm's `externally-managed-environment` refusal.

## Fan curve

```python
OFF_AT_OR_BELOW_C = 30.0
CURVE = [(0, 0), (33, 20), (36, 40), (39, 60), (42, 80), (45, 100)]
HYSTERESIS_C = 2.0
```

| CPU temp | Duty | Approx. rpm |
| -------- | ---- | ----------- |
| <= 30 C  | 0    | stopped     |
| 30-32    | 0    | stopped     |
| 33-35    | 20   | ~1100       |
| 36-38    | 40   | ~2000       |
| 39-41    | 60   | ~3000       |
| 42-44    | 80   | ~4000       |
| >= 45 C  | 100  | 5000        |

The rpm column is interpolated from the datasheet's two anchor points,
1100 rpm at 20% and 5000 rpm at 100%, using Noctua's statement that the
duty-to-rpm relationship is roughly linear. Measure your own with the tach
output rather than trusting the middle rows.

Duty below 20% is undefined for these fans, so the curve steps from 0 to 20
and never lands in between. The 3 C dead band between "off at 30" and the
first step at 33 keeps the fan from chattering on and off at idle.

`HYSTERESIS_C` applies on cooldown only. Rising temperature steps the speed
up at once; falling temperature has to drop 2 C below a threshold before the
speed steps back down. `target_duty()` also has an explicit guard at
`OFF_AT_OR_BELOW_C`, so widening the hysteresis can never quietly keep the
fan spinning below the off point.

To run at a fixed speed with no thermal control, replace the curve with a
single entry such as `[(0, 40)]`.

Note that 45 C is far below where the Pi actually needs help: the Arm cores
are throttled progressively between 80 C and 85 C, and the Pi 4 has no soft
limit below that. This curve trades noise for headroom.

## Status output

Every interval the script writes one line to `/run/noctua-fan/status`, on
tmpfs, replaced atomically so a reader never sees a partial line:

```
2450rpm 41C
off 28C
```

`RuntimeDirectory=noctua-fan` in the unit creates `/run/noctua-fan` at start
with mode 0755, so an unprivileged reader needs no udev rule. The same
setting removes the directory when the service stops, so a missing file means
a stopped service. Add `RuntimeDirectoryPreserve=restart` if the last reading
should survive a restart.

Set `STATUS_FILE = None` to turn the feature off.

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

## Files

| File                | Installed as                                |
| ------------------- | ------------------------------------------- |
| `noctua-fan.py`     | `/usr/local/bin/noctua-fan.py`              |
| `noctua-fan.service`| `/etc/systemd/system/noctua-fan.service`    |
| `tmux-status.conf`  | appended to `~/.tmux.conf`                  |
