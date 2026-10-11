"""Wake-word detection package (VESTA).

Import the interface from wakeword.detector. Concrete engine backends
(openWakeWord, later possibly Porcupine) live in their own modules so the
rest of the pipeline never depends on a specific engine.
"""
