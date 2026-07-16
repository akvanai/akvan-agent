from agent.context.budget import ContextBudget, resolve_context_length
from agent.context.config import ContextConfig


def test_model_windows_and_scaled_budgets() -> None:
    assert resolve_context_length("unknown") == 128_000
    assert resolve_context_length("gpt-5.2") == 400_000
    assert resolve_context_length("deepseek-chat") == 1_000_000
    budget = ContextBudget.for_model(
        "tiny", ContextConfig(context_length=20_000, max_output_tokens=4_000)
    )
    assert budget.effective_input_tokens == 16_000
    assert budget.max_result_chars == 12_000
    assert budget.max_turn_chars == 24_000


def test_request_estimate_counts_messages_schemas_and_images() -> None:
    budget = ContextBudget.for_model(
        "tiny", ContextConfig(context_length=20_000, max_output_tokens=2_000)
    )
    messages = [
        {"role": "user", "content": "x" * 400},
        {"role": "user", "content": [{"type": "image_url", "url": "x"}]},
    ]
    schema = [{"type": "function", "function": {"name": "large", "description": "y" * 400}}]
    usage = budget.estimate(messages, schema)
    assert usage.messages >= 1_700
    assert usage.tool_schemas >= 100
    assert usage.estimated_total == usage.messages + usage.tool_schemas
    assert usage.reserved_output == 2_000
    assert "xxxx" not in str(usage.as_dict())
