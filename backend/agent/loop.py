"""The agent loop for ONE user message: ask the model, run the tools it asks for, give it the results, repeat until it answers in plain text.

Safety rules enforced here (not left to the model):
- at most `max_calls` tool calls per user message;
- an unknown tool, unparsable JSON or invalid arguments are refused with a short message and nothing runs;
- a tool error goes back to the model as a short message;
- the only tools that exist are in tools.TOOLS, and none of them can confirm a document."""

import json
import logging
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from backend.agent.llm import AgentLLM, ToolCall
from backend.agent.tools import TOOLS, ToolContext, ToolError, tool_specs
from backend.llm.base import LLMBadOutput

log = logging.getLogger(__name__)

MAX_RESULT_CHARS = 1800
LIMIT_REPLY = ("I reached my limit of {n} steps for one message. Tell me to continue and I will pick up where I stopped.")


@dataclass
class TurnResult:
    reply: str
    trace: list[dict[str, Any]]
    limit_reached: bool = False


def _short(text: str, size: int = 200) -> str:
    return text if len(text) <= size else text[:size - 1] + "…"


def _execute(call: ToolCall, ctx: ToolContext, step: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Runs one tool call. Returns (what the model sees, the trace entry). Never raises."""
    entry: dict[str, Any] = {"step": step, "tool": call.name, "args": {}, "ok": False, "summary": ""}
    tool = TOOLS.get(call.name)
    if tool is None:
        entry["summary"] = f"unknown tool '{_short(call.name, 40)}' (refused)"
        return {"error": f"Unknown tool '{_short(call.name, 40)}'. Available: {', '.join(TOOLS)}."}, entry
    try:
        raw = json.loads(call.arguments or "{}")
        if not isinstance(raw, dict):
            raise ValueError("arguments must be a JSON object")
    except ValueError:
        entry["args"] = {"raw": _short(call.arguments or "", 120)}
        entry["summary"] = "arguments were not valid JSON (refused)"
        return {"error": "Arguments must be a valid JSON object."}, entry
    entry["args"] = raw
    try:
        args = tool.args_model.model_validate(raw)
    except ValidationError as error:
        problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}" for e in error.errors()[:4])
        entry["summary"] = _short(f"invalid arguments (refused): {problems}")
        return {"error": _short(f"Invalid arguments: {problems}", 300)}, entry
    try:
        result = tool.run(args, ctx)
    except ToolError as error:
        entry["summary"] = _short(f"error: {error}")
        return {"error": _short(str(error), 300)}, entry
    except Exception:                                              # a bug in a tool must not crash the chat
        log.exception("Tool %s failed", call.name)
        entry["summary"] = "the tool failed unexpectedly"
        return {"error": "The tool failed unexpectedly."}, entry
    entry.update(ok=True, summary=result.summary)
    if result.source_message is not None:
        entry["source_message"] = result.source_message
    return result.data, entry


def run_turn(llm: AgentLLM, ctx: ToolContext, messages: list[dict[str, Any]], max_calls: int) -> TurnResult:
    """`messages` = system prompt + history, ending with the user's new message. The list is extended in place with the turn's tool exchange."""
    specs, trace, made, repairs = tool_specs(), [], 0, 1
    while True:
        try:
            reply = llm.chat(messages, specs)
        except LLMBadOutput:                                       # a malformed tool call: tell the model once, then give up
            if repairs == 0:
                raise
            repairs -= 1
            messages.append({"role": "user", "content": "[app note, not from the user] Your last tool call was malformed. Call the tool again with valid JSON arguments."})
            continue
        if not reply.tool_calls:
            return TurnResult((reply.content or "").strip() or "I have nothing to add.", trace)
        messages.append({"role": "assistant", "content": reply.content,
                         "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in reply.tool_calls]})
        for call in reply.tool_calls:
            if made >= max_calls:
                return TurnResult(LIMIT_REPLY.format(n=max_calls), trace, limit_reached=True)
            made += 1
            data, entry = _execute(call, ctx, made)
            trace.append(entry)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": _short(json.dumps(data, separators=(",", ":"), ensure_ascii=False), MAX_RESULT_CHARS)})
