"""本地版：迁入缓存/真实向量化→SQLite→Qdrant Local→BM25S→全量校验→发布。

模型输入规则复用原工程；存储只通过适配接口调用。相同输入复用向量，相同
批次恢复只补缺失point。SQLite指针最后更新，中断不会把半成品变成正式发布。
"""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import shutil
import uuid
import numpy as np
from .common import SHARED,paths,read_json,rows,write_json,write_rows,digest,identity,file_hash,utc_now,long_path,secrets
from .embedding import Cache,Counter,Service,embedding_profile
from .candidates import payload,process_media,make_candidates,messages_from_description
from .text_inputs import visual_retrieval_text
from .storage import open_stores
from .importer import import_sources
from .validation import validate_prepared,validate_release


def inherit_file(source,target):
    """只继承不可变文件；同盘优先硬链接，跨盘复制。禁止链接可写数据库。

    硬链接是两个独立文件名引用相同字节：删除任一目录不会让另一文件名失效。
    缓存更新采用原子替换，不能原地改写共享字节。既有目标不一致则拒绝覆盖。
    """
    source=long_path(source);target=long_path(target);target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if os.path.samefile(source,target):return 'existing_hardlink'
        if file_hash(source)!=file_hash(target):raise ValueError('inherited immutable file changed')
        # 受限环境可能先复制成功而禁止link；允许link后原子替换新目录中的副本，
        # 仅减少重复字节，不覆盖来源。链接失败则保留已校验的普通副本。
        temporary=target.with_name(target.name+'.link-'+uuid.uuid4().hex)
        try:
            os.link(source,temporary);os.replace(temporary,target);return 'relinked'
        except OSError:return 'existing_copy'
        finally:temporary.unlink(missing_ok=True)
    try:os.link(source,target);return 'hardlink'
    except OSError:
        temporary=target.with_name(target.name+'.copy-'+uuid.uuid4().hex)
        try:shutil.copyfile(source,temporary);os.replace(temporary,target);return 'copy'
        finally:temporary.unlink(missing_ok=True)


def inherit_tree(source,target):
    counts=defaultdict(int)
    for file in long_path(source).rglob('*'):
        if file.is_file():counts[inherit_file(file,target/file.relative_to(long_path(source)))]+=1
    return dict(counts)


def ensure_tokenizer(project,upstream):
    """独立继承已固定的处理器文件；首次build也可直接执行，不依赖先迁移数据库。"""
    inherit_file(upstream/'model_profile.json',project/'model_profile.json')
    inherit_tree(upstream/'.cache/qwen-tokenizer',project/'.cache/qwen-tokenizer')


def retrieval_text(record,source,doc):
    if record['representation_type']!='text':return None
    text=source['retrieval_text'] if record['source_type']=='text_chunk' else visual_retrieval_text(doc,source)
    part=record.get('embedding_fragment')
    return text[part['start']:part['end']] if part else text


def ingest(db,vectors,lexical,manifest,output,cache,candidates,service=None,counter=None,fault_after=None):
    """先有来源再写表示；只补向量缺口，唯一输入成功写入后登记ready任务。

    candidates可为迁移生成器（无description），或新向量化候选。缓存缺失且没有
    服务时直接失败，绝不填充随机向量，也不借服务端数据库掩盖本地存储缺口。
    """
    generation=manifest['generation_id'];collection=manifest['collection']
    db.begin(generation,manifest);docs=import_sources(db,Path(manifest['prepared_path']),Path(manifest['media_path']))
    grouped=defaultdict(list)
    for c in candidates:
        r=c['record'];p=payload(r,docs[r['document_id']],c['source'],generation)
        db.put_record(generation,r,p,retrieval_text(r,c['source'],docs[r['document_id']]),c.get('description'))
        grouped[r['input_hash']].append((r,p,c.get('description')))
    db.commit();vectors.create(collection);present=vectors.ids(collection)
    stats={'representations':sum(len(v) for v in grouped.values()),'unique_inputs':len(grouped),
        'cache_hits':0,'new_input_vectors':0,'model_requests':0,'point_upserts':0,'points_reused':0}
    batch=[];completed=0;pending_text=[];pending_tokens=0
    def flush():
        nonlocal batch,completed
        if not batch:return
        vectors.upsert(collection,[(r,v,p) for key,r,v,p in batch])
        for key,_,_,_ in batch:db.task(generation,key,'ready')
        db.commit();stats['point_upserts']+=len(batch);batch=[]
    def flush_text():
        nonlocal pending_text,pending_tokens
        if not pending_text:return
        jobs=pending_text;pending_text=[];pending_tokens=0
        values,usage=service.text_batch([d['token_ids'] for key,members,d in jobs]);stats['model_requests']+=1
        for (key,members,d),value in zip(jobs,values):
            value,meta=cache.put(key,value,usage);stats['new_input_vectors']+=1
            batch.extend((key,r,value,p) for r,p,_ in members)
        if len(batch)>=128:flush()
    for key,members in grouped.items():
        pending=[(r,p,d) for r,p,d in members if r['point_id'] not in present]
        if not pending:
            db.task(generation,key,'ready');stats['points_reused']+=len(members);completed+=1;continue
        entry=cache.get(key)
        if entry is not None:value,meta=entry;stats['cache_hits']+=1
        else:
            if service is None:raise ValueError('migration cache missing: '+key)
            d=members[0][2]
            if d is None:raise ValueError('missing actual embedding input description')
            db.task(generation,key,'running');db.commit()
            if d['type']=='text':
                if pending_text and (len(pending_text)>=32 or pending_tokens+d['expected_tokens']>16384):flush_text()
                pending_text.append((key,pending,d));pending_tokens+=d['expected_tokens'];completed+=1
                if fault_after and completed>=fault_after:flush_text();flush();raise InterruptedError('requested local checkpoint interruption')
                continue
            try:
                flush_text();value,usage=service.vision_embedding(messages_from_description(d,counter),d['expected_tokens'])
                value,meta=cache.put(key,value,usage);stats['new_input_vectors']+=1;stats['model_requests']+=1
            except Exception as error:
                db.task(generation,key,'failed',type(error).__name__);db.commit();raise
        batch.extend((key,r,value,p) for r,p,d in pending)
        if len(batch)>=128:flush()
        completed+=1
        if completed%2000==0:
            write_json(output/'progress.json',{**stats,'completed_inputs':completed,'at':utc_now()})
            print(f'local input {completed}/{len(grouped)}; new model inputs {stats["new_input_vectors"]}',flush=True)
        if fault_after and completed>=fault_after:
            flush();raise InterruptedError('requested local checkpoint interruption')
    flush_text();flush();db.commit()
    documents=[(row['record']['id'],row['text']) for row in db.members(generation) if row['text'] is not None]
    stats['lexical']=lexical.build(generation,documents)
    write_json(output/'embedding_summary.json',stats)
    return stats


