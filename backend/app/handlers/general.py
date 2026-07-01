"""General handler — lets JARVIS persist things it learns about you.

Phase 0: an explicit `remember_fact` tool. Phase 1 adds an automatic reflector
that extracts facts without the model having to call a tool.
"""

from app.handlers.base import Context, Registry
from app.memory import remember


def _remember_fact(args: dict, ctx: Context) -> str:
    content = args["content"].strip()
    category = args.get("category", "general")
    sensitive = bool(args.get("sensitive", False))
    remember(ctx.db, content=content, category=category, source=ctx.channel, sensitive=sensitive)
    return f"Noted and remembered: {content}"


def register(reg: Registry) -> None:
    reg.register(
        {
            "name": "remember_fact",
            "description": (
                "Save a durable fact about the user or their world for future "
                "recall (preferences, people, projects, context). Use when the user "
                "shares something worth remembering long-term."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The fact to remember, self-contained."},
                    "category": {"type": "string", "description": "e.g. people, projects, preferences, context"},
                    "sensitive": {"type": "boolean", "description": "True for sensitive personal info."},
                },
                "required": ["content"],
            },
        },
        _remember_fact,
    )
