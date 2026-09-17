import logging
import threading
import time

log = logging.getLogger("lux")

SIM = True

ADDR = 0x23
BUS = 1
POWER_ON = 0x01
RESET = 0x07
CONT_HIGH_RES = 0x10
CONV_MS = 0.18

DARK = 50
DIM = 200


class LightSensor:
    def __init__(self, addr=ADDR, bus=BUS):
        self.addr = addr
        self.lux = 0.0
        self.bus = None
        self.running = False

        if not SIM:
            from smbus2 import SMBus
            self.bus = SMBus(bus)
            self.bus.write_byte(self.addr, POWER_ON)
            self.bus.write_byte(self.addr, RESET)
            self.bus.write_byte(self.addr, CONT_HIGH_RES)
            time.sleep(CONV_MS)
            log.info(f"bh1750 ready at 0x{self.addr:02x}")

    def read(self):
        try:
            if SIM:
                import random
                self.lux = round(random.uniform(0, 600), 1)
            else:
                raw = self.bus.read_i2c_block_data(self.addr, CONT_HIGH_RES, 2)
                self.lux = round(((raw[0] << 8) | raw[1]) / 1.2, 1)
        except Exception as e:
            log.error(f"lux read failed, {e}")
        return self.lux

    def poll(self, interval=5):
        self.running = True

        def run():
            while self.running:
                self.read()
                time.sleep(interval)

        threading.Thread(target=run, daemon=True).start()
        log.info(f"lux polling every {interval}s")

    def level(self):
        if self.lux < DARK:
            return "dark"
        if self.lux < DIM:
            return "dim"
        return "bright"

    def is_dark(self, threshold=DARK):
        return self.lux < threshold

    def stop(self):
        self.running = False
        if self.bus:
            self.bus.close()
        log.info("lux sensor stopped")

    def status(self):
        return {"lux": self.lux, "level": self.level(), "dark": self.is_dark(), "sim": SIM}
