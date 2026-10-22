"""真实存储验证、按来源回取、备份与恢复演练；不把文件存在称为恢复成功。"""
import os
from pathlib import Path
import shutil
import subprocess
import uuid
import numpy as np
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from .common import SHARED,paths,secrets,read_json,write_json,file_hash,rows,digest,utc_now,long_path
from .embedding import Cache
from .storage import Database,Vectors
from .validation import validate_release,compare_point


def open_release(project,generation=None):
    config,_,_=paths(project)
    pointer=read_json(Path(project)/'当前批次.json') if generation is None else None
    generation=generation or pointer['generation_id']
    output=Path(project)/'outputs'/generation
    frozen=read_json(output/'manifest.json')
    private=secrets()
    return config,generation,output,frozen,private


def verify(project,generation=None):
    config,generation,output,frozen,private=open_release(project,generation)
    db=Database(private,config['postgres_schema']);vectors=Vectors(private)
    try:
        return validate_release(db,vectors,generation,Path(frozen['prepared_path']),Cache(frozen['profile_id']),output)
    finally:
        vectors.close();db.close()


def document_status(project,planid):
    """从同一release回报政策覆盖、表示类型及未入库原因，不仅返回point总数。"""
    config,generation,output,frozen,private=open_release(project)
    db=Database(private,config['postgres_schema'])
    try:
        document=db.connection.execute('SELECT dv.* FROM release_documents rd JOIN document_versions dv USING(document_id) WHERE rd.generation_id=%s AND dv.planid=%s',(generation,planid)).fetchone()
        if not document:
            return {'planid':planid,'generation_id':generation,'status':'not_in_this_release'}
        counts=list(db.connection.execute('SELECT er.source_type,er.representation_type,count(*) AS count FROM release_representations rr JOIN embedding_records er USING(representation_id) WHERE rr.generation_id=%s AND er.document_id=%s GROUP BY er.source_type,er.representation_type',(generation,document['document_id'])))
        exclusions=list(db.connection.execute('SELECT x.source_id,x.reason FROM release_exclusions x JOIN source_objects o ON o.object_id=x.source_id WHERE x.generation_id=%s AND o.document_id=%s',(generation,document['document_id'])))
        warnings=list(db.connection.execute('SELECT x.source_id,x.reason FROM release_warnings x JOIN source_objects o ON o.object_id=x.source_id WHERE x.generation_id=%s AND o.document_id=%s',(generation,document['document_id'])))
        return {'planid':planid,'generation_id':generation,'document_id':document['document_id'],
            'source_hash':document['source_hash'],'source_status':document['data']['status'],
            'representations':counts,'excluded':exclusions,'warnings':warnings}
    finally:db.close()


def smoke(project):
    """用已存真实向量做自命中及PG父块/必要关联回取，不宣称检索准确率。"""
    config,generation,output,frozen,private=open_release(project)
    db=Database(private,config['postgres_schema']);vectors=Vectors(private)
    cache=Cache(frozen['profile_id']);results=[]
    try:
        selected=db.connection.execute('SELECT DISTINCT ON (payload->>\'planid\') * FROM release_representations WHERE generation_id=%s AND (payload->>\'planid\')=ANY(%s) ORDER BY payload->>\'planid\',representation_id LIMIT 5',(generation,config['sample_planids'])).fetchall()
        from qdrant_client import models
        for row in selected:
            record=db.connection.execute('SELECT * FROM embedding_records WHERE representation_id=%s',(row['representation_id'],)).fetchone()
            vector,_=cache.get(record['input_hash'])
            matches=vectors.client.query_points(frozen['collection'],query=vector.tolist(),using='dense',limit=10,
                query_filter=models.Filter(must=[models.FieldCondition(key='planid',match=models.MatchValue(value=row['payload']['planid']))])).points
            # 同输入可有并列向量；核对最高分接近1且命中来源集合，不强制并列排序。
            if not matches or matches[0].score<.999:
                raise ValueError('stored vector self lookup failed')
            source=db.connection.execute('SELECT * FROM source_objects WHERE object_id=%s',(record['source_id'],)).fetchone()
            if source is None or source['document_id']!=record['document_id']:
                raise ValueError('retrieved source provenance mismatch')
            parent=None
            parent_id=source['data'].get('parent_id')
            if parent_id:
                parent=db.connection.execute('SELECT * FROM source_objects WHERE object_id=%s',(parent_id,)).fetchone()
                if not parent or parent['document_id']!=record['document_id']:
                    raise ValueError('retrieved parent provenance mismatch')
            relations=list(db.connection.execute("SELECT data FROM relations WHERE source_id=ANY(%s) AND resolution_status='resolved'",([record['source_id'],parent_id] if parent_id else [record['source_id']],)))
            results.append({'planid':row['payload']['planid'],'representation_id':record['representation_id'],
                'top_score':matches[0].score,'source_id':source['object_id'],'parent_id':parent_id,
                'resolved_relations_returned':len(relations),'passed':True})
        write_json(output/'storage_smoke.json',{'passed':bool(results),'checks':results,'retrieval_quality_evaluation':False})
        db.connection.commit()
        return results
    finally:
        vectors.close();db.close()


