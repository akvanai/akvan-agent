"""Context budgeting, bounded tool results, compaction, and tool disclosure."""

from agent.context.budget import ContextBudget, RequestBreakdown
from agent.context.compression import CompactionResult, ContextCompressor
from agent.context.config import ContextConfig, load_context_config


def __getattr__(name: str):
    """Keep tool-dependent context helpers lazy to avoid package import cycles."""

    if name == "ToolResultStore":
        from agent.context.result_storage import ToolResultStore

        return ToolResultStore
    raise AttributeError(name)

__all__ = [
    "CompactionResult",
    "ContextBudget",
    "ContextCompressor",
    "ContextConfig",
    "RequestBreakdown",
    "ToolResultStore",
    "load_context_config",
]
