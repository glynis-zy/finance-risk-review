# -*- coding: utf-8 -*-
"""数据库引擎与会话工厂。

MySQL 8（Docker）：
  docker run --name frr-mysql -e MYSQL_ROOT_PASSWORD=root \
    -e MYSQL_DATABASE=finance_risk -p 3306:3306 -d mysql:8
SQLAlchemy 抽象层保证切库只改 database_url。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """启动时建表（demo 用；生产迁移见 scripts/alembic 说明）。"""
    from app.db.base import Base
    import app.models  # noqa: F401  确保所有模型注册到 Base.metadata
    Base.metadata.create_all(bind=engine)
