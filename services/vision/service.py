"""Vision lane, V-61. Publishes perception.occupancy once the camera loop is wired in."""

from services.core.service import Service


class VisionService(Service):
    name = "vision"
    component = "vision"
