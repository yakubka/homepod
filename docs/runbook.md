# Demo runbook

When something breaks during the demo, do the first matching section, in order, and nothing else.
Every command runs on the Pi from the repository directory, `ssh pi@room-pi.local` then `cd ~/homepod`.

## 0. Look first, 10 seconds

```bash
curl -s http://127.0.0.1:8080/api/v1/health | python3 -m json.tool
```

`status` is `ok`, `degraded` or `down`. `degradations` names what is broken, `components` shows each
part with `restarts`. Match the first broken thing below. The room keeps working in every degraded
mode, so carry on talking while you fix it.

## 1. `device_link_lost`, lights do not react, sensor values marked stale

1. Check the ESP32 USB cable at both ends and that the ESP32 LED is on.
2. Wait 5 seconds, the link retries every second by itself. This prints `"status": "ok"` when it is back:

   ```bash
   curl -s http://127.0.0.1:8080/api/v1/health | grep -o '"name": "esp32", "status": "[a-z]*"'
   ```
3. Still down: unplug the ESP32 USB cable, wait 3 seconds, plug it back in, wait 5 seconds.
4. Still down: restart core, section 7.

Meanwhile voice still answers questions from the last reading, say so to the audience.

## 2. `vision_unavailable`, people count missing

1. Occupancy now comes from the presence sensor, counts show as unknown, that is expected.
2. Check the camera ribbon or USB cable and that nothing covers the lens.
3. It recovers by itself on the next good frame. If not within 10 seconds, restart core, section 7.

## 3. `stt_unavailable`, the assistant says it can't understand speech

1. Use the app instead: the text box sends the same request.
2. Check the microphone is plugged in, then say the wake word again. The flag clears on the next
   utterance that works.
3. Still failing after two tries: restart core, section 7.

## 4. `internet_unavailable`, general questions fail

Nothing to do for the demo, every room command still works. Reconnect the Wi-Fi when there is time,
the flag clears within 30 seconds.

## 5. `over_budget`, counts missing and general questions refused

1. Something on the Pi is using too much memory or CPU. Check with `top -o %MEM`, close anything that
   is not `python3 -m services.core`.
2. It clears by itself 30 seconds after the room is back within budget.

## 6. A component shows `down` or `service_failed`

The supervisor already restarted it `restarts` times and gave up. Restart core, section 7.

## 7. Restart core, 15 seconds

In the terminal running core press Ctrl C once and wait for `core stopped`. Then:

```bash
VESTA_MODE=real python3 -m services.core 2>&1 | tee -a vesta.log
```

Start core with this same command before the demo too, so the log is kept. Wait for `core ready` and one `service healthy` line per service, then check section 0 again.
If Ctrl C does not stop it within 10 seconds it kills itself, start it again with the same command.

## 8. Nothing answers at all

1. `ping room-pi.local` from the laptop. No answer: the Pi is off or off the network, check its
   power LED and the network cable, then power cycle it and wait 60 seconds.
2. The Pi answers but `/health` does not: core is not running, start it, section 7.

## After the demo

Save the log, it tells the whole story of each interaction by `trace_id`:

```bash
cp ~/homepod/vesta.log ~/vesta-$(date +%H%M).log
```
