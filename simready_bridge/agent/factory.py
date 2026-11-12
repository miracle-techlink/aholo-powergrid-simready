from .codex import CodexProvider
from .responses import ResponsesProvider
from .rules import RuleProvider


def create_provider(config):
    options = dict(config)
    kind = options.pop("kind")
    types = {
        "codex": CodexProvider,
        "responses": ResponsesProvider,
        "rules": RuleProvider,
    }
    if kind not in types:
        raise ValueError(f"Unsupported agent provider: {kind}")
    return types[kind](**options)
