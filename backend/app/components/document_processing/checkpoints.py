"""LangGraph's official PostgreSQL saver, isolated from domain tables."""
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres import PostgresSaver

@contextmanager
def checkpoint_saver(engine):
    url=engine.url
    # A dedicated connection avoids changing a pooled domain connection's schema.
    with psycopg.connect(host=url.host,port=url.port or 5432,dbname=url.database,user=url.username,
                         password=url.password,autocommit=True,prepare_threshold=0,row_factory=dict_row,
                         options='-c search_path=processing_graph') as connection:
        yield PostgresSaver(connection)
