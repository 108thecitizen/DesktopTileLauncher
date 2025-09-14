from tile_launcher import FitPolicy, FitTrigger, should_fit
import pytest


@pytest.mark.unit
@pytest.mark.parametrize(
    "policy,did,trigger,expected",
    [
        ("always", False, "move", True),
        ("on_startup", False, "show", True),
        ("on_startup", True, "resize", False),
        ("off", False, "move", False),
        ("off", True, "manual", True),
    ],
)
def test_should_fit(
    policy: FitPolicy, did: bool, trigger: FitTrigger, expected: bool
) -> None:
    assert should_fit(policy, did, trigger) is expected  # nosec B101
