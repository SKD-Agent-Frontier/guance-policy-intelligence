"""复用既有BM25S、Qwen输入契约和Qdrant Local；不建立第二套索引。"""
from collections import defaultdict
import httpx
import numpy as np

from .errors import BackendError, InvalidArgument
from .legacy import component
from .storage import read_json


def fuse_sources(channels, members, rrf_k=60):
    """每路同source仅保留最好原始名次，再融合；不累计同块多表示的分数。"""
    scores = defaultdict(float)
    representatives = {}
    for name in sorted(channels):
        seen = set()
        for rank, hit in enumerate(channels[name], 1):
            rid = hit["id"]
            source = members[rid]["record"]["source_id"]
            if source in seen:
                continue
            seen.add(source)
            scores[source] += 1 / (rrf_k + rank)
            candidate = (rank, name, rid)
            if source not in representatives or candidate < representatives[source]:
                representatives[source] = candidate
    return [(source, scores[source], representatives[source][2])
            for source in sorted(scores, key=lambda s: (-scores[s], s))]


class LexicalReader:
    def __init__(self, project, root, generation, cache):
        if not root.is_dir():
            raise BackendError("BM25索引目录不存在")
        cls = component(project, "bm25s_index").BM25SLexical
        self.index = cls(root)
        # jieba仅在独立运行缓存中写分词缓存，不回写语料索引。
        cache.mkdir(parents=True, exist_ok=True)
        self.index.tokenizer.tmp_dir = str(cache)
        self.index.load(generation)
        self.index.tokenizer.initialize()
        self.loads = 1

    def search(self, query, limit, allowed):
        return self.index.search(query, limit, allowed)

    def close(self):
        self.index.close()


class DenseReader:
    def __init__(self, root, collection):
        from qdrant_client import QdrantClient, models
        self.models = models
        self.client = None
        if not root.is_dir():
            raise BackendError("向量索引目录不存在")
        try:
            self.client = QdrantClient(path=str(root))
            config = self.client.get_collection(collection).config.params.vectors["dense"]
            if config.size != 4096 or config.distance != models.Distance.COSINE:
                raise BackendError("既有向量空间不符合冻结配置")
            self.count = self.client.count(collection, exact=True).count
        except Exception:
            self.close()
            raise
        self.collection, self.loads = collection, 1

    def search(self, vector, limit, versions):
        # 版本集合和text_chunk限制在Top-K前执行；不从全库Top-K后裁掉不合格项。
        m = self.models
        filters = m.Filter(must=[
            m.FieldCondition(key="source_type", match=m.MatchValue(value="text_chunk")),
            m.FieldCondition(key="document_version_id", match=m.MatchAny(any=sorted(versions))),
        ])
        points = self.client.query_points(self.collection, query=vector.tolist(), using="dense",
                                          query_filter=filters, limit=limit, with_payload=True).points
        return [{"id": p.payload["representation_id"], "score": float(p.score), "point_id": str(p.id)} for p in points]

    def close(self):
        if self.client is not None:
            self.client.close()
            self.client = None


class QueryEncoder:
    def __init__(self, options, manifest):
        self.client = None
        self.manifest, self.project = manifest, options.store_project
        self.module = component(self.project, "embedding")
        self.common = component(self.project, "common")
        self.counter = self.module.Counter(self.project)
        profile = manifest["profile"]
        if self.counter.profile != profile["local_processor_profile"] or self.counter.instruction != profile["instruction"]:
            raise BackendError("本地查询输入配置与冻结向量空间不一致")
        private = self.common.secrets()
        if private["embedding_base_url"] != profile["service_url"]:
            raise BackendError("查询服务与冻结配置不一致")
        self.client = httpx.Client(base_url=private["embedding_base_url"].rstrip("/"),
                                  headers={"Authorization": "Bearer " + private["embedding_api_key"]},
                                  timeout=httpx.Timeout(options.execution_timeout, connect=min(10, options.execution_timeout)),
                                  trust_env=False)
        self.cache = self.module.Cache(manifest["profile_id"], options.cache_directory / "query_vectors" / manifest["profile_id"])
        old_path = self.project / ".cache/query_vectors" / manifest["profile_id"]
        self.old_cache = self.module.Cache(manifest["profile_id"], old_path) if old_path.is_dir() else None
        self.calls, self.cache_hits, self.canary_verified = 0, 0, False

    def _request(self, tokens):
        self.calls += 1
        try:
            response = self.client.post("/embeddings", json={"model": self.manifest["profile"]["service_model"]["id"],
                                        "input": [tokens], "encoding_format": "base64"})
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPError, ValueError):
            # 不将包含鉴权地址、响应原文或凭证的异常透传到日志/Agent。
            raise BackendError("查询向量服务不可用或响应无效") from None
        if result.get("model") != self.manifest["profile"]["service_model"]["id"]:
            raise BackendError("向量服务返回不同模型")
        if result.get("usage", {}).get("prompt_tokens") != len(tokens):
            raise BackendError("向量输入token数不一致，可能被截断")
        data = result.get("data", [])
        if len(data) != 1 or data[0].get("index") != 0:
            raise BackendError("查询向量条数不一致")
        return self.module.normalize(data[0]["embedding"]), result["usage"]

    def vector(self, text):
        rendered, tokens = self.counter.encode(text)
        if len(tokens) > self.manifest["profile"]["max_input_tokens"]:
            raise InvalidArgument("查询超出向量模型输入上限")
        description = {"type": "text", "token_ids": tokens, "expected_tokens": len(tokens),
                       "rendered_hash": self.common.digest(rendered)}
        key = self.common.digest(description)
        cached = self.cache.get(key) or (self.old_cache.get(key) if self.old_cache else None)
        if cached:
            self.cache_hits += 1
            return cached[0]
        if not self.canary_verified:
            # 只核对既有基准，不重建或覆盖建库侧的空间探针。
            path = self.project / ".cache/service-canaries" / (self.manifest["profile_id"] + ".json")
            if not path.is_file():
                raise BackendError("缺少既有向量空间探针，不能确认查询空间")
            old = read_json(path)
            _, probe_tokens = self.counter.encode("政策检索向量空间校验。")
            probe, _ = self._request(probe_tokens)
            if float(np.dot(np.asarray(old["vector"], dtype=np.float32), probe)) < .99999:
                raise BackendError("服务向量空间发生变化")
            self.canary_verified = True
        vector, usage = self._request(tokens)
        self.cache.put(key, vector, usage)
        return vector

    def close(self):
        if self.client is not None:
            self.client.close()
            self.client = None
