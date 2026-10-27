"""GitHub Copilot SDK factory for the ZavaShop supplier specialist agent."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, TypeVar, cast

import structlog
from agent_framework import AgentResponse, Content
from agent_framework.github import GitHubCopilotAgent, GitHubCopilotOptions
from copilot.generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject
from copilot.session import PermissionRequestResult
from copilot.session_events import PermissionRequest, PermissionRequestMcp
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.shared.copilot import build_copilot_client
from src.shared.settings import Settings
from src.shared.supplier_contracts import (
    SupplierAgentOutput,
    SupplierCatalogView,
)

from .prompts import SYSTEM_PROMPT
from .tools import TOOLS

log = structlog.get_logger(__name__).bind(agent_name="supplier")


class _SupplierToolDraft(BaseModel):
    """Validated draft_po result returned by the supplier MCP tool."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", allow_inf_nan=False)

    sku: str = Field(min_length=1)
    store_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    unit_cost: float = Field(gt=0)
    estimated_delivery_days: int = Field(ge=0)
    status: Literal["draft"]
    rationale: str = Field(min_length=1)


def _approve_supplier_operations(request: PermissionRequest, _context: dict[str, str]) -> PermissionRequestResult:
    if isinstance(request, PermissionRequestMcp):
        if request.server_name in {"supplier", "supplier-mcp"} and request.tool_name in {
            "list_suppliers",
            "supplier-list_suppliers",
            "draft_po",
            "supplier-draft_po",
        }:
            return PermissionDecisionApproveOnce()
    return PermissionDecisionReject(feedback="Only supplier lookup and purchase-order drafting are permitted.")


@dataclass
class _AgentRunResult:
    output_text: str
    tool_calls: list[str]


def _observed_tool_calls(response: AgentResponse) -> list[str]:
    return [
        content.name
        for message in response.messages or ()
        for content in message.contents
        if content.type == "function_call" and content.name is not None
    ]


def _supplier_tool_name(name: str) -> Literal["list_suppliers", "draft_po"] | None:
    if name.endswith("list_suppliers"):
        return "list_suppliers"
    if name.endswith("draft_po"):
        return "draft_po"
    return None


def _mcp_result_payload(content: Content, *, tool_name: str) -> dict[str, object]:
    content_items = content.items
    text_items = (
        [
            (index, item.type, item.text)
            for index, item in enumerate(content_items)
            if item.type == "text" and isinstance(item.text, str) and item.text.strip()
        ]
        if content_items is not None
        else []
    )
    item_count = len(content_items) if content_items is not None else 0
    if not text_items:
        result_text = content.result
        if isinstance(result_text, str) and result_text.strip():
            text_items = [(0, "result", result_text)]
            item_count = 1
    if not text_items:
        raise RuntimeError(f"Supplier MCP tool {tool_name} returned no text data")

    payloads: list[dict[str, object]] = []
    decoding_failures: list[dict[str, int | str]] = []
    for item_index, content_type, text in text_items:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            decoding_failures.append(
                {
                    "item_index": item_index,
                    "content_type": content_type,
                    "error_type": type(exc).__name__,
                    "error_position": exc.pos,
                    "error_line": exc.lineno,
                    "error_column": exc.colno,
                    "item_length": len(text),
                }
            )
            continue
        if not isinstance(payload, dict):
            log.error(
                "supplier.mcp_result.invalid_shape",
                tool=tool_name,
                content_type=content_type,
                item_count=item_count,
                item_index=item_index,
                expected="json_object",
            )
            raise RuntimeError(f"Supplier MCP tool {tool_name} returned an invalid JSON shape")
        payloads.append(cast(dict[str, object], payload))

    if not payloads:
        if decoding_failures:
            log.error(
                "supplier.mcp_result.invalid_json",
                tool=tool_name,
                item_count=item_count,
                **decoding_failures[0],
            )
        raise RuntimeError(f"Supplier MCP tool {tool_name} returned invalid JSON")

    for failure in decoding_failures:
        log.warning(
            "supplier.mcp_result.non_json_content",
            tool=tool_name,
            item_count=item_count,
            **failure,
        )
    if any(payload != payloads[0] for payload in payloads[1:]):
        log.error("supplier.mcp_result.conflicting_representations", tool=tool_name, representations=len(payloads))
        raise RuntimeError(f"Supplier MCP tool {tool_name} returned conflicting representations")
    return payloads[0]


