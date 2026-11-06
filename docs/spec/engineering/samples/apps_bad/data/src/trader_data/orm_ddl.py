"""违规样例四：由 ORM 推 DDL，与 storage.schema.json 争权威。

违反 SPEC §3.1 与 #25：DDL 权威唯一 = storage.schema.json，
任何包都不得由 ORM 推表结构，否则 SQLite 与 PostgreSQL 两阶段的结构会各自漂移。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base

Base = declarative_base()
engine = create_engine("sqlite:///app.db")
Base.metadata.create_all(engine)
