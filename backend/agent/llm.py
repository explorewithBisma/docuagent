"""The model behind the agent: a Groq TEXT model with tool calling (AGENT_MODEL), behind a small interface so tests can script it.
429, 503 (over capacity) and timeouts are retried with exponential backoff, like the extraction providers."""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from backend.config import Settings
from backend.llm.base import RETRYABLE, LLMNotConfigured
from backend.llm.groq_client import translate_groq_error


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str                      # the model's raw JSON text; validated by the tool registry, never trusted


@dataclass
class AgentReply:
    content: Optional[str] = None
    tool_calls: list[ToolCall] = field(default_factory=list)


class AgentLLM(ABC):
    @abstractmethod
    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AgentReply:
        """One model call. Raises an LLMError on failure."""


class GroqAgentLLM(AgentLLM):
    def __init__(self, api_key: str, model: str, reasoning_effort: str = "low", max_tokens: int = 700, timeout_seconds: float = 60.0,
                 max_tries: int = 3, backoff_seconds: float = 1.0, max_wait_seconds: float = 30.0,
                 sdk_client: Optional[Any] = None, sleep: Callable[[float], None] = time.sleep):
        self.api_key, self.model, self.reasoning_effort, self.max_tokens, self.timeout_seconds = api_key, model, reasoning_effort, max_tokens, timeout_seconds
        self.max_tries, self.backoff_seconds, self.max_wait_seconds = max_tries, backoff_seconds, max_wait_seconds
        self._client, self.sleep = sdk_client, sleep
        self.last_usage: Optional[dict[str, int]] = None              # tokens of the last call (for measuring)

    def _sdk(self):
        if self._client is None:
            import groq
            self._client = groq.Groq(api_key=self.api_key, timeout=self.timeout_seconds, max_retries=0)
        return self._client

    def _once(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AgentReply:
        import groq
        try:
            response = self._sdk().chat.completions.create(model=self.model, messages=messages, tools=tools, tool_choice="auto", temperature=0,
                                                           max_tokens=self.max_tokens, reasoning_effort=self.reasoning_effort)
        except groq.APIError as error:
            raise translate_groq_error(error) from error
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.last_usage = {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens}
        message = response.choices[0].message
        calls = [ToolCall(c.id, c.function.name, c.function.arguments or "{}") for c in (getattr(message, "tool_calls", None) or [])]
        return AgentReply(content=getattr(message, "content", None), tool_calls=calls)

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AgentReply:
        if not self.api_key:
            raise LLMNotConfigured("GROQ_API_KEY is not set, so the agent cannot run.")
        for attempt in range(1, self.max_tries + 1):
            try:
                return self._once(messages, tools)
            except RETRYABLE as error:
                if attempt == self.max_tries:
                    raise
                hint = min(getattr(error, "retry_after", None) or 0.0, self.max_wait_seconds)
                self.sleep(max(self.backoff_seconds * 2 ** (attempt - 1), hint))
        raise AssertionError("unreachable")                          # pragma: no cover


def build_agent_llm(settings: Settings) -> AgentLLM:
    return GroqAgentLLM(settings.groq_api_key, settings.agent_model, settings.agent_reasoning_effort, settings.agent_max_tokens,
                        settings.llm_timeout_seconds, settings.llm_max_tries, settings.llm_backoff_seconds)
