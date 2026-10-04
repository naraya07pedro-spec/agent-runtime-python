from uuid import uuid4

import pytest
from sqlalchemy import text

from app.domain import ApprovalDecision, CreateExecution, ExternalFault, Fault, State
from app.providers import OpenAIProvider
from app.tables import Approval, ToolCall
from tests.conftest import ScriptedProvider, turn

pytestmark = pytest.mark.integration


async def test_read_tool_then_final_answer(rig):
    eid = await rig.create()
    await rig.runtime.advance("worker-a", eid)
    first = await rig.store.get(eid)
    assert first.state == State.CREATED and first.steps == 1
    await rig.runtime.advance("worker-b", eid)
    final = await rig.store.get(eid)
    assert final.state == State.SUCCEEDED
    assert final.outcome == {"text": "Sandbox action completed.", "refused": False}
    history = await rig.store.history(eid)
    assert [e.kind for e in history.items] == [
        "created",
        "claimed",
        "model_started",
        "model_usage",
        "action_proposed",
        "dispatch_intent",
        "tool_succeeded",
        "claimed",
        "model_started",
        "model_usage",
        "completed",
    ]
    assert await rig.effects() == 0


async def test_request_identity_replays_and_rejects_conflicts(rig):
    request = CreateExecution(business_key="invoice-1", prompt="lookup customer demo")
    eid, created = await rig.store.create(request, "key-1", uuid4())
    again, created_again = await rig.store.create(request, "key-1", uuid4())
    assert (created, created_again) == (True, False) and again == eid
    with pytest.raises(Fault, match="idempotency_conflict"):
        await rig.store.create(request.model_copy(update={"prompt": "changed"}), "key-1", uuid4())
    with pytest.raises(Fault, match="idempotency_conflict"):
        await rig.store.create(request, "different-key", uuid4())
    with pytest.raises(Fault, match="idempotency_conflict"):
        await rig.store.create(
            CreateExecution(business_key="other", prompt=request.prompt), "key-1", uuid4()
        )


async def test_approval_is_persistent_and_required(rig):
    eid = await rig.create("notify customer demo")
    await rig.runtime.advance("worker-a", eid)
    view = await rig.store.get(eid)
    assert view.state == State.WAITING_FOR_APPROVAL and view.action
    assert await rig.effects() == 0
    await rig.runtime.advance("worker-b", eid)
    assert await rig.effects() == 0
    await rig.store.decide_approval(
        view.action.id,
        ApprovalDecision(fingerprint=view.action.fingerprint, approve=True),
        "operator-1",
    )
    async with rig.store.sessions() as session:
        approval = await session.get(Approval, view.action.id)
        assert (
            approval.actor == "operator-1"
            and approval.decided_at is not None
            and approval.decision is True
        )
    await rig.runtime.advance("restarted-worker", eid)
    assert await rig.effects() == 1
    await rig.runtime.advance("worker-c", eid)
    assert (await rig.store.get(eid)).state == State.SUCCEEDED


async def test_approval_denial_is_terminal(rig):
    eid = await rig.create("notify customer demo")
    await rig.runtime.advance("w", eid)
    action = (await rig.store.get(eid)).action
    await rig.store.decide_approval(
        action.id, ApprovalDecision(fingerprint=action.fingerprint, approve=False), "human"
    )
    assert (await rig.store.get(eid)).error_code == "approval_denied"
    assert await rig.runtime.advance("w", eid) is None
    assert await rig.effects() == 0


@pytest.mark.parametrize("mutation", ["payload", "expired", "policy"])
async def test_approved_action_cannot_mutate_or_outlive_approval(rig, mutation):
    eid = await rig.create("notify customer demo")
    await rig.runtime.advance("w", eid)
    action = (await rig.store.get(eid)).action
    await rig.store.decide_approval(
        action.id, ApprovalDecision(fingerprint=action.fingerprint, approve=True), "human"
    )
    async with rig.store.sessions.begin() as session:
        if mutation == "payload":
            call = await session.get(ToolCall, action.id)
            call.arguments = {"customer_id": "demo", "message": "changed after approval"}
        elif mutation == "expired":
            await session.execute(
                text(
                    "UPDATE approvals SET expires_at=clock_timestamp()-interval '1 second' WHERE call_id=:id"
                ),
                {"id": action.id},
            )
        else:
            call = await session.get(ToolCall, action.id)
            call.approval_required = False
    await rig.runtime.advance("w", eid)
    assert (await rig.store.get(eid)).state == State.FAILED_PERMANENT
    assert await rig.effects() == 0


async def test_forged_or_repeated_approval_rejected(rig):
    eid = await rig.create("notify customer demo")
    await rig.runtime.advance("w", eid)
    action = (await rig.store.get(eid)).action
    with pytest.raises(Fault, match="approval_fingerprint_conflict"):
        await rig.store.decide_approval(
            action.id, ApprovalDecision(fingerprint="0" * 64, approve=True), "human"
        )
    correct = ApprovalDecision(fingerprint=action.fingerprint, approve=True)
    await rig.store.decide_approval(action.id, correct, "human")
    with pytest.raises(Fault, match="approval_not_pending"):
        await rig.store.decide_approval(action.id, correct, "other-human")