def backup(project):
    """阻止发布指针在备份途中切换；记录PG dump、Qdrant快照与来源/缓存文件摘要。"""
    config,generation,output,frozen,private=open_release(project)
    db=Database(private,config['postgres_schema']);vectors=Vectors(private)
    target=output/'backup';long_path(target).mkdir(exist_ok=True)
    try:
        runtime=db.connection.execute('SELECT * FROM rag_runtime WHERE knowledge_base_id=%s FOR SHARE',(config['knowledge_base_id'],)).fetchone()
        if runtime['active_generation_id']!=generation:
            raise ValueError('active release changed before backup')
        from runtime_setup import native_path
        executable=native_path(SHARED/'.runtime/postgres-bin/pgsql/bin/pg_dump.exe')
        connection=conninfo_to_dict(private['postgres_dsn'])
        environment={**os.environ,'PGPASSWORD':connection.pop('password')}
        dump=target/'postgres.dump'
        command=[str(executable),'--host',connection['host'],'--port',connection['port'],'--username',connection['user'],
            '--dbname',connection['dbname'],'--format=custom','--schema',config['postgres_schema'],'--file',str(native_path(dump))]
        subprocess.run(command,env=environment,check=True,capture_output=True)
        snapshot=vectors.client.create_snapshot(frozen['collection'],wait=True)
        source=SHARED/'.runtime/snapshots'/frozen['collection']/snapshot.name
        if not source.exists():
            raise ValueError('Qdrant native snapshot path unavailable')
        qbackup=target/snapshot.name
        shutil.copyfile(long_path(source),long_path(qbackup))
        artifacts={'postgres.dump':file_hash(dump),snapshot.name:file_hash(qbackup)}
        # 来源与向量必须是独立备份副本，不只登记原路径；统一记录相对备份路径与摘要。
        prepared=Path(frozen['prepared_path']);copies=[]
        for folder,label in [(prepared,'prepared'),(Path(frozen['media_path']),'media'),(output,'release')]:
            for path in long_path(folder).rglob('*'):
                logical=folder/path.relative_to(long_path(folder))
                if not path.is_file() or target in logical.parents or (label=='release' and path.name in {'backup_receipt.json'}):
                    continue
                relative=Path(label)/path.relative_to(long_path(folder))
                destination=long_path(target/'artifacts'/relative);destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(path,destination)
                copies.append({'path':relative.as_posix(),'sha256':file_hash(destination)})
        cache=Cache(frozen['profile_id'])
        keys={r['input_hash'] for r in db.connection.execute('SELECT er.input_hash FROM release_representations rr JOIN embedding_records er USING(representation_id) WHERE generation_id=%s',(generation,))}
        for key in keys:
            cached=cache.get(key)
            if not cached:raise ValueError('backup vector cache missing')
            for suffix in ('.json','.f32'):
                source=cache.root/(key+suffix);destination=long_path(target/'vectors'/source.name)
                destination.parent.mkdir(exist_ok=True);shutil.copyfile(source,destination)
                copies.append({'path':('vectors/'+source.name),'sha256':file_hash(destination)})
        db.connection.commit()
        receipt={'generation_id':generation,'created_at':utc_now(),'runtime_revision':runtime['revision'],
            'postgres_schema':config['postgres_schema'],'qdrant_snapshot':snapshot.name,
            'database_files':artifacts,'artifacts':copies,'restore_verified':False}
        write_json(output/'backup_receipt.json',receipt)
        return receipt
    finally:
        db.connection.rollback();vectors.close();db.close()


