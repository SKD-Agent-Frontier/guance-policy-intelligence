"""模拟Windows短时占用和持续拒绝，验证重试仍原子且不会吞掉永久失败。"""
import pytest
from safe_run import replace_with_retry, DELAYS


def test_atomic_retry_recovers_and_permanent_denial_is_not_hidden(tmp_path):
    calls=[]
    def transient(source,target):
        calls.append((source,target))
        if len(calls)<3:
            raise PermissionError('temporary file lock')
        return 'atomic replacement succeeded'
    assert replace_with_retry(transient,'unchanged.tmp',tmp_path/'checkpoint.json',project=tmp_path,sleeper=lambda _:None)=='atomic replacement succeeded'
    assert len(calls)==3
    failures=[]
    def permanent(source,target):
        failures.append(target)
        raise PermissionError('permanent denial')
    with pytest.raises(PermissionError,match='permanent denial'):
        replace_with_retry(permanent,'unchanged.tmp',tmp_path/'checkpoint.json',project=tmp_path,sleeper=lambda _:None)
    assert len(failures)==len(DELAYS)+1