def _supplier_tool_results(response: AgentResponse) -> dict[str, dict[str, object]]:
    call_names: dict[str, Literal["list_suppliers", "draft_po"]] = {}
    results: dict[str, dict[str, object]] = {}
    for message in response.messages or ():
        for content in message.contents:
            if content.type == "function_call" and content.call_id and content.name:
                tool_name = _supplier_tool_name(content.name)
                if tool_name is not None:
                    call_names[content.call_id] = tool_name
            elif content.type == "function_result" and content.call_id in call_names:
                tool_name = call_names[content.call_id]
                if content.exception:
                    log.error("supplier.mcp_result.tool_failed", tool=tool_name)
                    raise RuntimeError(f"Supplier MCP tool {tool_name} failed")
                if tool_name in results:
                    log.error("supplier.mcp_result.duplicate_tool_result", tool=tool_name)
                    raise RuntimeError(f"Supplier MCP tool {tool_name} returned ambiguous repeated results")
                results[tool_name] = _mcp_result_payload(content, tool_name=tool_name)
    return results


TModel = TypeVar("TModel", bound=BaseModel)


def _log_validation_failure(tool_name: str, error: ValidationError) -> None:
    diagnostics = [
        {
            "location": ".".join(str(part) for part in item["loc"]),
            "type": item["type"],
        }
        for item in error.errors(include_input=False, include_context=False, include_url=False)
    ]
    log.error("supplier.mcp_result.validation_failed", tool=tool_name, validation_errors=diagnostics)


def _validated_tool_result(payload: dict[str, object], *, tool_name: str, model: type[TModel]) -> TModel:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        _log_validation_failure(tool_name, exc)
        raise RuntimeError(f"Supplier MCP tool {tool_name} returned data outside its validated contract") from exc


def _canonical_supplier_output(response: AgentResponse) -> str:
    tool_results = _supplier_tool_results(response)
    if set(tool_results) != {"list_suppliers", "draft_po"}:
        raise RuntimeError("Supplier response requires verified list_suppliers and draft_po results")

    catalog = _validated_tool_result(
        tool_results["list_suppliers"], tool_name="list_suppliers", model=SupplierCatalogView
    )
    draft = _validated_tool_result(tool_results["draft_po"], tool_name="draft_po", model=_SupplierToolDraft)

    if catalog.sku != draft.sku:
        raise RuntimeError("Supplier catalog and draft refer to different SKUs")
    if "approval is required" not in draft.rationale.lower():
        raise RuntimeError("Supplier draft does not verify that approval is required")

    try:
        output = SupplierAgentOutput.model_validate(
            {
                "sku": draft.sku,
                "store_id": draft.store_id,
                "supplier_view": catalog.model_dump(mode="json"),
                "po_view": {
                    "supplier_id": draft.supplier_id,
                    "quantity": draft.quantity,
                    "unit_cost": draft.unit_cost,
                    "estimated_delivery_days": draft.estimated_delivery_days,
                    "status": draft.status,
                    "approval_required": True,
                    "rationale": draft.rationale,
                },
                "recommended_supplier": draft.supplier_id,
                "next_action": draft.rationale,
            }
        )
    except ValidationError as exc:
        _log_validation_failure("supplier_results", exc)
        raise RuntimeError("Supplier MCP results are inconsistent with the canonical output contract") from exc
    return output.model_dump_json()


def _is_refusal(text: str) -> bool:
    lowered = text.strip().lower()
    return any(marker in lowered for marker in ("i cannot help", "i can't help", "i am unable", "i'm unable")) or (
        '"refuse"' in lowered or '"refusal"' in lowered
    )


@dataclass(frozen=True)
class _RunnableAgent:
    """Adapter satisfying the shared make_app runnable protocol."""

    agent: GitHubCopilotAgent

    async def run(self, message: str, /) -> _AgentRunResult:
        response_stream = self.agent.run(message, stream=True)
        async for _update in response_stream:
            pass
        response = await response_stream.get_final_response()
        tool_calls = _observed_tool_calls(response)
        if _is_refusal(response.text) and not tool_calls:
            return _AgentRunResult(output_text=response.text, tool_calls=tool_calls)
        return _AgentRunResult(output_text=_canonical_supplier_output(response), tool_calls=tool_calls)


async def build_agent(settings: Settings) -> _RunnableAgent:
    default_options: GitHubCopilotOptions = {
        "model": settings.copilot_model,
        "timeout": settings.copilot_timeout_seconds,
        "on_permission_request": _approve_supplier_operations,
        "mcp_servers": {
            "supplier": {
                "type": "http",
                "url": settings.supplier_mcp_url,
                "tools": ["list_suppliers", "draft_po"],
                "timeout": int(settings.copilot_timeout_seconds * 1000),
            },
        },
    }
    agent = GitHubCopilotAgent(
        client=build_copilot_client(),
        instructions=SYSTEM_PROMPT,
        name="supplier",
        description="ZavaShop supplier specialist for replenishment and purchase-order drafts.",
        tools=list(TOOLS),
        default_options=default_options,
    )
    return _RunnableAgent(agent=agent)
