"""Experiment protocol validation; no network calls."""
from types import SimpleNamespace
import pytest
from scripts.run_atomic_boundary_experiment import extract, ATOM_SCHEMA, clean


def response(payload, stop='tool_use'):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type='tool_use', name='submit_atoms', input=payload)])


def test_atom_output_preserves_complete_conditional_proposition():
    data={'atoms':[{'quote':'如果完好可以退','proposition':'如果商品完好，则可以退货。'}]}
    assert extract(response(data), 'submit_atoms', ATOM_SCHEMA)==data


@pytest.mark.parametrize('payload',[{}, {'atoms':[]}, {'atoms':[{'quote':'可以退'}]}])
def test_malformed_extraction_is_not_accepted(payload):
    with pytest.raises(Exception):extract(response(payload),'submit_atoms',ATOM_SCHEMA)


def test_truncated_extraction_is_not_accepted():
    with pytest.raises(ValueError):extract(response({'atoms':[]},'max_tokens'),'submit_atoms',ATOM_SCHEMA)


def test_export_removes_reasoning_but_retains_check_and_cost_material():
    data={'content':[{'type':'thinking','thinking':'private'}, {'type':'tool_use','input':{'atoms':[]}}], 'usage':{'output_tokens':10}}
    assert clean(data)=={'content':[{'type':'tool_use','input':{'atoms':[]}}], 'usage':{'output_tokens':10}}
