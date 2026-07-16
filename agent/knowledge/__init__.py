"""Global OKF-backed knowledge interfaces."""

from agent.knowledge.config import KnowledgeConfig, load_knowledge_config
from agent.knowledge.store import KnowledgeStore

__all__ = ["KnowledgeConfig", "KnowledgeStore", "load_knowledge_config"]
