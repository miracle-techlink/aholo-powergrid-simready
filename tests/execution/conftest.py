import pytest


@pytest.fixture
def device_inventory():
    """Declared fixture identity only; tests never initialize a physical GPU."""
    return {
        "schema_version": 1,
        "cpu_slots": 4,
        "ram_gb": 4,
        "gpus": [
            {
                "index": 0,
                "uuid": "GPU-fixture-0",
                "vram_gb": 24,
                "capabilities": ["cuda", "rtx"],
                "rtx_isolated": True,
            }
        ],
    }
