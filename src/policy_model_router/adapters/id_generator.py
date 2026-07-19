"""UUID-based identifier generator implementing the application's ``IdGenerator`` port."""

import uuid


class Uuid4IdGenerator:
    """Generates random (version 4) UUIDs as string identifiers."""

    def new_id(self) -> str:
        """Return a new random UUID as a string."""
        return str(uuid.uuid4())
