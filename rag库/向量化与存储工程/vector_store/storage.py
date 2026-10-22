"""PostgreSQL权威元数据与Qdrant物理collection；发布只在PG事务内切指针。

不尝试跨数据库伪造原子事务。先构建独立collection，核对完整point身份、payload、
向量和来源，成功后才把release置ready；失败、中断或版本竞争不能切换生效批次。
"""
from contextlib import contextmanager
import re
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from psycopg.rows import dict_row
from qdrant_client import QdrantClient,models
from .common import SHARED,digest,canonical

KEYS={'document_versions':('document_id',),'source_objects':('object_id',),'parents':('parent_id',),
      'chunks':('chunk_id',),'relations':('relation_id',),'media_assets':('asset_id',),
      'media_occurrences':('occurrence_id',),'visual_extractions':('extraction_id',),
      'visual_regions':('region_id',),'visual_parents':('parent_id',),'visual_chunks':('chunk_id',),'embedding_profiles':('profile_id',),
      'embedding_inputs':('input_hash',),'embedding_records':('representation_id',),
      'embedding_cache':('profile_id','input_hash')}


class Database:
    def __init__(self,private,schema):
        if not re.fullmatch(r'[a-z][a-z0-9_]*',schema):
            raise ValueError('invalid database schema')
        self.connection=psycopg.connect(private['postgres_dsn'],row_factory=dict_row)
        self.connection.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(schema)))
        self.connection.execute(sql.SQL('SET search_path TO {},public').format(sql.Identifier(schema)))
        self.connection.execute((SHARED/'schema.sql').read_text(encoding='utf-8'))
        self.connection.commit()
        self.schema=schema

    def close(self):
        self.connection.close()

    @contextmanager
    def worker_lock(self,generation_id):
        # 会话锁覆盖外部模型调用，保证单worker。崩溃后PG自动释放，不需要猜测旧租约。
        key=self.schema+':'+generation_id
        acquired=self.connection.execute('SELECT pg_try_advisory_lock(hashtext(%s)) AS ok',(key,)).fetchone()['ok']
        self.connection.commit()
        if not acquired:
            raise RuntimeError('another worker owns this release')
        try:
            yield
        finally:
            self.connection.rollback()
            self.connection.execute('SELECT pg_advisory_unlock(hashtext(%s))',(key,))
            self.connection.commit()

    def immutable(self,table,records):
        """同ID只能重写相同data_hash；冲突不覆盖。批次执行有明确列，不拼用户值。"""
        if table not in KEYS:
            raise ValueError('unregistered immutable table')
        records=list(records)
        if not records:
            return
        fields=list(records[0])
        if any(list(r)!=fields for r in records) or 'data_hash' not in fields:
            raise ValueError('inconsistent immutable row columns')
        columns=sql.SQL(',').join(map(sql.Identifier,fields))
        placeholders=sql.SQL(',').join(sql.Placeholder() for _ in fields)
        conflict=sql.SQL(',').join(map(sql.Identifier,KEYS[table]))
        query=sql.SQL('INSERT INTO {t} ({fields}) VALUES ({values}) ON CONFLICT ({keys}) DO UPDATE SET data_hash=EXCLUDED.data_hash WHERE {t}.data_hash=EXCLUDED.data_hash').format(t=sql.Identifier(table),fields=columns,values=placeholders,keys=conflict)
        values=[tuple(Jsonb(r[k]) if isinstance(r[k],(dict,list)) else r[k] for k in fields) for r in records]
        with self.connection.transaction():
            with self.connection.cursor() as cursor:
                cursor.executemany(query,values)
                if cursor.rowcount!=len(records):
                    raise ValueError('immutable identity conflict in '+table)

    def begin_release(self,generation,profile_id,collection,manifest):
        with self.connection.transaction():
            self.connection.execute('INSERT INTO index_releases(generation_id,prepared_id,collection,profile_id,status,manifest) VALUES(%s,%s,%s,%s,\'building\',%s) ON CONFLICT DO NOTHING',
                (generation,manifest['prepared_id'],collection,profile_id,Jsonb(manifest)))
            old=self.connection.execute('SELECT * FROM index_releases WHERE generation_id=%s FOR UPDATE',(generation,)).fetchone()
            if old['manifest']!=manifest or old['profile_id']!=profile_id or old['collection']!=collection:
                raise ValueError('release identity conflict')
            if old['status']=='failed':
                self.connection.execute("UPDATE index_releases SET status='building' WHERE generation_id=%s",(generation,))
            return old['status']

    def task(self,generation,key,status,error=None):
        with self.connection.transaction():
            release=self.connection.execute('SELECT status FROM index_releases WHERE generation_id=%s FOR UPDATE',(generation,)).fetchone()
            if release['status']!='building':
                raise ValueError('cannot write tasks into a non-building release')
            self.connection.execute('INSERT INTO index_tasks(generation_id,task_key,status,attempts,error) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(generation_id,task_key) DO UPDATE SET status=EXCLUDED.status, attempts=index_tasks.attempts+EXCLUDED.attempts,error=EXCLUDED.error,updated_at=now()',
                (generation,key,status,1 if status=='running' else 0,error))

    def register_representation(self,generation,record,input_description,payload):
        self.immutable('embedding_inputs',[{'input_hash':record['input_hash'],'data':input_description,'data_hash':digest(input_description)}])
        self.immutable('embedding_records',[{'representation_id':record['id'],'document_id':record['document_id'],
            'source_id':record['source_id'],'source_type':record['source_type'],'representation_type':record['representation_type'],
            'profile_id':record['profile_id'],'input_hash':record['input_hash'],'point_id':record['point_id'],
            'data':record,'data_hash':digest(record)}])
        with self.connection.transaction():
            self.connection.execute('INSERT INTO release_representations(generation_id,representation_id,point_id,payload,payload_hash) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                (generation,record['id'],record['point_id'],Jsonb(payload),digest(payload)))
            existing=self.connection.execute('SELECT payload_hash,point_id FROM release_representations WHERE generation_id=%s AND representation_id=%s',(generation,record['id'])).fetchone()
            if existing['payload_hash']!=digest(payload) or str(existing['point_id'])!=record['point_id']:
                raise ValueError('release representation mapping conflict')

    def activate(self,kbid,generation,expected_revision,reason):
        """只切换已验证ready批次；revision防止旧构建覆盖另一个已发布选择。"""
        with self.connection.transaction():
            release=self.connection.execute('SELECT status FROM index_releases WHERE generation_id=%s FOR SHARE',(generation,)).fetchone()
            if not release or release['status']!='ready':
                raise ValueError('only validated ready releases can be activated')
            self.connection.execute('INSERT INTO rag_runtime(knowledge_base_id) VALUES(%s) ON CONFLICT DO NOTHING',(kbid,))
            current=self.connection.execute('SELECT * FROM rag_runtime WHERE knowledge_base_id=%s FOR UPDATE',(kbid,)).fetchone()
            if current['revision']!=expected_revision:
                raise ValueError('runtime revision conflict')
            self.connection.execute('UPDATE rag_runtime SET active_generation_id=%s,revision=revision+1,updated_at=now() WHERE knowledge_base_id=%s',(generation,kbid))
            self.connection.execute('INSERT INTO activation_history(knowledge_base_id,generation_id,previous_generation_id,reason) VALUES(%s,%s,%s,%s)',(kbid,generation,current['active_generation_id'],reason))
            return expected_revision+1


class Vectors:
    def __init__(self,private):
        # 服务版本和客户端版本均锁定并报告；实际协议由集成测试验证，不假定版本相等。
        self.client=QdrantClient(url=private['qdrant_url'],api_key=private['qdrant_api_key'],
            prefer_grpc=False,timeout=60,check_compatibility=False)

    def close(self):
        self.client.close()

    def create(self,collection):
        if not self.client.collection_exists(collection):
            self.client.create_collection(collection,vectors_config={'dense':models.VectorParams(size=4096,distance=models.Distance.COSINE,on_disk=True)},
                on_disk_payload=True,hnsw_config=models.HnswConfigDiff(on_disk=True))
        info=self.client.get_collection(collection)
        vector=info.config.params.vectors['dense']
        if vector.size!=4096 or vector.distance!=models.Distance.COSINE:
            raise ValueError('existing collection has incompatible vector space')
        # 建collection与8个索引不是原子操作。中断后不能因collection已存在就跳过
        # 剩余索引；逐项补齐，并拒绝同名字段的类型冲突，不把失败批次误作已就绪。
        for key in ('planid','document_version_id','source_type','representation_type','area_code','area_name','period_key','document_type'):
            existing=info.payload_schema.get(key)
            if existing is not None:
                if existing.data_type!=models.PayloadSchemaType.KEYWORD:
                    raise ValueError('existing payload index has incompatible type: '+key)
            else:
                self.client.create_payload_index(collection,key,models.PayloadSchemaType.KEYWORD,wait=True)

    def upsert(self,collection,records):
        points=[models.PointStruct(id=record['point_id'],vector={'dense':vector.tolist()},payload=payload)
            for record,vector,payload in records]
        if points:
            self.client.upsert(collection,points=points,wait=True)

    def scan(self,collection):
        offset=None
        while True:
            points,offset=self.client.scroll(collection,offset=offset,limit=128,with_vectors=True,with_payload=True)
            yield from points
            if offset is None:
                break
