"""Retail owner's order-status transitions, independent of task fixtures.

Mirrors tau2.domains.retail.tools.RetailTools: cancel and item modification
require exactly pending; address/payment accept pending and pending (item modified);
return/exchange require delivered and consume that state. Other eligibility,
payment, item and policy checks remain at the official tool and domain review.
"""
from application.action_compatibility import ActionStateTransition


def retail_action_transitions():
    def order(states, result=None):
        return ActionStateTransition("retail.order.status", "order_id", states, result)
    return {
        "cancel_pending_order": order(("pending",), "cancelled"),
        "modify_pending_order_items": order(("pending",), "pending (item modified)"),
        "modify_pending_order_address": order(("pending", "pending (item modified)")),
        "modify_pending_order_payment": order(("pending", "pending (item modified)")),
        "return_delivered_order_items": order(("delivered",), "return requested"),
        "exchange_delivered_order_items": order(("delivered",), "exchange requested"),
    }
