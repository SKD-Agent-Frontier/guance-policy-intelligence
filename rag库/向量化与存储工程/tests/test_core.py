"""离线回归只验证本地逻辑，不冒充真实模型或数据库集成通过。"""
import base64
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import numpy as np
import pytest
import httpx

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from vector_store.common import digest,identity,point_id,validate_segments,atomic_bytes,read_json,long_path
from vector_store.embedding import normalize,Cache,Service
from vector_store.media import parse_ocr,smart_resize,derive_visual
from vector_store.prepare import reviewed_exclusion
from vector_store.candidates import representation
from vector_store.validation import compare_point
from vector_store.dedup import critical,comparison_view


def test_unicode_crlf_and_repeated_passages():
    source='头😀\r\n重复句。\r\n重复句。'
    start=source.rindex('重复句。')
    validate_segments(source,[{'start':start,'end':len(source),'text':'重复句。'}])
    with pytest.raises(ValueError):
        validate_segments(source,[{'start':start-1,'end':len(source)-1,'text':'重复句。'}])


def test_identity_is_stable_but_keeps_sources_distinct():
    assert digest({'x':1,'y':2})==digest({'y':2,'x':1})
    assert identity('x','ab','c')!=identity('x','a','bc')
    doc={'id':'d1','planid':'1','source_hash':'a'}
    source={'id':'c1'}
    description={'type':'text','token_ids':[1,2],'expected_tokens':2}
    a=representation(doc,source,'text_chunk','text',description,'p')
    b=representation({**doc,'id':'d2','planid':'2'},source,'text_chunk','text',description,'p')
    assert a['input_hash']==b['input_hash'] and a['point_id']!=b['point_id']
    assert a['point_id']==point_id(a['id'])


@pytest.mark.parametrize('value',[np.zeros(4096),np.ones(8),np.full(4096,np.nan),np.full(4096,np.inf)])
def test_invalid_vectors_rejected(value):
    with pytest.raises(ValueError):normalize(value)


def test_base64_float32_and_cache_tampering(tmp_path):
    original=np.arange(1,4097,dtype='<f4')
    vector=normalize(base64.b64encode(original.tobytes()).decode())
    assert abs(float(np.linalg.norm(vector.astype(float)))-1)<1e-6
    cache=Cache('p',tmp_path)
    cache.put('key',vector,{'prompt_tokens':20})
    assert np.array_equal(cache.get('key')[0],vector)
    (tmp_path/'key.f32').write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='checksum'):cache.get('key')


def make_service(response):
    service=Service({'embedding_base_url':'http://example.invalid/v1','embedding_api_key':'test','postgres_dsn':'test','qdrant_api_key':'test'},'qwen3-vl-embedding-8b',audit=False)
    service.client.close()
    service.client=httpx.Client(base_url='http://example.invalid/v1',transport=httpx.MockTransport(lambda req:httpx.Response(200,json=response)))
    return service


def test_actual_input_token_total_and_response_index_checked():
    vector=base64.b64encode(normalize(np.ones(4096)).tobytes()).decode()
    reply={'model':'qwen3-vl-embedding-8b','usage':{'prompt_tokens':3},
           'data':[{'index':1,'embedding':vector},{'index':0,'embedding':vector}]}
    service=make_service(reply)
    values,_=service.text_batch([[1,2],[3]])
    assert len(values)==2
    service.close()
    reply['usage']['prompt_tokens']=2
    service=make_service(reply)
    with pytest.raises(ValueError,match='token count'):service.text_batch([[1,2],[3]])
    service.close()


def test_duplicate_returned_indices_are_not_silently_reordered():
    reply={'model':'qwen3-vl-embedding-8b','usage':{'prompt_tokens':2},
           'data':[{'index':0,'embedding':[]},{'index':0,'embedding':[]}]}
    service=make_service(reply)
    with pytest.raises(ValueError,match='index mismatch'):service.text_batch([[1],[2]])
    service.close()


def test_ocr_bbox_and_incomplete_output():
    text=json.dumps({'image_type':'text','regions':[{'kind':'paragraph','bbox':[100,200,900,800],'text':'识别文字','uncertain':False}]})
    result=parse_ocr(text,2000,1000)
    assert result['regions'][0]['bbox']==[200,200,1800,800]
    with pytest.raises(ValueError,match='incomplete'):parse_ocr(text,2000,1000,'length')
    with pytest.raises(ValueError,match='outside'):parse_ocr(text.replace('900','1900'),2000,1000)


