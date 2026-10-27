"""FastAPI entrypoint for the ZavaShop orchestrator agent."""

from __future__ import annotations

from uuid import uuid4

import structlog
from fastapi import FastAPI
from structlog.contextvars import bind_contextvars, clear_contextvars

from src.shared.server import make_app
from src.shared.settings import Settings

from .a2a import A2AClient, SpecialistInvoker
from .agent import build_agent
from .contracts import Goal, Plan
from .workflow import build_plan_workflow, run_plan_workflow

log = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None, specialist_client: SpecialistInvoker | None = None) -> FastAPI:
    """Create the orchestrator app with /invoke and /plan endpoints."""

    app = make_app(name="orchestrator", build=build_agent, settings=settings)

    @app.post("/plan", response_model=Plan)
    async def plan(goal: Goal) -> Plan:
        run_id = uuid4().hex
        bind_contextvars(agent_name="orchestrator", run_id=run_id, span_id=run_id[:16])
        try:
            log.info("orchestrator.plan.begin", sku=goal.sku, store_id=goal.store_id)
            current_settings = app.state.settings
            if not isinstance(current_settings, Settings):
                raise RuntimeError("orchestrator settings were not initialized")
            client = specialist_client or A2AClient(settings=current_settings)
            workflow = build_plan_workflow(client)
            result = await run_plan_workflow(workflow, goal)
            log.info("orchestrator.plan.end", sku=goal.sku, store_id=goal.store_id)
            return result
        finally:
            clear_contextvars()

    app.state.plan_endpoint = plan

    return app


app = create_app()
