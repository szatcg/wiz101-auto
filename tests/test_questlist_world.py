from wiz101_auto.questlist import ListedQuest, world_of_quest


def test_world_of_quest():
    lists = {"Karamelle": [ListedQuest(66, "Hit The Gym", "Gobblerton", [])],
             "Celestia": [ListedQuest(1, "Fish Tricks", "Science Center", [])]}
    assert world_of_quest("Hit the Gym", lists) == "Karamelle"
    assert world_of_quest("Nothing Like It", lists) == ""
