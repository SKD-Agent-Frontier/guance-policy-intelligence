"""真实本地适配器回归；小型确定性向量只用于测试，不是生产模型结果。"""
import sqlite3
from pathlib import Path
import sys
import numpy as np
import pytest

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from vector_store.common import write_json,write_rows,read_json,digest,file_hash,atomic_bytes
from vector_store.embedding import Cache
from vector_store.candidates import representation
from vector_store.sqlite_metadata import SQLiteMetadata
from vector_store.bm25s_index import BM25SLexical
from vector_store.qdrant_local import QdrantLocalVectors
from vector_store.storage import open_stores
from vector_store.pipeline import ingest,finish,inherit_file
from vector_store.operations import verify,query
from vector_store.backup import backup,restore


def test_sqlite_immutability_foreign_keys_and_activation(tmp_path):
    db=SQLiteMetadata(tmp_path/'meta.sqlite3')
    try:
        doc={'id':'d','planid':'1','source_hash':'hash'};db.put_documents([doc])
        with pytest.raises(ValueError,match='identity conflict'):
            db.put_documents([{**doc,'source_hash':'changed'}])
        assert db.document('d')==doc
        db.put_objects('chunk',[{'id':'c','document_id':'d'}])
        assert db.object_owners()['c']=='d'
        with pytest.raises(sqlite3.IntegrityError):db.register_links([{'id':'c','parent_id':'missing'}],[])
        db.begin('g',{'generation_id':'g'})
        with pytest.raises(ValueError,match='not ready'):db.activate('g',0)
        db.ready('g',{'passed':True})
        assert db.activate('g',0)=={'generation':'g','revision':1}
        assert db.activate('g',1)['revision']==1
        with pytest.raises(ValueError,match='revision conflict'):db.activate('g',0)
        assert db.integrity()
        db.connection.execute("UPDATE objects SET data=? WHERE id='c'",('{"id":"c","document_id":"d","unexpected":true}',))
        db.commit();assert not db.integrity()
    finally:db.close()


def test_bm25_pre_topk_filter_reopen_and_tamper(tmp_path):
    index=BM25SLexical(tmp_path/'bm25')
    docs=[('a','城市燃气安全管理'),('b','农业发展'),('c','燃气安全 燃气安全 燃气安全')]
    index.build('g',docs)
    assert index.validate('g',docs)['passed']
    with pytest.raises(ValueError,match='fingerprint mismatch'):index.validate('g',[('a','被改动的检索文本'),*docs[1:]])
    assert [r['id'] for r in index.search('燃气安全',1,{'a'})]==['a']
    assert index.search('zzunknownqq',3)==[]
    assert index.search('燃气',3,set())==[]
    index.close();index=BM25SLexical(tmp_path/'bm25');index.load('g')
    assert index.search('农业',3)[0]['id']=='b'
    index.close()
    path=tmp_path/'bm25/g/params.index.json';path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError,match='file changed'):index.load('g')


def test_immutable_hardlink_survives_atomic_source_replacement(tmp_path):
    source=tmp_path/'original';clone=tmp_path/'clone'
    atomic_bytes(source,b'old')
    assert inherit_file(source,clone) in ('hardlink','copy')
    atomic_bytes(source,b'new')
    assert clone.read_bytes()==b'old' and source.read_bytes()==b'new'


