"""Mock chat client for agent unit tests."""

from __future__ import annotations

from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar, cast

from agent_framework import AgentResponse, AgentResponseUpdate, Content, ResponseStream


@dataclass(frozen=True)
class MockAgentResponse:
    """Minimal response shape consumed by specialist adapters."""

    text: str


class MockChatClient:
    """ClassVar-capturing fake for GitHubCopilotAgent."""

    captured_kwargs: ClassVar[dict[str, object] | None] = None
    responses: ClassVar[list[str]] = []
    errors: ClassVar[list[Exception]] = []
    messages: ClassVar[list[str]] = []
    tool_calls: ClassVar[list[list[str]]] = []

    def __init__(self, **kwargs: object) -> None:
        self.__class__.captured_kwargs = kwargs

    @classmethod
    def reset(
        cls,
        *,
        responses: Sequence[str] = (),
        errors: Sequence[Exception] = (),
        tool_calls: Sequence[Sequence[str]] = (),
    ) -> None:
        cls.captured_kwargs = None
        cls.responses = list(responses)
        cls.errors = list(errors)
        cls.messages = []
        cls.tool_calls = [list(names) for names in tool_calls]

    def run(
        self,
        message: str,
        /,
        *,
        stream: bool = False,
    ) -> Awaitable[MockAgentResponse] | ResponseStream[AgentResponseUpdate, AgentResponse]:
        if stream:

            async def updates():
                response, tool_names = self._next_response(message)
                for index, name in enumerate(tool_names):
                    yield AgentResponseUpdate(
                        role="assistant",
                        contents=[Content.from_function_call(call_id=f"mock-{index}", name=name)],
                    )
                yield AgentResponseUpdate(role="assistant", contents=[Content.from_text(response.text)])

            return ResponseStream(updates(), finalizer=AgentResponse.from_updates)

        async def response() -> MockAgentResponse:
            result, _tool_names = self._next_response(message)
            return result

        return response()

    def _next_response(self, message: str) -> tuple[MockAgentResponse, list[str]]:
        self.__class__.messages.append(message)
        if self.__class__.errors:
            raise self.__class__.errors.pop(0)
        response = MockAgentResponse(text=self.__class__.responses.pop(0) if self.__class__.responses else "{}")
        tool_names = self.__class__.tool_calls.pop(0) if self.__class__.tool_calls else []
        return response, tool_names


def captured_options() -> Mapping[str, object]:
    """Return captured default options as a mapping."""

    kwargs = MockChatClient.captured_kwargs
    if kwargs is None:
        raise AssertionError("GitHubCopilotAgent was not constructed")
    options = kwargs.get("default_options")
    if not isinstance(options, Mapping):
        options_dict = getattr(options, "__dict__", None)
        if not isinstance(options_dict, Mapping):
            raise AssertionError("default_options was not captured")
        return cast("Mapping[str, object]", options_dict)
    return cast("Mapping[str, object]", options)
