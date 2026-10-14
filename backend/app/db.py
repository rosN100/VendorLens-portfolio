"""Shared database connection; table ownership stays with storage/components."""

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from app.settings import database_url


@lru_cache
def get_engine() -> Engine:
    return create_engine(database_url(), pool_pre_ping=True, hide_parameters=True)
