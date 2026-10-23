"""SQLite权威来源、父子关系、任务和发布指针；不模拟PostgreSQL协议。

JSON保留原始字段，外键约束来源身份；BM25和向量索引可以由这些来源重建。
SQLite事务只管理元数据。三个存储完成独立校验后才能发布，不能伪造跨库事务。
"""
import json
import sqlite3
from pathlib import Path
from .common import canonical,digest,utc_now

SCHEMA=(Path(__file__).resolve().parents[1]/'schema.sql').read_text(encoding='utf-8')


class SQLiteMetadata:
    def __init__(self,path):
        path.parent.mkdir(parents=True,exist_ok=True)
        self.path=path;self.connection=sqlite3.connect(path,timeout=30)
        self.connection.row_factory=sqlite3.Row
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('PRAGMA synchronous=FULL')
        self.connection.executescript(SCHEMA)

    def put_documents(self,records):
        with self.connection:
            for r in records:
                cursor=self.connection.execute('INSERT INTO documents VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data_hash=excluded.data_hash WHERE documents.data_hash=excluded.data_hash',
                    (r['id'],r['planid'],r['source_hash'],canonical(r),digest(r)))
                if cursor.rowcount!=1:raise ValueError('document identity conflict')
        self.put_objects('document',records)

    def put_objects(self,kind,records):
        """按批原子写入不可变对象；同ID不同正文立即失败，不覆盖旧证据。"""
        batch=[]
        for r in records:
            owner=r.get('document_id') or (r['id'] if kind=='document' else None)
            batch.append((r['id'],owner,kind,canonical(r),digest(r)))
            if len(batch)>=500:self._objects(batch);batch=[]
        if batch:self._objects(batch)

    def _objects(self,batch):
        with self.connection:
            cursor=self.connection.executemany('INSERT INTO objects VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data_hash=excluded.data_hash WHERE objects.data_hash=excluded.data_hash',batch)
            if cursor.rowcount!=len(batch):raise ValueError('source object identity conflict')

    def object(self,object_id):
        row=self.connection.execute('SELECT data FROM objects WHERE id=?',(object_id,)).fetchone()
        return json.loads(row['data']) if row else None

    def document(self,document_id):
        row=self.connection.execute('SELECT data FROM documents WHERE id=?',(document_id,)).fetchone()
        return json.loads(row['data']) if row else None

    def object_owners(self):
        """批量返回对象归属。逐点校验不必反复解码整段父块，正文另做全量指纹检查。"""
        return {r['id']:r['document_id'] for r in self.connection.execute('SELECT id,document_id FROM objects')}

    def register_links(self,children,relations):
        with self.connection:
            self.connection.executemany('INSERT OR IGNORE INTO children VALUES(?,?)',((r['id'],r['parent_id']) for r in children))
            for r in relations:
                source=r.get('from_chunk_id') or r.get('from_parent_id') or r.get('source_id')
                target=r.get('target_id') if r['status']=='resolved' else None
                if not source or (r['status']=='resolved' and not target):raise ValueError('invalid relation endpoint')
                self.connection.execute('INSERT OR IGNORE INTO links VALUES(?,?,?,?)',(r['id'],source,target,r['status']))

    def begin(self,generation,manifest):
        with self.connection:
            row=self.connection.execute('SELECT manifest FROM releases WHERE id=?',(generation,)).fetchone()
            if row and row['manifest']!=canonical(manifest):raise ValueError('release identity conflict')
            self.connection.execute('INSERT OR IGNORE INTO releases VALUES(?,?,?,?,?)',(generation,'building',canonical(manifest),None,utc_now()))

    def put_record(self,generation,record,payload,text,description=None):
        """表示身份与批次成员分开。同一来源表示可被不同本地发布重复引用。

        初次迁移可只引用已校验的既有向量；新调用的真实description另存，不猜造旧请求。
        """
        if description is not None and digest(description)!=record['input_hash']:raise ValueError('input description hash mismatch')
        h=digest({'record':record,'text':text})
        cursor=self.connection.execute('''INSERT INTO representations VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET description=coalesce(representations.description,excluded.description)
            WHERE representations.data_hash=excluded.data_hash''',
            (record['id'],record['source_id'],record['document_id'],record['point_id'],record['input_hash'],record['profile_id'],canonical(record),text,canonical(description) if description is not None else None,h))
        if cursor.rowcount!=1:raise ValueError('representation identity conflict')
        self.connection.execute('INSERT INTO members VALUES(?,?,?) ON CONFLICT(generation,representation_id) DO UPDATE SET payload=excluded.payload',
            (generation,record['id'],canonical(payload)))

    def commit(self):self.connection.commit()

    def task(self,generation,input_hash,status,error=None):
        self.connection.execute('INSERT INTO tasks VALUES(?,?,?,?) ON CONFLICT(generation,input_hash) DO UPDATE SET status=excluded.status,error=excluded.error',(generation,input_hash,status,error))

    def unfinished(self,generation):
        return self.connection.execute("SELECT count(*) FROM tasks WHERE generation=? AND status<>'ready'",(generation,)).fetchone()[0]

    def members(self,generation):
        cursor=self.connection.execute('SELECT r.*,m.payload FROM members m JOIN representations r ON r.id=m.representation_id WHERE m.generation=? ORDER BY r.id',(generation,))
        for row in cursor:
            yield {'record':json.loads(row['record']),'payload':json.loads(row['payload']),'text':row['text']}

    def release(self,generation):
        row=self.connection.execute('SELECT * FROM releases WHERE id=?',(generation,)).fetchone()
        return dict(row) if row else None

    def active(self):
        row=self.connection.execute('SELECT generation,revision FROM runtime WHERE singleton=1').fetchone()
        return dict(row) if row else None

    def ready(self,generation,validation):
        if not validation['passed']:raise ValueError('cannot mark invalid release ready')
        with self.connection:self.connection.execute("UPDATE releases SET status='ready',validation=? WHERE id=?",(canonical(validation),generation))

    def activate(self,generation,expected_revision):
        """指针只在所有索引校验后切换；旧revision不能覆盖新的发布决定。"""
        with self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            row=self.connection.execute('SELECT status FROM releases WHERE id=?',(generation,)).fetchone()
            if not row or row['status']!='ready':raise ValueError('release is not ready')
            active=self.active();revision=active['revision'] if active else 0
            if revision!=expected_revision:raise ValueError('activation revision conflict')
            if active and active['generation']==generation:return active
            self.connection.execute('INSERT INTO runtime VALUES(1,?,?) ON CONFLICT(singleton) DO UPDATE SET generation=excluded.generation,revision=excluded.revision',(generation,revision+1))
            self.connection.execute('INSERT INTO activation_history(generation,revision,at) VALUES(?,?,?)',(generation,revision+1,utc_now()))
        return self.active()

    def integrity(self):
        if self.connection.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or self.connection.execute('PRAGMA foreign_key_check').fetchall():return False
        # SQLite结构完整不等于JSON正文未变；重算存入时的内容指纹，不能只验外键。
        for table in ('documents','objects'):
            if any(digest(r['data'])!=r['data_hash'] for r in self.connection.execute('SELECT data,data_hash FROM '+table)):return False
        for r in self.connection.execute('SELECT record,text,description,input_hash,data_hash FROM representations'):
            if digest({'record':json.loads(r['record']),'text':r['text']})!=r['data_hash']:return False
            if r['description'] is not None and digest(json.loads(r['description']))!=r['input_hash']:return False
        return True

    def related(self,source_ids):
        if not source_ids:return []
        marks=','.join('?' for _ in source_ids)
        ids=[r[0] for r in self.connection.execute('SELECT target_id FROM links WHERE status=\'resolved\' AND source_id IN ('+marks+')',source_ids)]
        return [self.object(i) for i in ids]

    def rebase(self,old_root,new_root):
        """仅备份恢复使用：迁移清单中的本地绝对路径，不改变原文/表示身份。"""
        with self.connection:
            for row in self.connection.execute('SELECT id,manifest FROM releases').fetchall():
                data=json.loads(row['manifest'])
                for key in ('prepared_path','media_path'):
                    data[key]=str(Path(new_root)/Path(data[key]).relative_to(Path(old_root)))
                self.connection.execute('UPDATE releases SET manifest=? WHERE id=?',(canonical(data),row['id']))

    def close(self):
        self.connection.commit();self.connection.execute('PRAGMA wal_checkpoint(TRUNCATE)');self.connection.close()
