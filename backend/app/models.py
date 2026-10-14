"""One schema registry; each component still owns its own tables."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