def finish(db,vectors,lexical,manifest,output,cache,expected_revision):
    check=validate_release(db,vectors,lexical,manifest,output,cache)
    if not check['passed']:raise ValueError('local release validation failed; active pointer unchanged')
    db.ready(manifest['generation_id'],check);active=db.activate(manifest['generation_id'],expected_revision)
    project=output.parents[1]
    pointer={'generation_id':manifest['generation_id'],'run':str(output.relative_to(project)),
        'collection':manifest['collection'],'profile_id':manifest['profile_id'],'revision':active['revision'],
        'backend':'sqlite+qdrant_local+bm25s','status':'active','partial':manifest.get('partial',True),'activated_at':utc_now()}
    write_json(project/'当前批次.json',pointer)
    return check


def migrate(project,source_project=None,fault_after=None):
    """只读旧版冻结文件和向量缓存，完整迁入本地三存储；不连接旧数据库或模型。"""
    config,_,_=paths(project);source_project=Path(source_project or project/config['migration_source']).resolve()
    pointer=read_json(source_project/'当前批次.json');old_output=source_project/pointer['run']
    old=read_json(old_output/'manifest.json')
    if not read_json(old_output/'validation.json')['passed']:raise ValueError('source release was not validated')
    prepared=project/'outputs'/old['prepared_id'];media=project/'outputs'/Path(old['media_path']).name
    inherited={'prepared':inherit_tree(Path(old['prepared_path']),prepared),'media':inherit_tree(Path(old['media_path']),media)}
    for name in ('ocr','ocr-failures','ocr-attempts','derived_images','service-canaries'):
        if (source_project/'.cache'/name).exists():inherited[name]=inherit_tree(source_project/'.cache'/name,project/'.cache'/name)
    profile=old['profile_id'];source_cache=source_project/'.cache/vectors'/profile
    _,upstream,_=paths(project)
    ensure_tokenizer(project,upstream)
    records=list(rows(old_output/'representations.jsonl'))
    def inherit_input(key):
        return [inherit_file(source_cache/(key+suffix),project/'.cache/vectors'/profile/(key+suffix)) for suffix in ('.json','.f32')]
    # 数万小文件的防病毒/权限检查是I/O瓶颈；仅不可变文件登记用有限线程。
    # 每个key独占目标路径，SQLite/Qdrant写入与正式发布仍由主线程串行执行。
    cache_modes=defaultdict(int)
    with ThreadPoolExecutor(max_workers=8) as executor:
        for index,modes in enumerate(executor.map(inherit_input,sorted({r['input_hash'] for r in records})),1):
            for mode in modes:cache_modes[mode]+=1
            if index%5000==0:print(f'inherited vector input files: {index}',flush=True)
    inherited['vector_cache']=dict(cache_modes)
    generation=identity('local_generation',old['generation_id'],config['storage'],'local-contract-1')
    manifest={'generation_id':generation,'collection':'guance_local_'+digest(generation)[:20],
        'prepared_id':old['prepared_id'],'prepared_path':str(prepared),'media_path':str(media),
        'profile_id':profile,'profile':old['profile'],'source_generation_id':old['generation_id'],
        'source_manifest_hash':file_hash(old_output/'manifest.json'),'partial':pointer.get('partial',True),
        'migration_mode':'immutable files and checked caches only; no database/model connection'}
    output=project/'outputs'/generation;output.mkdir(parents=True,exist_ok=True)
    for name in ('representations.jsonl','representation_excluded.jsonl','lexical_members.jsonl','media_summary.json','exact_groups.jsonl','near_pairs.jsonl','dedup_summary.json'):
        if (old_output/name).exists():inherit_file(old_output/name,output/name)
    write_json(output/'manifest.json',manifest);write_json(output/'migration_inheritance.json',inherited)
    source_map={r['id']:r for folder,name in [(prepared,'chunks'),(prepared,'media_sources'),(media,'visual_chunks')] for r in rows(folder/(name+'.jsonl'))}
    db,vectors,lexical=open_stores(project,config)
    try:
        active=db.active();revision=active['revision'] if active else 0
        cache=Cache(profile,project/'.cache/vectors'/profile)
        stats=ingest(db,vectors,lexical,manifest,output,cache,
            ({'record':r,'source':source_map[r['source_id']]} for r in records),fault_after=fault_after)
        check=finish(db,vectors,lexical,manifest,output,cache,revision)
        return {'output':str(output),'statistics':stats,'validation':check}
    finally:lexical.close();vectors.close();db.close()


