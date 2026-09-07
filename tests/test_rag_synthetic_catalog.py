"""Synthetic policies and queries share an explicit host-owned vocabulary."""
import pytest
from application.sales_channels import require_catalog_channel, sales_channel_schema, filter_contract
from evaluation.rag_applicability_dev import applicability_catalog, applicability_development


def test_every_fixture_channel_is_declared_for_import_and_query():
    catalog=applicability_catalog()
    docs,cases,options,_,_=applicability_development()
    for doc in docs:
        require_catalog_channel(doc.metadata.get('channel','global'),catalog,source=True)
    for case in cases:
        channel=options[case.case_id].get('sales_channel')
        if channel:require_catalog_channel(channel,catalog)
    assert set(sales_channel_schema(catalog)['enum'])=={'web','store'}
    assert filter_contract()['sales_channels']=={}
    with pytest.raises(ValueError):require_catalog_channel('shipping',catalog)
    # Fresh snapshots do not share a mutable channel authority.
    catalog['sales_channels']['injected']='not a fixture channel'
    assert 'injected' not in applicability_catalog()['sales_channels']


def test_synthetic_policy_effective_dates_are_fixed_not_import_time():
    from datetime import datetime, timezone
    from evaluation.rag_ecommerce_dev import synthetic_development
    docs,_=synthetic_development()
    assert all(datetime.fromisoformat(d.metadata['effective_from'])==datetime(2020,1,1,tzinfo=timezone.utc) for d in docs)