def tiny_release(project):
    """构造有原文、连续范围、父子关系、两种独立输入的最小完整冻结批次。"""
    prepared=project/'outputs/prepared_test';media=project/'outputs/media_test';output=project/'outputs/local_generation_test'
    for folder in (prepared,media,output):folder.mkdir(parents=True)
    config={'storage':{'metadata':'sqlite','vectors':'qdrant_local','lexical':'bm25s','directory':'.runtime/local'},'rrf_k':60}
    write_json(project/'config.json',config)
    text='城市燃气安全。\r\n农业发展😀。\r\n';raw=text.encode('utf-8');cut=text.index('农业')
    atomic_bytes(prepared/'snapshots/source.md',raw)
    doc={'id':'d','planid':'1','source_hash':digest(raw),'snapshot':'snapshots/source.md','metadata':{'title':'城市发展'}}
    segment=lambda a,b:{'start':a,'end':b,'text':text[a:b]}
    parent={'id':'p','document_id':'d','start':0,'end':len(text),'source_segments':[segment(0,len(text))]}
    chunks=[{'id':cid,'document_id':'d','parent_id':'p','start':a,'end':b,
        'source_segments':[segment(a,b)],'context_segments':[],'retrieval_text':text[a:b],
        'status':'ready','reasons':[]} for cid,a,b in [('c1',0,cut),('c2',cut,len(text))]]
    tables={'documents':[doc],'units':[{'id':'u','document_id':'d','start':0,'end':len(text),'text':text}],
        'parents':[parent],'chunks':chunks,'structures':[],'relations':[],'media_assets':[],'media_sources':[],'parent_contexts':[]}
    for name,values in tables.items():write_rows(prepared/(name+'.jsonl'),values)
    write_json(prepared/'manifest.json',{'prepared_id':'prepared_test',
        'frozen_files':{name+'.jsonl':file_hash(prepared/(name+'.jsonl')) for name in tables},
        'specification':{'config':{'coverage_policy':'all_existing_content','max_input_tokens':8192}}})
    for name in ('media_extractions','visual_parents','visual_chunks','media_failures','media_excluded'):write_rows(media/(name+'.jsonl'),[])
    manifest={'generation_id':'local_generation_test','collection':'test','prepared_path':str(prepared),
        'media_path':str(media),'profile_id':'profile_test','partial':False}
    candidates=[];cache=Cache('profile_test',project/'.cache/vectors/profile_test')
    for i,chunk in enumerate(chunks):
        description={'type':'text','token_ids':[i+1],'expected_tokens':1}
        record=representation(doc,chunk,'text_chunk','text',description,'profile_test')
        value=np.zeros(4096,dtype=np.float32);value[i]=1;cache.put(record['input_hash'],value,{})
        candidates.append({'record':record,'source':chunk,'description':description})
    write_rows(output/'representations.jsonl',[c['record'] for c in candidates]);write_json(output/'manifest.json',manifest)
    return config,manifest,output,cache,candidates


def test_checkpoint_resume_real_local_indexes_and_closed_restore(tmp_path,monkeypatch):
    project=tmp_path/'project';config,manifest,output,cache,candidates=tiny_release(project)
    db,vectors,lexical=open_stores(project,config)
    try:
        with pytest.raises(InterruptedError):ingest(db,vectors,lexical,manifest,output,cache,candidates,fault_after=1)
        assert vectors.count('test')==1 and db.active() is None
        with sqlite3.connect(project/'.runtime/local/qdrant/collection/test/storage.sqlite') as check:
            assert check.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
        # SDK自身的单路径锁必须有效，不能靠删除锁文件绕开。
        second=QdrantLocalVectors(project/'.runtime/local/qdrant')
        with pytest.raises(RuntimeError,match='already accessed'):second.create('test')
        stats=ingest(db,vectors,lexical,manifest,output,cache,candidates)
        assert stats['points_reused']==1 and stats['point_upserts']==1 and stats['model_requests']==0
        assert finish(db,vectors,lexical,manifest,output,cache,0)['passed']
    finally:lexical.close();vectors.close();db.close()
    assert verify(project)['vectors_checked']==2
    # BM25单路查询完全不打开Qdrant，也不需要服务凭证。
    with monkeypatch.context() as patch:
        import vector_store.qdrant_local as local
        patch.setattr(local,'QdrantClient',lambda **kwargs:pytest.fail('BM25 opened dense store'))
        result=query(project,'燃气',mode='bm25',filters={'planid':'1'})
        assert result['results'][0]['source']['id']=='c1'
        assert result['results'][0]['parent']['id']=='p'
    receipt=backup(project,tmp_path/'backup.zip')
    restored=tmp_path/'restored';restore(receipt['path'],restored)
    check=verify(restored)
    assert check['passed'] and check['vectors_checked']==2
    assert read_json(restored/'outputs/local_generation_test/manifest.json')['prepared_path']==str(restored/'outputs/prepared_test')
