"""Application-level errors. The presentation layer maps them to HTTP status codes."""

from __future__ import annotations


class AppError(Exception):
    """Base class for expected, user-facing errors."""


class NotFoundError(AppError):
    def __init__(self, entity: str, identifier: object) -> None:
        super().__init__(f"{entity} {identifier!r} not found")
        self.entity = entity


class ConflictError(AppError):
    """The request is valid but clashes with existing data (duplicates, history rewrites)."""


class InvalidRequestError(AppError):
    """The request is well-formed JSON but semantically wrong (e.g. unusable cursor)."""