def restore_drill(project):
    """恢复到新数据库与新collection并逐点对账；保留演练产物，不删除其他数据库。"""
    config,generation,output,frozen,private=open_release(project)
    receipt=read_json(output/'backup_receipt.json');target=output/'backup'
    for relative,checksum in receipt['database_files'].items():
        if file_hash(target/relative)!=checksum:raise ValueError('database backup checksum mismatch')
    for item in receipt['artifacts']:
        path=target/item['path'] if item['path'].startswith('vectors/') else target/'artifacts'/item['path']
        if file_hash(path)!=item['sha256']:raise ValueError('artifact backup checksum mismatch')
    connection=conninfo_to_dict(private['postgres_dsn']);password=connection.pop('password')
    name='guance_restore_'+uuid.uuid4().hex[:12]
    with psycopg.connect(**{**connection,'dbname':'postgres','password':password},autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    from runtime_setup import native_path
    executable=native_path(SHARED/'.runtime/postgres-bin/pgsql/bin/pg_restore.exe')
    command=[str(executable),'--host',connection['host'],'--port',connection['port'],'--username',connection['user'],
        '--dbname',name,'--exit-on-error',str(native_path(target/'postgres.dump'))]
    subprocess.run(command,env={**os.environ,'PGPASSWORD':password},check=True,capture_output=True)
    from psycopg.conninfo import make_conninfo
    restored_private={**private,'postgres_dsn':make_conninfo(**{**connection,'dbname':name,'password':password})}
    db=Database(restored_private,config['postgres_schema']);vectors=Vectors(private)
    restored_collection='guance_restore_'+uuid.uuid4().hex[:16]
    try:
        from qdrant_client import models
        with long_path(target/receipt['qdrant_snapshot']).open('rb') as snapshot_file:
            vectors.client.http.snapshots_api.recover_from_uploaded_snapshot(
                collection_name=restored_collection,wait=True,priority=models.SnapshotPriority.SNAPSHOT,
                checksum=receipt['database_files'][receipt['qdrant_snapshot']],snapshot=snapshot_file)
        with db.connection.transaction():
            # 只调整演练库的物理collection绑定；业务generation和point身份保持原值。
            db.connection.execute('UPDATE index_releases SET collection=%s WHERE generation_id=%s',(restored_collection,generation))
        expected={str(r['point_id']):r for r in db.connection.execute('SELECT rr.*,er.input_hash FROM release_representations rr JOIN embedding_records er USING(representation_id) WHERE generation_id=%s',(generation,))}
        seen=set();errors=[]
        for point in vectors.scan(restored_collection):
            key=str(point.id);seen.add(key);row=expected.get(key)
            if row is None:errors.append('unexpected_point');continue
            vector=np.frombuffer(long_path(target/'vectors'/(row['input_hash']+'.f32')).read_bytes(),dtype='<f4')
            errors.extend(compare_point(point,row['payload'],vector))
        if seen!=set(expected):errors.append('restored_member_set_mismatch')
        # 复核备份副本里的原文，证明引用入口可由副本重建。
        copied_prepared=target/'artifacts/prepared'
        from .validation import validate_prepared
        source=validate_prepared(copied_prepared,read_json(copied_prepared/'manifest.json'))
        snapshot_rows=db.connection.execute('SELECT dv.document_id,dv.snapshot_path,dv.source_hash FROM release_documents rd JOIN document_versions dv USING(document_id) WHERE generation_id=%s',(generation,)).fetchall()
        for row in snapshot_rows:
            if file_hash(copied_prepared/row['snapshot_path'])!=row['source_hash']:
                errors.append('restored_postgres_source_path_mismatch')
        result={'database':name,'collection':restored_collection,'generation_id':generation,
            'points_checked':len(seen),'source':source,'errors':errors,'passed':not errors and source['passed'],
            'cleanup':'drill artifacts retained; no existing database or collection deleted'}
        write_json(output/'restore_drill.json',result)
        return result
    finally:
        vectors.close();db.close()
