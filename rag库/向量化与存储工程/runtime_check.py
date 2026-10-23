"""只读检查两版生效指针、成员计数和Qdrant索引健康；不调用模型或改写数据。

本检查补充全量逐点校验：数量相等不能替代verify；green/ok用于检查后台优化器。
少量低于索引阈值的向量可使用精确扫描，因此不要求indexed_vectors等于points。
"""
import json
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from vector_store.common import SHARED,secrets,paths,read_json,write_json,utc_now,redact
from vector_store.storage import Vectors


def check():
    private=secrets();vectors=Vectors(private);results={}
    try:
        for project in (SHARED,SHARED.with_name('向量化与存储工程（开源组件版）')):
            config,_,_=paths(project);pointer=read_json(project/'当前批次.json')
            with psycopg.connect(private['postgres_dsn'],row_factory=dict_row) as connection:
                connection.execute(sql.SQL('SET search_path TO {},public').format(sql.Identifier(config['postgres_schema'])))
                runtime=connection.execute('SELECT * FROM rag_runtime WHERE knowledge_base_id=%s',(config['knowledge_base_id'],)).fetchone()
                release=connection.execute('SELECT status,collection FROM index_releases WHERE generation_id=%s',(pointer['generation_id'],)).fetchone()
                count=connection.execute('SELECT count(*) AS n FROM release_representations WHERE generation_id=%s',(pointer['generation_id'],)).fetchone()['n']
                tasks=connection.execute("SELECT count(*) AS n FROM index_tasks WHERE generation_id=%s AND status!='ready'",(pointer['generation_id'],)).fetchone()['n']
            info=vectors.client.get_collection(pointer['collection']);state=info.model_dump(mode='json')
            if not(runtime['active_generation_id']==pointer['generation_id'] and runtime['revision']==pointer['revision']
                and release['status']=='ready' and release['collection']==pointer['collection']
                and count==pointer['statistics']['representations']==info.points_count and tasks==0
                and state['status']=='green' and state['optimizer_status']=='ok' and len(info.payload_schema)==8):
                raise ValueError('runtime unhealthy: '+config['variant']+' '+json.dumps({k:state[k] for k in ('status','optimizer_status','points_count')},ensure_ascii=False))
            results[config['variant']]={'generation_id':pointer['generation_id'],'revision':runtime['revision'],
                'postgres_points':count,'qdrant_points':info.points_count,'collection_status':state['status'],
                'optimizer_status':state['optimizer_status'],'indexed_vectors':info.indexed_vectors_count,
                'keyword_indexes':list(info.payload_schema),'unfinished_tasks':tasks,'passed':True}
        receipt={'at':utc_now(),'releases':results,'passed':True}
        write_json(SHARED/'.cache/current-runtime.json',receipt)
        return receipt
    finally:vectors.close()


if __name__=='__main__':
    try:print(json.dumps(check(),ensure_ascii=False,indent=2))
    except Exception as error:
        print(type(error).__name__+': '+redact(str(error),secrets()))
        raise SystemExit(1)
