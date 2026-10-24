"""只读比较现有两版切块；只计算问题向量，结果写在本脚本目录。

不重切语料、不改变数据库、不建立collection。训练集只读题干送入模型，
答案要点的planid仅在检索完成后计算文档级命中，不能据此声称片段语义正确。
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from collections import Counter, defaultdict

import numpy as np

# 归档后仍以仓库根目录定位输入；不依赖作者本机绝对路径。
REPO = Path(__file__).resolve().parents[3]
SHARED = Path(__file__).resolve().parents[1]/'原向量化与存储工程'
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(SHARED))
from vector_store.common import read_json, rows, file_hash, canonical, secrets, redact
from vector_store.embedding import Counter as TokenCounter, Service
from qdrant_client import QdrantClient, models


def save(name, value):
    """原子保存评测产物，避免中断留下可被误读为完整结果的半份JSON。"""
    path = OUT/name
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    # Windows索引/安全软件可能短时打开刚写的JSON；只对同一份结果作有界重试。
    for attempt in range(6):
        try:
            os.replace(tmp, path)
            break
        except PermissionError:
            if attempt == 5:raise
            time.sleep(.1 * 2**attempt)


def load_inputs():
    questions = []
    for q in rows(REPO/'训练集/train.jsonl'):
        if q['cat'] not in ('A', 'B', 'C', 'D'):
            continue
        gold = sorted({p['planid'] for p in q['points']})
        assert gold and all(p.isdigit() for p in gold)
        questions.append({'qid':q['qid'], 'cat':q['cat'], 'fmt':q['fmt'],
                          'query':q['stem'], 'gold_planids':gold})
    assert len({q['qid'] for q in questions}) == len(questions)
    return questions


def structural_comparison():
    """比较同源区间与实际检索输入；码点范围相同不代表上下文也相同。"""
    result = {}
    maps = {}
    for variant, folder in [('legacy','切块工程'), ('oss','切块工程（开源组件版）')]:
        project = REPO/'rag库'/folder
        run = project/read_json(project/'当前批次.json')['run']
        docs = {d['id']:d for d in rows(run/'documents.jsonl')}
        bydoc = defaultdict(list)
        status = Counter(); reasons = Counter(); lengths = []
        for child in rows(run/'chunks.jsonl'):
            pid = docs[child['document_id']]['planid']
            ranges = tuple((s['start'],s['end']) for s in child['source_segments'])
            bydoc[pid].append((ranges, child['embedding_input_hash']))
            status[child['status']] += 1
            reasons.update(child.get('reasons', [])); lengths.append(child['embedding_tokens'])
        all_ranges = {(pid, r) for pid, values in bydoc.items() for r, _ in values}
        all_inputs = {(pid, h) for pid, values in bydoc.items() for _, h in values}
        result[variant] = {'run':str(run), 'documents':len(docs), 'parents':sum(1 for _ in rows(run/'parents.jsonl')),
            'chunks':len(lengths), 'status':dict(status), 'pending_reasons':dict(reasons),
            'input_tokens':{str(p):float(np.percentile(lengths,p)) for p in (0,25,50,75,90,95,99,100)},
            'over_8192':sum(n>8192 for n in lengths),
            'native_hashes':{name:file_hash(run/(name+'.jsonl')) for name in ('documents','parents','chunks','structures')},
            'document_hashes':{d['planid']:d['source_hash'] for d in docs.values()}}
        maps[variant] = (bydoc, all_ranges, all_inputs)
    assert result['legacy']['document_hashes'] == result['oss']['document_hashes']
    left, right = maps['legacy'], maps['oss']
    result['comparison'] = {'same_source_documents':len(result['legacy']['document_hashes']),
        'documents_with_native_children':len(left[0]),
        'identical_child_ranges':len(left[1] & right[1]),
        'identical_model_inputs_by_document':len(left[2] & right[2]),
        'documents_with_identical_range_sequences':sum(
            [x[0] for x in left[0][p]] == [x[0] for x in right[0][p]] for p in left[0]),
        'documents_with_identical_input_sequences':sum(left[0][p] == right[0][p] for p in left[0])}
    save('structural_comparison.json',result)
    print('Structural comparison:',result['comparison'],flush=True)
    return result


def query_embeddings(questions, private, model):
    """两版共用同一份问题向量；缓存仅属于本评测，不改正式文档缓存。"""
    counter = TokenCounter(REPO/'rag库/切块工程')
    token_lists = [counter.encode(q['query'])[1] for q in questions]
    assert all(0<len(t)<=8192 for t in token_lists)
    specification = {'training_sha256':file_hash(REPO/'训练集/train.jsonl'), 'model':model,
        'instruction':counter.instruction, 'tokenizer_profile':counter.profile,
        'queries':[{'qid':q['qid'],'query_sha256':hashlib.sha256(q['query'].encode()).hexdigest(),
                    'tokens':len(t)} for q,t in zip(questions,token_lists)]}
    path = OUT/'query_vectors.npy'; profile = OUT/'query_profile.json'
    if profile.exists():
        assert read_json(profile)==specification, 'query cache specification changed'
    else:
        save('query_profile.json', specification)
    matrix = np.load(path) if path.exists() else np.full((len(questions),4096),np.nan,dtype=np.float32)
    assert matrix.shape==(len(questions),4096)
    service = Service(private, model, audit=False)
    try:
        missing = [i for i in range(len(questions)) if not np.isfinite(matrix[i]).all()]
        while missing:
            indices=[]; budget=0
            for i in missing:
                if indices and (len(indices)>=32 or budget+len(token_lists[i])>16384):break
                indices.append(i);budget+=len(token_lists[i])
            started=time.monotonic()
            values,usage=service.text_batch([token_lists[i] for i in indices])
            for i,value in zip(indices,values):matrix[i]=value
            tmp=OUT/'query_vectors.tmp.npy';np.save(tmp,matrix);os.replace(tmp,path)
            with (OUT/'embedding_calls.jsonl').open('a',encoding='utf-8') as f:
                f.write(canonical({'qids':[questions[i]['qid'] for i in indices], 'usage':usage,
                                   'seconds':round(time.monotonic()-started,3)})+'\n')
            missing=missing[len(indices):]
            print(f'Question vectors: {len(questions)-len(missing)}/{len(questions)}',flush=True)
    finally:service.close()
    assert np.isfinite(matrix).all() and np.allclose(np.linalg.norm(matrix,axis=1),1,atol=1e-5)
    return matrix


def search(questions, matrix, private, variant, project):
    """只检索原生文本子块，排除相同的图片分支；真实精确Cosine前20名。

    相同查询、模型、模板、top-k和精确检索参数，避免HNSW近似误差冒充切块差异。
    不使用答案planid作为过滤器，不重排，不生成答案。每32题保存一次断点。
    """
    pointer=read_json(project/'当前批次.json')
    output=OUT/(variant+'_retrieval.json')
    completed=read_json(output) if output.exists() else []
    assert [r['qid'] for r in completed]==[q['qid'] for q in questions[:len(completed)]]
    assert all(r['generation_id']==pointer['generation_id'] and r['collection']==pointer['collection'] for r in completed), 'active release changed; create a new evaluation directory'
    client=QdrantClient(url=private['qdrant_url'],api_key=private['qdrant_api_key'],
        prefer_grpc=False,timeout=120,check_compatibility=False,trust_env=False)
    try:
        info=client.get_collection(pointer['collection'])
        assert info.points_count==pointer['statistics']['representations']
        for start in range(len(completed),len(questions),32):
            indices=list(range(start,min(start+32,len(questions))))
            requests=[models.QueryRequest(query=matrix[i].tolist(),using='dense',limit=20,
                filter=models.Filter(must=[models.FieldCondition(key='source_type',match=models.MatchValue(value='text_chunk'))]),
                params=models.SearchParams(exact=True),with_payload=['planid','chunk_id','parent_id','original_parent_id'],
                with_vector=False) for i in indices]
            tick=time.monotonic()
            responses=client.query_batch_points(pointer['collection'],requests,timeout=120)
            assert len(responses)==len(indices)
            for i,response in zip(indices,responses):
                assert len(response.points)==20
                completed.append({'qid':questions[i]['qid'],'generation_id':pointer['generation_id'],
                    'collection':pointer['collection'],'matches':[{'point_id':str(p.id),
                        'score':p.score,**p.payload} for p in response.points]})
            save(output.name,completed)
            print(f'{variant} exact retrieval: {len(completed)}/{len(questions)}, batch {time.monotonic()-tick:.2f}s',flush=True)
    finally:client.close()
    return completed


def metrics(questions, records):
    """参考标签只有文档级；K指文本向量返回条数，不能报告成片段精确率。

    通常一条对应一个子块，超长原生子块可能对应多个完整覆盖分片；不将多个
    分片误称为不同原生子块，也不把Top10结果误称为10篇不同的政策。
    """
    per_query=[]
    for q,r in zip(questions,records):
        assert q['qid']==r['qid']
        gold=set(q['gold_planids']); returned=[p['planid'] for p in r['matches']]
        row={'qid':q['qid'],'cat':q['cat'],'fmt':q['fmt']}
        for k in (1,5,10,20):
            found=gold & set(returned[:k])
            row['hit@'+str(k)]=int(bool(found));row['recall@'+str(k)]=len(found)/len(gold)
        row['mrr@20']=next((1/(i+1) for i,p in enumerate(returned) if p in gold),0)
        per_query.append(row)
    names=[n for n in per_query[0] if '@' in n]
    aggregate=lambda subset:{name:float(np.mean([r[name] for r in subset])) for name in names}
    return {'overall':aggregate(per_query),'by_category':{cat:aggregate([r for r in per_query if r['cat']==cat]) for cat in 'ABCD'},
            'single_question_only':aggregate([r for r in per_query if r['fmt']=='single']), 'per_query':per_query}


def main():
    required=REPO/'rag库/切块工程（开源组件版）/当前批次.json'
    if not required.exists():
        raise RuntimeError('开源组件版已按用户要求移除。此目录保留历史评估证据；重新评估需先恢复并构建被移除版本，不会自动新建工程或集合。')
    questions=load_inputs(); structural=structural_comparison();private=secrets()
    for q in questions:
        assert set(q['gold_planids']) <= set(structural['legacy']['document_hashes'])
    save('questions.json',questions)
    model=read_json(SHARED/'config.json')['model']
    vectors=query_embeddings(questions,private,model)
    all_results={}
    for variant, project in [('legacy',SHARED),('oss',REPO/'rag库/向量化与存储工程（开源组件版）')]:
        all_results[variant]=metrics(questions,search(questions,vectors,private,variant,project))
    comparisons={}
    rng=np.random.default_rng(20261023)
    for name in all_results['legacy']['overall']:
        a=np.array([r[name] for r in all_results['legacy']['per_query']]);b=np.array([r[name] for r in all_results['oss']['per_query']]);d=b-a
        draws=rng.integers(0,len(d),size=(5000,len(d)))
        bootstrap=d[draws].mean(axis=1)
        comparisons[name]={'oss_minus_legacy':float(d.mean()),'paired_bootstrap_95_ci':np.percentile(bootstrap,[2.5,97.5]).tolist(),
                           'oss_better_questions':int((d>0).sum()),'legacy_better_questions':int((d<0).sum()),'tied_questions':int((d==0).sum())}
    result={'n':len(questions),'categories':dict(Counter(q['cat'] for q in questions)),
        'single_questions':sum(q['fmt']=='single' for q in questions), 'multi_questions':sum(q['fmt']=='multi' for q in questions),
        'method':'existing native text representations; same query vectors; exact cosine top20 vector results (overlimit source chunk may have multiple fragments); no answer input, gold filtering, BM25, reranker, or answer generation',
        'multi_turn_handling':'use stem verbatim once; does not evaluate dialogue memory or every turn',
        'ground_truth':'reference planid labels; document-level only; training development set, not held-out test',
        'results':all_results,'paired_differences':comparisons}
    for name in ('legacy','oss'):
        run=Path(structural[name]['run'])
        assert all(file_hash(run/(table+'.jsonl'))==h for table,h in structural[name]['native_hashes'].items())
    result['native_input_files_unchanged']=True
    save('evaluation.json',result)
    print(json.dumps({k:v['overall'] for k,v in all_results.items()},ensure_ascii=False),flush=True)
    print('Paired differences:',json.dumps(comparisons,ensure_ascii=False),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        # 源码归档不携带历史凭证；缺少已移除版本时仍应返回原有明确提示。
        private=secrets() if (SHARED/'secrets.local.json').is_file() else {}
        print(type(error).__name__+': '+redact(str(error),private),flush=True)
        raise SystemExit(1)
