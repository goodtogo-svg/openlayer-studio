from pathlib import Path
import json
import pytest
from ali import runtime
from ali.engines.common import checkpoint


def test_missing_checkpoint_never_reports_success(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'ROOT', tmp_path)
    with pytest.raises(RuntimeError, match='missing checkpoint'):
        checkpoint('inpaint-lama')


def test_resume_detects_changed_mask_even_after_failed_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'ROOT', tmp_path)
    source=tmp_path/'source.png';source.write_bytes(b'input image fixture')
    mask=tmp_path/'mask.png';mask.write_bytes(b'first mask')
    output=tmp_path/'job'
    with pytest.raises(RuntimeError, match='Missing isolated environment'):
        runtime.run_engine('inpaint',source,output,{'mask':str(mask)})
    state=json.loads((output/'state.json').read_text())
    assert state['status']=='failed'
    assert state['request']['auxiliary_sha256']['mask']==runtime.digest(mask)
    mask.write_bytes(b'edited mask')
    with pytest.raises(ValueError, match='Resume input/options changed'):
        runtime.run_engine('inpaint',source,output,{'mask':str(mask)},resume=True)
