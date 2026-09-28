from wiz101_auto.questlist import norm
from wiz101_auto.trainer import next_training, trainable

SCHEDULE = [1, 5, 8, 10, 16, 20, 22, 26, 33, 38, 42, 50]


def test_trip_due_once_per_schedule_level():
    assert next_training(11, 10, SCHEDULE) is None  # Cyclops already trained
    assert next_training(16, 10, SCHEDULE) == 16
    assert next_training(21, 10, SCHEDULE) == 20  # skipped a trip: catch up in one visit
    assert next_training(9, 0, SCHEDULE) == 8


def test_trainable_skips_known_and_too_high():
    options = [("Cyclops", 10), ("Ether Shield", 16), ("Blood Bat", 1)]
    known = {norm("Bloodbat")}
    assert trainable(options, 16, known) == ["Cyclops", "Ether Shield"]
    assert trainable(options, 11, known | {norm("Cyclops")}) == []
