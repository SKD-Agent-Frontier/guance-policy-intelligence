"""唯一存储装配入口；业务层通过独立接口使用三个本地适配器。"""
from .sqlite_metadata import SQLiteMetadata
from .qdrant_local import QdrantLocalVectors
from .bm25s_index import BM25SLexical
from .contracts import MetadataStore,VectorStore,LexicalStore


def open_stores(project,config) -> tuple[MetadataStore,VectorStore,LexicalStore]:
    storage=config['storage']
    if storage['metadata']!='sqlite' or storage['vectors']!='qdrant_local' or storage['lexical']!='bm25s':
        raise ValueError('storage backend has no installed adapter')
    root=project/storage['directory'];root.mkdir(parents=True,exist_ok=True)
    db=SQLiteMetadata(root/'metadata.sqlite3')
    try:vectors=QdrantLocalVectors(root/'qdrant')
    except Exception:db.close();raise
    return db,vectors,BM25SLexical(root/'bm25')
