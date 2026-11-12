import pytest

from simready.core.artifacts import loads_json


@pytest.mark.parametrize("payload", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'])
def test_strict_json_rejects_ambiguous_evidence(payload):
    with pytest.raises(ValueError):
        loads_json(payload)
