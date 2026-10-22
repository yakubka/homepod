"""Every service core runs, in start order. Shutdown runs the list in reverse.

Producers start before the consumers that need them: the device link first,
the recorder before anything publishes in earnest, the engine after its
inputs, the API last so it never serves a half started room.
"""

SERVICES = [
    "services.device.service:DeviceService",
    "services.recorder.service:RecorderService",
    "services.vision.service:VisionService",
    "services.voice.service:VoiceService",
    "services.core.engine.service:EngineService",
    "services.api.service:ApiService",
]
