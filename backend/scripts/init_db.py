# -*- coding: utf-8 -*-
"""建库脚本：python scripts/init_db.py
前提：MySQL 已启动且存在 finance_risk 库。
启动后 main.py 也会自动 create_all，本脚本用于单独初始化/演示数据灌入。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import init_db  # noqa: E402


def main() -> None:
    init_db()
    print("[init_db] 表结构已创建/同步")


if __name__ == "__main__":
    main()
