import numpy as np
import pytest
from scripts.audit_mtrag_dense_shards import check_vector_file
from scripts.adapt_mtrag_retrieval_dataset import digest


@pytest.mark.parametrize('bad',['shape','dtype','nan','norm','checksum','none'])
def test_cache_validates_bytes_and_positive_vector_contract(tmp_path,bad):
    values=np.zeros((2,1024),dtype=np.float32);values[:,0]=1
    if bad=='shape':values=values[:1]
    if bad=='dtype':values=values.astype(np.float16)
    if bad=='nan':values[0,0]=np.nan
    if bad=='norm':values[0,0]=2
    path=tmp_path/'v.npy';np.save(path,values)
    meta={'vectors_sha256':'wrong' if bad=='checksum' else digest(path)}
    if bad=='none':assert check_vector_file(path,meta,2)==(1.,1.)
    else:
        with pytest.raises(ValueError):check_vector_file(path,meta,2)
