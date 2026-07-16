from agent.context.budget import ContextBudget
from agent.context.config import ContextConfig
from agent.context.tool_search import build_disclosure
from agent.tools.base import Tool


def tool(name: str, description: str = "specialized") -> Tool:
    return Tool(
        name=name,
        description=description,
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        run=lambda: f"ran {name}",
    )


def test_large_schema_surface_is_progressively_disclosed() -> None:
    config = ContextConfig(context_length=8_000, tool_search_enabled="on")
    budget = ContextBudget.for_model("tiny", config)
    disclosure = build_disclosure(
        (tool("read_file"), tool("banner_render", "render a banner"), tool("telegram_send", "send telegram")),
        config=config,
        budget=budget,
    )
    names = {item.name for item in disclosure.visible}
    assert disclosure.activated
    assert "read_file" in names
    assert {"tool_search", "tool_describe", "tool_call"} <= names
    assert set(disclosure.deferred) == {"banner_render", "telegram_send"}
    search = next(item for item in disclosure.visible if item.name == "tool_search")
    assert "banner_render" in search.invoke({"query": "banner"}).content
    call = next(item for item in disclosure.visible if item.name == "tool_call")
    assert call.invoke({"name": "telegram_send", "arguments": {}}).content == "ran telegram_send"


def test_tool_disclosure_can_be_disabled() -> None:
    config = ContextConfig(context_length=8_000, tool_search_enabled="off")
    tools = (tool("one"), tool("two"))
    disclosure = build_disclosure(tools, config=config, budget=ContextBudget.for_model("tiny", config))
    assert disclosure.visible == tools
    assert not disclosure.activated
