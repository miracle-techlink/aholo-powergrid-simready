from simready.agents.contracts import DecisionProvider
from simready.agents.providers.codex_cli import CodexCliProvider
from simready.agents.providers.openai_responses import OpenAIResponsesProvider
from simready.agents.providers.rule_based import RuleBasedProvider
from simready.core.types import JsonObject


def create_decision_provider(config: JsonObject) -> DecisionProvider:
    """Instantiate the explicitly selected decision-provider adapter."""
    options = dict(config)
    kind = options.pop("kind")
    types = {
        "codex": CodexCliProvider,
        "responses": OpenAIResponsesProvider,
        "rules": RuleBasedProvider,
    }
    if kind not in types:
        raise ValueError(f"Unsupported agent provider: {kind}")
    return types[kind](**options)
