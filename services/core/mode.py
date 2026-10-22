"""The one place that decides which implementation of a source is built.

    sensors = mode.pick(
        replay=lambda: RecordedSensors(),
        sim=lambda: SimulatedSensors(),
        real=lambda: BoardSensors(),  # imports the device library inside
    )

Only the factory for the current VESTA_MODE is called, so a device library is
imported only when its real implementation is constructed. No other module
checks for hardware.
"""

from services.core import config


def pick(**factories):
    unknown = set(factories) - set(config.MODES)
    if unknown:
        raise ValueError(f"unknown modes {sorted(unknown)}, expected {config.MODES}")
    if config.MODE not in factories:
        raise LookupError(f"no implementation for VESTA_MODE={config.MODE}, have {sorted(factories)}")
    return factories[config.MODE]()
