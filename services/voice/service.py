"""Voice lane, V-25. Publishes voice.wake, voice.transcript and voice.intent."""

from services.core.service import Service


class VoiceService(Service):
    name = "voice"
    component = "voice"
