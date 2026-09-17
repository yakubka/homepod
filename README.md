# HomePod

A modular HomePod-style smart speaker built on Raspberry Pi 4B. Voice control, music playback,
environmental sensing, computer-vision presence detection, and occupancy-aware room lighting.

University project — software module.

## Architecture

```
                    ┌─────────────┐
   microphone ─────▶│  AI module  │ speech → intent
                    └──────┬──────┘
                           ▼
 camera ──▶ detector ──▶ ┌──────────┐ ◀── REST API ── mobile app
 BH1750  ──────────────▶ │ commands │
                         └────┬─────┘
                              ▼
            ┌─────────┬───────┴───┬──────────┬─────────┐
          audio    LED ring    display    lights    sensors
```

## Modules

| File | Responsibility |
|---|---|
| `main.py` | Entry point, boots every subsystem, graceful shutdown |
| `config.py` | JSON device settings, persisted across reboots |
| `hardware.py` | GPIO — status LEDs, DHT climate sensor, touch button |
| `camera.py` | Frame capture from the CSI camera, threaded buffer |
| `detector.py` | MobileNet SSD person detection, three-zone mapping |
| `presence.py` | Vision → lighting control loop |
| `lights.py` | Per-zone lamp switching, follow-me logic, manual override |
| `light_sensor.py` | BH1750 ambient light over I²C, lux thresholds |
| `display.py` | SSD1306 OLED over I²C |
| `led_ring.py` | WS2812B addressable ring, six animations |
| `audio.py` | Playlist and playback |
| `servo.py` | PWM servo, volume indicator |
| `commands.py` | Intent routing — 18 voice commands |
| `server.py` | REST API for the mobile app |

## Features

**Voice commands** — music control, volume, time, room climate, timers, lighting, presence queries.

**Follow-me lighting** — the camera detects a person and assigns them to one of three spatial
zones. The lamp in their zone turns on; zones they have left switch off after a timeout. The
BH1750 gates the whole behaviour, so lamps only activate when the room is genuinely dark.

**Simulation mode** — every hardware module carries a `SIM` flag, so the full pipeline runs and
is testable on a laptop without a Raspberry Pi attached.

## Hardware

| Component | Part | Interface |
|---|---|---|
| Controller | Raspberry Pi 4B 4GB | — |
| Camera | Raspberry Pi 5MP camera | CSI |
| Presence radar | LD2410B 24GHz mmWave | UART |
| Light sensor | GY-302 / BH1750 | I²C `0x23` |
| Climate sensor | DHT11 3-pin | 1-Wire GPIO 4 |
| Microphone | MAX9814 analog | needs ADC |
| Audio amp | MAX98357A | I²S |
| DAC | PCM5102 | I²S |
| Speaker | 8Ω 0.5W 40mm | — |
| LED ring | WS2812B 24-LED | PWM GPIO 12 |
| Touch button | TTP223 | GPIO 18 |
| Cooling | Evercool 40mm PWM fan | PWM |

## API

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/status` | Full device state |
| GET | `/commands` | Available intents |
| GET | `/sensor` | Temperature, humidity, ambient light |
| GET | `/ambient` | Lux reading and level |
| GET | `/presence` | Person detected, zone, occupancy stats |
| GET | `/lights` | Per-zone lamp state |
| GET | `/audio` | Player state |
| POST | `/command` | Execute an intent |
| POST | `/volume` | Set volume |
| POST | `/config` | Update a setting |
| POST | `/lights/zone` | Control one zone |
| POST | `/lights/follow` | Toggle follow mode |

## Running

```bash
pip install -r requirements.txt
python src/main.py
```

Set `SIM = False` in each hardware module when running on the Raspberry Pi.

The detector needs the MobileNet SSD weights in `models/` — see `models/README.md`.
