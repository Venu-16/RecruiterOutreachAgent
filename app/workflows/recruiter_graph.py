from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app import config as settings
from app.db import repository
from app.schemas import CandidateProfile
from app.services.draft_generation import generate_draft
from app.services.evaluation import draft_policy_check
from app.services.qualification import qualify_contact


class OutreachState(TypedDict, total=False):
    contact_id: int
    contact: dict[str, Any]
    candidate_profile: dict[str, Any]
    qualification: dict[str, Any]
    draft_id: int
    status: str
    error_code: str
    review_decision: str
    policy_check: dict[str, Any]


def _load_contact(state: OutreachState, config: RunnableConfig) -> dict[str, Any]:
    database_path = config["configurable"]["database_path"]
    contact = repository.get_contact(database_path, state["contact_id"])
    if contact is None:
        return {"status": "failed", "error_code": "contact_not_found"}
    if contact["opt_out"] or repository.is_suppressed(database_path, contact["email"]):
        return {"contact": contact, "status": "suppressed"}
    return {"contact": contact}


def _qualify(state: OutreachState, config: RunnableConfig) -> dict[str, Any]:
    database_path = config["configurable"]["database_path"]
    contact = state["contact"]
    profile = CandidateProfile.model_validate(state.get("candidate_profile", {}))
    result = qualify_contact(contact, tuple(profile.target_roles), settings.QUALIFIED_THRESHOLD)
    serialized = result.to_dict()
    repository.save_qualification(database_path, contact["id"], serialized)
    if result.status != "qualified_for_review":
        return {"qualification": serialized, "status": result.status}
    return {"qualification": serialized}


def _create_draft(state: OutreachState, config: RunnableConfig) -> dict[str, Any]:
    database_path = config["configurable"]["database_path"]
    try:
        profile = CandidateProfile.model_validate(state["candidate_profile"])
        draft = generate_draft(
            state["contact"],
            qualify_contact(state["contact"], tuple(profile.target_roles), settings.QUALIFIED_THRESHOLD),
            profile,
            settings.MAX_DRAFT_LENGTH,
        )
        persisted = repository.create_draft(database_path, state["contact_id"], draft.model_dump())
    except (ValueError, KeyError):
        return {"status": "draft_blocked", "error_code": "draft_requirements_not_met"}
    return {"draft_id": persisted["id"], "status": "draft_created"}


def _check_draft_policy(state: OutreachState, config: RunnableConfig) -> dict[str, Any]:
    database_path = config["configurable"]["database_path"]
    draft = repository.get_draft(database_path, state["draft_id"])
    contact = state["contact"]
    result = draft_policy_check(
        draft or {}, state.get("candidate_profile", {}), contact, state.get("qualification", {}),
        repository.is_suppressed(database_path, contact["email"]),
    )
    return {"policy_check": result,
            "status": "awaiting_human_review" if result["passed"] else "draft_blocked",
            "error_code": None if result["passed"] else "draft_policy_check_failed"}


def _review_interrupt(state: OutreachState, config: RunnableConfig) -> dict[str, Any]:
    decision = interrupt({"draft_id": state["draft_id"], "review_required": True})
    if not isinstance(decision, dict) or decision.get("decision") not in {"approved", "rejected"}:
        return {"status": "review_invalid"}
    database_path = config["configurable"]["database_path"]
    target_draft_id = int(decision.get("draft_id", state["draft_id"]))
    repository.review_draft(database_path, target_draft_id, decision["decision"], decision.get("note"))
    return {"review_decision": decision["decision"], "status": decision["decision"]}


def _route_after_load(state: OutreachState) -> str:
    if state.get("error_code") or state.get("status") == "suppressed":
        return END
    return "qualify"


def _route_after_qualification(state: OutreachState) -> str:
    return "create_draft" if state.get("status") is None else END


def build_graph(checkpointer: Any):
    builder = StateGraph(OutreachState)
    builder.add_node("load_contact", _load_contact)
    builder.add_node("qualify", _qualify)
    builder.add_node("create_draft", _create_draft)
    builder.add_node("policy_check", _check_draft_policy)
    builder.add_node("human_review", _review_interrupt)
    builder.add_edge(START, "load_contact")
    builder.add_conditional_edges("load_contact", _route_after_load, {"qualify": "qualify", END: END})
    builder.add_conditional_edges("qualify", _route_after_qualification, {"create_draft": "create_draft", END: END})
    builder.add_edge("create_draft", "policy_check")
    builder.add_conditional_edges("policy_check", lambda state: "human_review" if state.get("policy_check", {}).get("passed") else END,
                                  {"human_review": "human_review", END: END})
    builder.add_edge("human_review", END)
    return builder.compile(checkpointer=checkpointer)


def run_workflow(database_path: str | Path, thread_id: str, contact_id: int,
                 candidate_profile: CandidateProfile) -> dict[str, Any]:
    path = str(Path(database_path).resolve())
    contact = repository.get_contact(path, contact_id)
    if contact is None:
        return {"contact_id": contact_id, "status": "failed", "error_code": "contact_not_found"}
    if contact["opt_out"] or repository.is_suppressed(path, contact["email"]):
        repository.record_workflow(path, thread_id, contact_id, "suppressed")
        return {"contact_id": contact_id, "status": "suppressed"}
    existing = repository.get_latest_draft_for_contact(path, contact_id)
    if existing:
        return {"contact_id": contact_id, "draft_id": existing["id"],
                "status": existing["review_status"], "idempotent_replay": True}
    repository.record_workflow(path, thread_id, contact_id, "running")
    try:
        with SqliteSaver.from_conn_string(path) as checkpointer:
            checkpointer.setup()
            graph = build_graph(checkpointer)
            result = graph.invoke(
                {"contact_id": contact_id, "candidate_profile": candidate_profile.model_dump()},
                {"configurable": {"thread_id": thread_id, "database_path": path}},
            )
    except Exception as exc:
        repository.record_workflow(path, thread_id, contact_id, "failed", type(exc).__name__)
        raise
    repository.record_workflow(path, thread_id, contact_id, result.get("status", "complete"), result.get("error_code"))
    return result


def resume_review(database_path: str | Path, thread_id: str, contact_id: int,
                  decision: str, note: str | None = None, draft_id: int | None = None) -> dict[str, Any]:
    path = str(Path(database_path).resolve())
    repository.record_workflow(path, thread_id, contact_id, "review_resuming")
    try:
        with SqliteSaver.from_conn_string(path) as checkpointer:
            checkpointer.setup()
            graph = build_graph(checkpointer)
            result = graph.invoke(
                Command(resume={"decision": decision, "note": note, "draft_id": draft_id}),
                {"configurable": {"thread_id": thread_id, "database_path": path}},
            )
    except Exception as exc:
        repository.record_workflow(path, thread_id, contact_id, "failed", type(exc).__name__)
        raise
    repository.record_workflow(path, thread_id, contact_id, result.get("status", "complete"), result.get("error_code"))
    return result