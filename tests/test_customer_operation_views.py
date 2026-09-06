import asyncio
from datetime import datetime, timezone

from services.customer_operation_views import order_read_view, refund_eligibility_read_view, ORDER_STATUS_LABELS
from services.customer_operations import Order, OrderStatus, CustomerOperationsService


def order(status=OrderStatus.DELIVERED, until=None):
    return Order('DP1','u','耳机',100,'CNY',status,until,1,
                 '2026-09-01T00:00:00+00:00','2026-09-06T00:00:00+00:00')


def test_order_projection_preserves_business_values_and_distinguishes_record_time():
    for status in OrderStatus:
        source=order(status).to_dict();original=dict(source)
        view=order_read_view(source)
        assert source==original
        assert 'user_id' not in view and 'created_at' not in view
        assert view['record_created_at']==source['created_at']
        assert view['status_label']==ORDER_STATUS_LABELS[status.value]
        assert '实际下单时间' in view['field_semantics']['record_created_at']
        for key in source.keys()-{'user_id','created_at'}: assert view[key]==source[key]


def test_all_produced_eligibility_outcomes_keep_precheck_and_unknown_distinct():
    service=object.__new__(CustomerOperationsService)
    service._clock=lambda:datetime(2026,9,6,tzinfo=timezone.utc)
    observed=set()
    for status in OrderStatus:
        for until in (None,'2026-09-01T00:00:00+00:00','2026-09-10T00:00:00+00:00'):
            for has_refund in (False,True):
                result=service._eligibility(order(status,until),has_refund=has_refund)
                raw=result.to_dict();view=refund_eligibility_read_view(raw)
                assert all(view[k]==v for k,v in raw.items())
                observed.add(view['assessment_status'])
                assert (view['assessment_status']=='INSUFFICIENT_DATA')==(result.reason_code=='refund_window_missing')
                assert (view['assessment_status']=='ELIGIBLE')==result.eligible
    assert observed=={'ELIGIBLE','INELIGIBLE','INSUFFICIENT_DATA'}


def test_real_tools_export_versioned_semantics_and_no_private_identity(customer_operations):
    from tests.test_customer_operations_tools import setup_runtime, context
    owner,manager=setup_runtime(customer_operations)
    result=asyncio.run(manager.execute_for_agent('order_lookup',{'order_id':'order-1'},agent_type='billing',context=context()))
    assert result.success and result.output_schema_version=='order-view-v2'
    assert result.data['status_label']=='已送达'
    assert 'record_created_at' in result.data and 'user_id' not in result.data
    # Evidence projection persists the semantic fields rather than only the ToolMessage.
    from infrastructure.target_agent_result_adapter import fact_from_tool_result
    from tests.test_target_framework_agent import _item
    import json
    fact=fact_from_tool_result(_item(),result)
    assert fact.producer_version=='order-view-v2'
    assert json.loads(fact.value_json)['field_semantics']==result.data['field_semantics']
    refund=asyncio.run(manager.execute_for_agent('refund_eligibility_check',{'order_id':'order-1'},agent_type='billing',context=context()))
    assert refund.success and refund.output_schema_version=='refund-eligibility-v2'
    assert refund.data['assessment_status']=='ELIGIBLE'


def test_current_evidence_authority_rejects_retired_read_versions():
    import pytest
    from application.authority_policy import AuthorityPolicyRegistry, AuthorityContractError
    registry=AuthorityPolicyRegistry.v1()
    for tool,requirement,prefix in (
        ('order_lookup','order.current_state','order-view'),
        ('refund_eligibility_check','refund.eligibility','refund-eligibility'),
    ):
        kwargs=dict(requirement_id=requirement,adapter_id='business-tool-evidence-adapter',
                    adapter_version='business-tool-evidence-adapter-v1',producer_id=tool)
        registry.authorize_evidence_adapter(**kwargs,producer_version=prefix+'-v2')
        with pytest.raises(AuthorityContractError,match='producer version'):
            registry.authorize_evidence_adapter(**kwargs,producer_version=prefix+'-v1')