def build(project,sample=False,allow_partial=False,fault_after=None):
    """新输入仍能真实OCR及向量化；完整计数/原文映射复用原版，不加载模型权重。"""
    from .prepare import prepare
    from .media import has_media_gaps
    from .dedup import prepare_dedup
    config,upstream,_=paths(project);prepared,pm=prepare(project,sample)
    if not validate_prepared(prepared,pm)['passed']:raise ValueError('prepared source validation failed')
    ensure_tokenizer(project,upstream)
    dedup,dedup_summary=prepare_dedup(prepared,config['dedup'])
    private=secrets();counter=Counter(project);service=Service(private,config['model'])
    db=vectors=lexical=None
    try:
        advertised=service.request('/models')
        config={**config,'vision_service_url':private['embedding_base_url'],
            'vision_model_advertisement':next(m for m in advertised['data'] if m['id']==config['vision_model'])}
        profile,profile_data=embedding_profile(config,counter,advertised,private['embedding_base_url'])
        verify_service_space(service,counter,profile)
        work=project/'.cache/media-work'/digest({'prepared':pm['prepared_id'],'vision':config['vision_model']})[:24];work.mkdir(parents=True,exist_ok=True)
        extractions,vp,vc,ms=process_media(prepared,work,config,service)
        hashes={n:file_hash(work/n) for n in ('media_extractions.jsonl','visual_parents.jsonl','visual_chunks.jsonl','media_failures.jsonl','media_excluded.jsonl')}
        media=project/'outputs'/('media_'+digest(hashes)[:24]);inherit_tree(work,media)
        generation=identity('local_generation',pm['prepared_id'],hashes,profile,config['storage'],'local-contract-1')
        output=project/'outputs'/generation;output.mkdir(exist_ok=True)
        for name in ('exact_groups.jsonl','near_pairs.jsonl'):inherit_file(dedup/name,output/name)
        write_json(output/'dedup_summary.json',dedup_summary)
        docs={d['id']:d for d in rows(prepared/'documents.jsonl')}
        candidates,excluded=make_candidates(prepared,output,config,counter,profile,docs,vc,extractions)
        partial=has_media_gaps(ms,excluded)
        if partial and not allow_partial:raise ValueError('media gaps recorded; inspect and pass --allow-partial')
        manifest={'generation_id':generation,'collection':'guance_local_'+digest(generation)[:20],
            'prepared_id':pm['prepared_id'],'prepared_path':str(prepared),'media_path':str(media),
            'profile_id':profile,'profile':profile_data,'partial':partial,'migration_mode':'new input build'}
        write_json(output/'manifest.json',manifest);write_json(output/'media_summary.json',ms)
        db,vectors,lexical=open_stores(project,config);active=db.active();revision=active['revision'] if active else 0
        cache=Cache(profile,project/'.cache/vectors'/profile)
        stats=ingest(db,vectors,lexical,manifest,output,cache,candidates,service,counter,fault_after)
        check=finish(db,vectors,lexical,manifest,output,cache,revision)
        return {'output':str(output),'statistics':stats,'validation':check}
    finally:
        if lexical is not None:lexical.close()
        if vectors is not None:vectors.close()
        if db is not None:db.close()
        service.close()


def verify_service_space(service,counter,profile_id):
    """保留原版固定探针：服务未公开权重修订时，防止明显换空间后复用旧缓存。"""
    _,tokens=counter.encode('政策检索向量空间校验。');vectors,usage=service.text_batch([tokens])
    path=SHARED/'.cache/service-canaries'/(profile_id+'.json')
    if path.exists():
        old=read_json(path)
        if float(np.dot(np.asarray(old['vector'],dtype=np.float32),vectors[0]))<.99999:
            raise ValueError('service vector space changed; new profile required')
    else:write_json(path,{'profile_id':profile_id,'vector':vectors[0].tolist(),'token_count':len(tokens),'usage':usage})
    return {'token_count':len(tokens),'usage':usage,'server_revision_verified':False}
