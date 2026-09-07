import json
import zipfile
from types import SimpleNamespace

import pytest
from evaluation.doc2dial_history_roles import history_roles


def test_roles_preserve_consecutive_agent_turns_and_validate_text(tmp_path):
    archive=tmp_path/'source.zip'
    turns=[{'turn_id':1,'role':'user','utterance':'question'}, {'turn_id':2,'role':'agent','utterance':'answer'}, {'turn_id':3,'role':'agent','utterance':'clarify'}, {'turn_id':4,'role':'user','utterance':'yes'}]
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('doc2dial_dial_validation.json',json.dumps({'dial_data':{'domain':{'doc':[{'dial_id':'abc','turns':turns}]}}}))
    case=SimpleNamespace(group_id='doc2dial-abc',case_id='doc2dial-dev-abc-4',query='yes',history=('question','answer','clarify'))
    assert history_roles(archive,[case])[case.case_id]==('user','assistant','assistant')
    case.history=('question','answer','different')
    with pytest.raises(AssertionError):history_roles(archive,[case])
