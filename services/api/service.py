"""API lane, V-42. Serves contracts/openapi.yaml and publishes control.request."""

from services.core.service import Service


class ApiService(Service):
    name = "api"
    component = "api"
