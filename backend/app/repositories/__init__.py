# -*- coding: utf-8 -*-
"""Repository 层：SQLAlchemy 数据访问聚合，Service 不再直接堆 SQL。

按业务聚合（不一张表一个 Repository）：
DocumentRepository / AttachmentRepository / WorkflowRepository / AnalysisRepository / UserRepository
"""
