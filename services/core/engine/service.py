"""Engine lane. Turns bus inputs into decision.action and device.command."""

from services.core.service import Service


class EngineService(Service):
    name = "engine"
    component = "engine"
