import time

import pytest

from tests import scenario_runner as sr


def test_there_are_at_least_eight_scenarios():
    assert len(sr.names()) >= 8


@pytest.mark.parametrize("name", sr.names())
def test_scenario_matches_its_golden_file(name):
    actual = sr.run(sr.load(name))
    expected = sr.golden(name)
    assert actual == expected, f"{name} drifted\n{sr.diff(expected, actual)}"


def test_the_whole_set_replays_in_seconds():
    started = time.time()
    for name in sr.names():
        sr.run(sr.load(name))
    assert time.time() - started < 60


def test_a_broken_threshold_fails_exactly_one_scenario_with_a_readable_diff():
    broken = sr.load("arriving")
    broken["settings"] = {"light.full_at": 2}
    actual = sr.run(broken)
    expected = sr.golden("arriving")
    assert actual != expected
    assert "expected" in sr.diff(expected, actual)

    for name in ("empty_daylight", "professor_on_time"):
        assert sr.run(sr.load(name)) == sr.golden(name)


def test_the_announcement_scenario_announces_once():
    acts = sr.golden("professor_absent")
    assert [a for a in acts if a["kind"] == "announce"] == [
        {"at": 330.0, "rule": "announce.professor_absent", "kind": "announce",
         "target": "professor_absent", "value": "L4"}
    ]


def test_the_flapping_scenario_settles():
    assert len(sr.golden("boundary_flap")) <= 2


def test_daylight_switches_the_room_off_and_back_on():
    acts = sr.golden("blinds_open")
    assert [a["value"] for a in acts if a["at"] == 600.0] == [0, 0, 0]
    assert all(a["value"] > 0 for a in acts if a["at"] == 900.0)
