"""Test doubles for the Anthropic client so the orchestrator/reflector run offline."""

from types import SimpleNamespace


def text_block(t: str):
    return SimpleNamespace(type="text", text=t)


def tool_block(name: str, inp: dict, id: str = "tool_1"):
    return SimpleNamespace(type="tool_use", name=name, input=inp, id=id)


def response(blocks, stop_reason="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason)


class ScriptedLLM:
    """Returns queued responses in order; repeats the last once exhausted.

    Signature matches app.llm.create_message(system, messages, tools=None, model=None).
    """

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, system, messages, tools=None, model=None):
        self.calls.append(SimpleNamespace(system=system, messages=messages, tools=tools, model=model))
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


def install_llm(monkeypatch, fn):
    """Patch create_message everywhere it was imported by name."""
    import app.orchestrator as orch
    import app.reflector as refl
    import app.agents as agents
    monkeypatch.setattr(orch, "create_message", fn)
    monkeypatch.setattr(refl, "create_message", fn)
    monkeypatch.setattr(agents, "create_message", fn)
    return fn


# Convenience builders -------------------------------------------------------
def say(text: str):
    """A single plain-text answer, no tools."""
    return ScriptedLLM(response([text_block(text)], stop_reason="end_turn"))


def use_tool_then(text: str, tool_name: str, tool_input: dict):
    """First turn calls a tool; second turn returns text."""
    return ScriptedLLM(
        response([tool_block(tool_name, tool_input)], stop_reason="tool_use"),
        response([text_block(text)], stop_reason="end_turn"),
    )
