"""Bind one compiled media work item to an authorized turn asset."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping

from application.routing_media_probe import (
    RoutingAssetRef,
    RoutingMediaContext,
    RoutingMediaNeed,
    RoutingMediaOutcome,
    RoutingMediaProbe,
    RoutingObservationRef,
)
from application.task_media import TaskMediaPolicy
from application.turn_plan import CompiledWorkItem, TurnPlan


class CommandPrimaryMediaError(ValueError):
    pass


@dataclass(frozen=True)
class BoundCommandPrimaryMedia:
    item: CompiledWorkItem
    policy: TaskMediaPolicy
    asset: RoutingAssetRef
    producer_task: Mapping[str, Any]


def media_policy_for(plan: TurnPlan | None) -> TaskMediaPolicy | None:
    if plan is None or plan.work is None:
        return None
    policies = tuple(
        item.media_policy
        for item in plan.work.items
        if item.media_policy is not None
    )
    if not policies:
        return None
    if len(plan.work.items) != 1 or len(policies) != 1:
        raise CommandPrimaryMediaError("media primary requires one work item")
    return policies[0]


def bind_command_primary_media(
    plan: TurnPlan,
    *,
    current_assets: tuple[RoutingAssetRef, ...],
    observations: tuple[RoutingObservationRef, ...] = (),
) -> BoundCommandPrimaryMedia:
    policy = media_policy_for(plan)
    if policy is None or plan.work is None:
        raise CommandPrimaryMediaError("turn plan has no media requirement")
    result = RoutingMediaProbe().resolve(
        RoutingMediaNeed(
            stage=policy.requirement.minimum_stage,
            scope=policy.scope,
        ),
        RoutingMediaContext(
            current_assets=current_assets,
            observations=observations,
        ),
    )
    if result.outcome not in {RoutingMediaOutcome.L1, RoutingMediaOutcome.L2}:
        raise CommandPrimaryMediaError(result.reason_code)
    if result.asset is None:
        raise CommandPrimaryMediaError("MEDIA_TARGET_MISSING")
    return BoundCommandPrimaryMedia(
        item=plan.work.items[0],
        policy=policy,
        asset=result.asset,
        producer_task={
            "media_need": policy.requirement.minimum_stage.name,
            "requirement_id": policy.requirement.requirement_id,
        },
    )


async def decide_command_primary_media(
    plan: TurnPlan,
    *,
    asset_ids: tuple[str, ...],
    tenant_id: str,
    user_id: str,
    turn_ref: str,
    asset_store: Any,
    producer: Any,
    validator: Any,
) -> tuple[Any, tuple[str, ...]]:
    assets = await asyncio.gather(*(
        asyncio.to_thread(
            asset_store.get,
            asset_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        for asset_id in asset_ids
    ))
    bound = bind_command_primary_media(
        plan,
        current_assets=tuple(
            RoutingAssetRef(
                admission.asset_id,
                admission.checksum,
                turn_ref,
            )
            for admission, _content in assets
        ),
    )
    selected = (bound.asset.asset_id,)
    decision = await producer.decide_media_requirement(
        task=bound.producer_task,
        asset_ids=selected,
    )
    validator.validate(
        decision,
        policies=(bound.policy.requirement,),
        allowed_asset_ids=selected,
    )
    return decision, selected
