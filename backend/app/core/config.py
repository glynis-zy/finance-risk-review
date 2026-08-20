# -*- coding: utf-8 -*-
"""全局配置：从环境变量读取，便于一键切换 LLM/OCR 厂商与数据库。"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # ---- 数据库 ----
    database_url: str = "mysql+pymysql://root:root@127.0.0.1:3306/finance_risk?charset=utf8mb4"

    # ---- JWT 认证 ----
    jwt_secret: str = "finance-risk-review-demo-secret-key-2026-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 120
    # 令牌撤销（jti 黑名单，进程内存存续；生产可换 Redis，见 architecture.md §15）
    jwt_revoked: bool = False

    # ---- 文件存储 ----
    file_storage_path: str = "data/uploads"

    # ---- LLM（DeepSeek，OpenAI 兼容接口；换厂商只改这里）----
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    # AutoDL/自建推理网关常为自签证书；演示/本地环境设为 true 跳过 SSL 校验
    llm_insecure_ssl: bool = False

    # ---- OCR（百度云：增值税发票识别 + 通用文字识别）----
    ocr_api_key: str = ""
    ocr_secret_key: str = ""
    ocr_base_url: str = "https://aip.baidubce.com"

    # ---- CORS ----
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # ---- 演示 ----
    preset_parse_dir: str = "demo/preset_parse"
    # 前端静态目录（FastAPI 托管，无构建步骤）
    frontend_dir: str = "../frontend"

    @property
    def storage_dir(self) -> Path:
        return Path(self.file_storage_path)


settings = Settings()
