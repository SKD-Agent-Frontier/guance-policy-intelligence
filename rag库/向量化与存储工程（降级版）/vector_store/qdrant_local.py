"""真正使用QdrantClient(path=...)的进程内持久化，不连接任何Qdrant端口。

Local不提供服务端HNSW/快照；搜索为本地精确计算，数据量大会占内存。
同一路径由项目锁和Qdrant锁保护，禁止绕过锁或由两个进程同时打开。
"""
from qdrant_client import QdrantClient,models
import sqlite3
from .contracts import FILTER_KEYS


def query_filter(filters):
    if set(filters)-FILTER_KEYS:raise ValueError('unsupported filter field')
    return models.Filter(must=[models.FieldCondition(key=k,match=models.MatchValue(value=v)) for k,v in filters.items()]) if filters else None


class QdrantLocalVectors:
    def __init__(self,path):
        path.mkdir(parents=True,exist_ok=True);self.path=path
        self._client=None

    @property
    def client(self):
        # 纯BM25查询不加载约0.75GiB的dense向量；只有实际使用向量接口才打开。
        if self._client is None:
            self._client=QdrantClient(path=str(self.path))
            # 先取得SDK自身的路径锁，再调整其SQLite日志配置，不绕开并发约束。
            # 1.15.1的持久化布局固定为collection/<name>/storage.sqlite。
            # 只设置日志模式，不读写内部point表，所有向量操作继续走公开SDK。
            for file in self.path.glob('collection/*/storage.sqlite'):self._wal(file)
        return self._client

    @staticmethod
    def _wal(file):
        """保留SQLite默认FULL同步；WAL避免逐point反复创建/删除回滚日志。"""
        file.parent.mkdir(parents=True,exist_ok=True)
        connection=sqlite3.connect(file,timeout=30)
        try:
            if connection.execute('PRAGMA journal_mode=WAL').fetchone()[0]!='wal':raise ValueError('Qdrant Local WAL unavailable')
        finally:connection.close()

    def count(self,collection):return self.client.count(collection,exact=True).count

    def create(self,collection):
        if not self.client.collection_exists(collection):
            if '/' in collection or '\\' in collection or collection in ('.','..'):raise ValueError('unsafe collection name')
            self._wal(self.path/'collection'/collection/'storage.sqlite')
            self.client.create_collection(collection,vectors_config={'dense':models.VectorParams(size=4096,distance=models.Distance.COSINE)})
        config=self.client.get_collection(collection).config.params.vectors['dense']
        if config.size!=4096 or config.distance!=models.Distance.COSINE:raise ValueError('incompatible local vector space')
        # Local中不创建无效的payload/HNSW索引，不将接受参数冒充服务端能力。

    def upsert(self,collection,records):
        if records:self.client.upsert(collection,[models.PointStruct(id=r['point_id'],vector={'dense':v.tolist()},payload=p) for r,v,p in records],wait=True)

    def scan(self,collection):
        offset=None
        while True:
            # Local每页需要重新定位有序ID，适当增大页可减少全库扫描开销；
            # 只保留一个512条页面，验证层仍以128条有界预读缓存。
            points,offset=self.client.scroll(collection,offset=offset,limit=512,with_vectors=True,with_payload=True)
            yield from points
            if offset is None:break

    def ids(self,collection):
        result=set();offset=None
        while True:
            points,offset=self.client.scroll(collection,offset=offset,limit=1000,with_vectors=False,with_payload=False)
            result.update(str(p.id) for p in points)
            if offset is None:return result

    def search(self,collection,vector,limit,filters):
        points=self.client.query_points(collection,query=vector.tolist(),using='dense',query_filter=query_filter(filters),limit=limit,with_payload=True).points
        return [{'id':p.payload['representation_id'],'score':float(p.score),'payload':p.payload} for p in points]

    def close(self):
        if self._client is not None:
            try:
                # 显式checkpoint后再释放SDK锁。只close连接不总能回写WAL（例如
                # 另有空闲SQLite诊断连接）；备份只取主文件必须先证明回写完成。
                for file in self.path.glob('collection/*/storage.sqlite'):
                    connection=sqlite3.connect(file,timeout=30)
                    try:
                        if connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:raise ValueError('Qdrant Local checkpoint busy; cannot safely copy database')
                    finally:connection.close()
            finally:self._client.close();self._client=None
