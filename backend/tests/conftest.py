# -*- coding: utf-8 -*-
"""pytest 夹具：内存 SQLite + 建表，供规则引擎等纯逻辑测试使用。"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.base import Base  # noqa: E402
import app.models  # noqa: E402,F401  注册全部模型


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
