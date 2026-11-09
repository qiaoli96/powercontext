from tickets import list_tickets


TICKETS = [
    {"id": 3, "priority": "high"},
    {"id": 2, "priority": "low"},
    {"id": 1, "priority": "high"},
]


def test_filters_by_priority():
    assert [ticket["id"] for ticket in list_tickets(TICKETS, priority="high")] == [1, 3]


def test_no_matching_priority():
    assert list_tickets(TICKETS, priority="medium") == []


def test_default_preserves_all_tickets_and_order():
    assert [ticket["id"] for ticket in list_tickets(TICKETS)] == [1, 2, 3]
    assert [ticket["id"] for ticket in TICKETS] == [3, 2, 1]
