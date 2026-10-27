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
    tool_results: ClassVar[list[list[object | Exception]]] = []

    def __init__(self, **kwargs: object) -> None:
        self.__class__.captured_kwargs = kwargs

    @classmethod
    def reset(
        cls,
        *,
        responses: Sequence[str] = (),
        errors: Sequence[Exception] = (),
        tool_calls: Sequence[Sequence[str]] = (),
        tool_results: Sequence[Sequence[object | Exception]] = (),
    ) -> None:
        cls.captured_kwargs = None
        cls.responses = list(responses)
        cls.errors = list(errors)
        cls.messages = []
        cls.tool_calls = [list(names) for names in tool_calls]
        cls.tool_results = [list(results) for results in tool_results]

    def run(
        self,
        message: str,
        /,
        *,
        stream: bool = False,
    ) -> Awaitable[MockAgentResponse] | ResponseStream[AgentResponseUpdate, AgentResponse]:
        if stream:

            async def updates():
                response, tool_names, tool_results = self._next_response(message)
                call_ids = [f"mock-{index}" for index, _name in enumerate(tool_names)]
                for call_id, name in zip(call_ids, tool_names, strict=True):
                    yield AgentResponseUpdate(
                        role="assistant",
                        contents=[Content.from_function_call(call_id=call_id, name=name)],
                    )
                for index, result in enumerate(tool_results):
                    if index >= len(call_ids):
                        break
                    call_id = call_ids[index]
                    if isinstance(result, Exception):
                        content = Content.from_function_result(call_id=call_id, exception=str(result))
                    else:
                        content = Content.from_function_result(call_id=call_id, result=result)
                    yield AgentResponseUpdate(role="tool", contents=[content])
                yield AgentResponseUpdate(role="assistant", contents=[Content.from_text(response.text)])

            return ResponseStream(updates(), finalizer=AgentResponse.from_updates)

        async def response() -> MockAgentResponse:
            result, _tool_names, _tool_results = self._next_response(message)
            return result

        return response()

    def _next_response(self, message: str) -> tuple[MockAgentResponse, list[str], list[object | Exception]]:
        self.__class__.messages.append(message)
        if self.__class__.errors:
            raise self.__class__.errors.pop(0)
        response = MockAgentResponse(text=self.__class__.responses.pop(0) if self.__class__.responses else "{}")
        tool_names = self.__class__.tool_calls.pop(0) if self.__class__.tool_calls else []
        tool_results = self.__class__.tool_results.pop(0) if self.__class__.tool_results else []
        return response, tool_names, tool_results


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
