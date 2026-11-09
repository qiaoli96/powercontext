def list_tickets(tickets, *, priority=None):
    """Return tickets ordered by ID."""
    return sorted(tickets, key=lambda ticket: ticket["id"])