@pytest.mark.parametrize(
    "tool,args,code",
    [
        ("shell", {}, "unknown_tool"),
        ("lookup_customer", {}, "tool_arguments_invalid"),
        ("lookup_customer", {"customer_id": "demo", "approval": True}, "tool_arguments_invalid"),
    ],
)
async def test_untrusted_model_cannot_bypass_policy(rig, tool, args, code):
    rig.runtime.provider = ScriptedProvider(turn({"kind": "tool", "tool": tool, "arguments": args}))
    eid = await rig.create()
    await rig.runtime.advance("w", eid)
    assert (await rig.store.get(eid)).error_code == code
    assert await rig.effects() == 0


async def test_missing_tool_credentials_fail_before_proposal(rig):
    rig.settings.tool_api_token = None
    eid = await rig.create("create ticket demo")
    await rig.runtime.advance("w", eid)
    view = await rig.store.get(eid)
    assert view.error_code == "tool_configuration_missing" and view.action is None
    assert await rig.effects() == 0


async def test_missing_model_credentials_cannot_succeed(rig):
    rig.runtime.provider = OpenAIProvider(rig.client, rig.settings)
    eid = await rig.create()
    await rig.runtime.advance("w", eid)
    assert (await rig.store.get(eid)).error_code == "model_configuration_missing"


async def test_retry_is_persisted_and_honors_retry_after(rig):
    rig.runtime.provider = ScriptedProvider(
        ExternalFault("rate_limited", transient=True, retry_after=15),
        turn({"kind": "finish", "text": "done"}),
    )
    eid = await rig.create()
    await rig.runtime.advance("w", eid)
    view = await rig.store.get(eid)
    assert view.state == State.RETRY_PENDING and view.retry_count == 1
    assert await rig.runtime.advance("w2", eid) is None
    async with rig.store.sessions.begin() as session:
        await session.execute(
            text(
                "UPDATE executions SET next_attempt_at=clock_timestamp()-interval '1 second' WHERE id=:id"
            ),
            {"id": eid},
        )
    await rig.runtime.advance("w3", eid)
    assert (await rig.store.get(eid)).state == State.SUCCEEDED


async def test_retry_budget_exhaustion(rig):
    rig.runtime.provider = ScriptedProvider(
        *[ExternalFault("unavailable", transient=True) for _ in range(4)]
    )
    eid = await rig.create()
    for _ in range(4):
        await rig.runtime.advance("w", eid)
    view = await rig.store.get(eid)
    assert view.error_code == "retry_budget_exhausted" and view.retry_count == 3


async def test_token_budget_blocks_tool_dispatch(rig):
    rig.runtime.provider = ScriptedProvider(
        turn(
            {
                "kind": "tool",
                "tool": "upsert_ticket",
                "arguments": {"customer_id": "demo", "summary": "test"},
            },
            input_tokens=33,
        )
    )
    eid = await rig.create(max_tokens=32)
    await rig.runtime.advance("w", eid)
    assert (await rig.store.get(eid)).error_code == "token_budget_exceeded"
    assert await rig.effects() == 0


async def test_step_budget_survives_multiple_workers(rig):
    rig.runtime.provider = ScriptedProvider(
        *[
            turn({"kind": "tool", "tool": "lookup_customer", "arguments": {"customer_id": name}})
            for name in ["one", "two"]
        ]
    )
    eid = await rig.create(max_steps=1)
    await rig.runtime.advance("w1", eid)
    await rig.runtime.advance("w2", eid)
    assert (await rig.store.get(eid)).error_code == "step_budget_exhausted"


async def test_repeated_action_is_denied_even_on_new_model_turn(rig):
    output = turn(
        {
            "kind": "tool",
            "tool": "upsert_ticket",
            "arguments": {"customer_id": "demo", "summary": "ticket"},
        }
    )
    rig.runtime.provider = ScriptedProvider(output, output)
    eid = await rig.create()
    await rig.runtime.advance("w1", eid)
    await rig.runtime.advance("w2", eid)
    assert (await rig.store.get(eid)).error_code == "repeated_action_denied"
    assert await rig.effects() == 1


async def test_cancel_and_history_pagination(rig):
    eid = await rig.create()
    await rig.store.cancel(eid)
    assert (await rig.store.get(eid)).state == State.CANCELLED
    first = await rig.store.history(eid, limit=1)
    second = await rig.store.history(eid, cursor=first.next_cursor, limit=1)
    assert first.items[0].kind == "created" and second.items[0].kind == "cancelled"
    assert second.next_cursor is None
    with pytest.raises(Fault, match="illegal_transition"):
        await rig.store.cancel(eid)


async def test_database_constraints_reject_invalid_terminal_state(rig):
    from sqlalchemy.exc import IntegrityError

    eid = await rig.create()
    with pytest.raises(IntegrityError):
        async with rig.store.sessions.begin() as session:
            await session.execute(
                text("UPDATE executions SET state='SUCCEEDED' WHERE id=:id"), {"id": eid}
            )
    assert (await rig.store.get(eid)).state == State.CREATED