def test_uncertain_ocr_does_not_become_ready_fact():
    result=parse_ocr(json.dumps({'image_type':'text','regions':[{'kind':'paragraph','bbox':[0,0,1000,1000],'text':'金额□元','uncertain':False}]}),100,100)
    assert result['status']=='pending'
    parent,children=derive_visual({'id':'a'},{'id':'o','document_id':'d'},{'id':'e',**result})
    assert children[0]['status']=='pending'
    assert children[0]['offset_unit']=='unicode_codepoint_in_ocr_transcript'
    assert 'start' not in children[0] and parent['transcript']=='金额□元'


def test_image_resize_is_patch_aligned_and_budget_bounded():
    h,w=smart_resize(6000,4000)
    assert h%32==w%32==0 and h*w<=1048576
    assert (h//16)*(w//16)//4<=1024


def test_visual_transcript_ranges_and_heading_context():
    extraction=parse_ocr(json.dumps({'image_type':'text','regions':[
        {'kind':'heading','bbox':[0,0,1000,100],'text':'标题'},
        {'kind':'paragraph','bbox':[0,100,1000,900],'text':'正文😀\r\n保留换行。'}]}),100,200)
    parent,children=derive_visual({'id':'a'},{'id':'o','document_id':'d'},{'id':'e',**extraction})
    c=children[0]
    assert parent['transcript'][c['derived_start']:c['derived_end']]==c['text']
    assert c['context_segments'][0]['text']=='标题'


def test_reviewed_bad_chunks_excluded_without_changing_source():
    doc={'planid':'29326','source_hash':'3b2f1487d7b732fb44119b6be8a2fffb26cd24cd83d35bf6df499c3229b0123d'}
    chunk={'source_segments':[{'start':61806,'end':61843,'text':'原文'}]}
    assert reviewed_exclusion(doc,chunk)=='reviewed_pdf_word_split_确保'
    assert reviewed_exclusion({**doc,'source_hash':'different'},chunk) is None


def test_number_and_negation_changes_never_equated():
    assert critical('补助100万元，不得重复申领。')!=critical('补助200万元，可重复申领。')
    source='Ａ  1\r\n甲'
    assert comparison_view(source)=='A1甲' and source=='Ａ  1\r\n甲'


def test_equal_point_counts_do_not_hide_wrong_payload_or_vector():
    vector=normalize(np.arange(1,4097,dtype=np.float32))
    point=SimpleNamespace(payload={'planid':'wrong'},vector={'dense':vector.tolist()})
    assert compare_point(point,{'planid':'right'},vector)==['payload_mismatch']
    point.payload={'planid':'right'};point.vector={'dense':np.zeros(4096).tolist()}
    assert compare_point(point,point.payload,vector)==['vector_mismatch']


def test_atomic_write_preserves_raw_bytes(tmp_path):
    file=tmp_path/'raw.bin';value='😀\r\n甲\n'.encode()
    atomic_bytes(file,value)
    assert file.read_bytes()==value


def test_near_duplicates_with_changed_amount_remain_separate(tmp_path):
    pytest.importorskip('datasketch')
    from vector_store.dedup import detect
    common='围绕产业发展和公共服务提升，支持企业科技创新，提高资源配置效率。'*4
    chunks=[{'id':'b','source_segments':[{'text':common+'补助100万元，不得重复申领。'}]},
            {'id':'a','source_segments':[{'text':common+'补助200万元，不得重复申领。'}]}]
    summary=detect(chunks,tmp_path,{'threshold':.8,'num_perm':64,'ngram':5,'seed':42,'max_candidates_per_chunk':100})
    from vector_store.common import rows
    pairs=list(rows(tmp_path/'near_pairs.jsonl'))
    assert summary['sources_removed']==0 and len(pairs)==1
    assert pairs[0]['status']=='rejected_as_equivalent'
    assert pairs[0]['left_id']=='a' and pairs[0]['left_signals']['numbers'][0]=='200万元'


def test_changed_ocr_regions_cannot_reuse_unmodified_raw_response(tmp_path,monkeypatch):
    from PIL import Image
    from vector_store import media
    monkeypatch.setattr(media,'SHARED',tmp_path)
    path=tmp_path/'source.png';Image.new('RGB',(100,100),'white').save(path)
    asset={'sha256':digest(path.read_bytes()),'width':100,'height':100}
    body=json.dumps({'image_type':'text','regions':[{'kind':'paragraph','bbox':[0,0,1000,1000],'text':'真实转录'}]})
    class Stub:
        def request(self,*args):
            return {'model':'vision','choices':[{'finish_reason':'stop','message':{'content':body}}]}
    config={'vision_model':'vision','vision_max_output_tokens':100}
    result,hit=media.recognize(asset,path,Stub(),config)
    assert not hit
    profile,_=media.recognition_profile(config)
    cache=long_path(tmp_path/'.cache/ocr'/profile/(asset['sha256']+'.json'))
    result['regions'][0]['text']='伪造转录'
    cache.write_text(json.dumps(result),encoding='utf-8')
    with pytest.raises(ValueError,match='regions differ'):
        media.recognize(asset,path,Stub(),config)


def test_concurrent_ocr_keeps_source_order(tmp_path,monkeypatch):
    import time
    from vector_store import candidates
    from vector_store.common import write_rows
    prepared=tmp_path/'prepared';prepared.mkdir()
    output=tmp_path/'result';output.mkdir()
    write_rows(prepared/'media_assets.jsonl',[
        {'id':'first','sha256':'a','paths':['a.png'],'status':'registered'},
        {'id':'second','sha256':'b','paths':['b.png'],'status':'registered'}])
    write_rows(prepared/'media_sources.jsonl',[])
    completed=[]
    def recognize(asset,*args):
        if asset['id']=='first':time.sleep(.08)
        completed.append(asset['id'])
        return {'id':'e_'+asset['id'],'regions':[],'status':'recognized','image_type':'photo','profile_id':'p'},False
    monkeypatch.setattr(candidates,'recognize',recognize)
    results,_,_,summary=candidates.process_media(prepared,output,{'vision_concurrency':2},None)
    assert completed==['second','first']
    assert [r['asset_id'] for r in results]==['first','second']
    assert summary['recognized_assets']==2


def test_truncated_ocr_preserves_raw_attempt_and_avoids_identical_retry(tmp_path,monkeypatch):
    from PIL import Image
    from vector_store import media
    from vector_store.common import read_json
    monkeypatch.setattr(media,'SHARED',tmp_path)
    path=tmp_path/'source.png';Image.new('RGB',(100,100),'white').save(path)
    asset={'sha256':digest(path.read_bytes()),'width':100,'height':100}
    class Stub:
        calls=0
        def request(self,*args):
            self.calls+=1
            return {'model':'vision','choices':[{'finish_reason':'length',
                'message':{'content':'{"regions":["未完成的真实输出'}}]}
    service=Stub();config={'vision_model':'vision','vision_max_output_tokens':100}
    with pytest.raises(media.OCRPending) as first:
        media.recognize(asset,path,service,config)
    attempt=read_json(tmp_path/first.value.artifact)
    assert attempt['finish_reason']=='length' and attempt['raw_output'].endswith('真实输出')
    assert digest(attempt['raw_output'])==attempt['raw_output_hash']
    with pytest.raises(media.OCRPending) as repeated:
        media.recognize(asset,path,service,config)
    assert repeated.value.artifact==first.value.artifact and service.calls==1
    with pytest.raises(media.OCRPending):
        media.recognize(asset,path,service,{**config,'vision_retry_incomplete':True})
    assert service.calls==2


def test_visual_validation_detects_wrong_derived_range(tmp_path):
    from vector_store.validation import validate_visual_artifacts
    from vector_store.common import write_rows
    prepared=tmp_path/'prepared';prepared.mkdir()
    output=tmp_path/'media';output.mkdir()
    asset={'id':'a','width':100,'height':200}
    occurrence={'id':'o','asset_id':'a','document_id':'d'}
    body=json.dumps({'image_type':'text','regions':[
        {'kind':'paragraph','bbox':[0,0,1000,1000],'text':'正文😀\r\n第二行'}]})
    extraction={'id':'e','asset_id':'a','raw_output':body,'raw_output_hash':digest(body),
        **parse_ocr(body,100,200)}
    parent,children=derive_visual(asset,occurrence,extraction)
    for name,values,folder in [('media_assets',[asset],prepared),('media_sources',[occurrence],prepared),
        ('media_extractions',[extraction],output),('visual_parents',[parent],output),('visual_chunks',children,output)]:
        write_rows(folder/(name+'.jsonl'),values)
    assert validate_visual_artifacts(prepared,output)['passed']
    children[0]['derived_start']+=1
    write_rows(output/'visual_chunks.jsonl',children)
    checked=validate_visual_artifacts(prepared,output)
    assert not checked['passed'] and 'visual chunk range/region mismatch' in checked['errors']


def test_resume_completes_partially_created_payload_indexes():
    from vector_store.storage import Vectors
    from qdrant_client import models
    existing={'planid':SimpleNamespace(data_type=models.PayloadSchemaType.KEYWORD)}
    class Client:
        def collection_exists(self,name):return True
        def get_collection(self,name):
            return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors={
                'dense':SimpleNamespace(size=4096,distance=models.Distance.COSINE)})),payload_schema=existing.copy())
        def create_payload_index(self,name,key,data_type,wait):
            assert key not in existing
            existing[key]=SimpleNamespace(data_type=data_type)
    vectors=Vectors.__new__(Vectors);vectors.client=Client()
    vectors.create('interrupted_collection')
    assert len(existing)==8 and 'document_type' in existing
    vectors.create('interrupted_collection')
    assert len(existing)==8
