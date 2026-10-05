import pytest
from pydantic import ValidationError

from pod_artwork_engine.contracts import BoundingBox, DesignSpec, QualityMode


def test_design_spec_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        DesignSpec(unexpected=True)


def test_bounding_box_is_normalized() -> None:
    box = BoundingBox(x=0.1, y=0.2, width=0.5, height=0.4)
    assert box.width == 0.5


def test_quality_mode_values_are_stable() -> None:
    assert QualityMode.PRINT_READY.value == "print_ready"
