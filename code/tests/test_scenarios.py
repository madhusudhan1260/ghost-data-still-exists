import pytest

from scenarios import SCENARIOS, run_scenario


@pytest.mark.parametrize("sc", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_synthetic_scenario(sc, tmp_path):
    _, problems = run_scenario(sc, tmp_path)
    assert problems == []
