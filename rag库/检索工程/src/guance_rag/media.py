"""冻结媒体回取：ID定位、原图字节核验、像素裁剪和独立OCR坐标。"""
import base64
import hashlib
import io
import json
from pathlib import Path
import re

from PIL import Image

from .errors import BackendError, InvalidArgument, KBNotFound
from .media_models import DocumentAnchor, ImageDataV04, MediaHitV04, MediaPassageV04, MediaPointer, OCRContext, OCRRange

MEDIA_TYPES = {"visual_chunk", "image_occurrence"}
SOURCE_TYPES = MEDIA_TYPES | {"text_chunk"}


class MediaLibrary:
    def __init__(self, store, options):
        self.store, self.options = store, options
        self.generations = {}
        self.objects = {}
        self.image_checks = 0
        self._load_generation(store.generation)

    def _object(self, generation, oid):
        key = generation, oid
        if key not in self.objects:
            value = self.store.object(oid)
            if value is None:
                raise BackendError("已发布媒体缺少来源对象")
            self.objects[key] = value
        return self.objects[key]

    def _load_generation(self, generation):
        if generation in self.generations:
            return self.generations[generation]
        if generation not in self.store.manifests:
            raise KBNotFound()
        if generation == self.store.generation:
            members = self.store.all_members.values()
        else:
            members = ({"record": json.loads(row[0]), "payload": json.loads(row[1])} for row in self.store.connection.execute(
                "SELECT r.record,m.payload FROM members m JOIN representations r ON r.id=m.representation_id WHERE m.generation=?", (generation,)))
        sources = {}
        for row in members:
            record = row["record"]
            if record["source_type"] in MEDIA_TYPES:
                sources.setdefault(record["source_id"], row)
        data = dict(sources=sources, parents={}, assets={})
        self.generations[generation] = data
        if not sources:
            return data
        manifest = self.store.manifests[generation]
        path = Path(manifest["media_path"]) / "visual_parents.jsonl"
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    parent = json.loads(line)
                    occurrence = parent["occurrence_id"]
                    if occurrence in data["parents"]:
                        raise BackendError("同一发布的图片出现位置关联多个识别版本")
                    data["parents"][occurrence] = parent["id"]
        # 当前发布的媒体映射和原图校验在ready之前完成，历史发布按需缓存。
        for source in sources:
            self.resolve(source, generation_id=generation)
        return data

    def _asset(self, generation, aid):
        data = self.generations[generation]
        if aid not in data["assets"]:
            asset = self._object(generation, aid)
            if asset.get("status") != "registered" or not re.fullmatch(r"[0-9a-f]{64}", asset.get("sha256", "")):
                raise BackendError("已发布媒体的原图身份无效")
            prepared = Path(self.store.manifests[generation]["prepared_path"]).resolve()
            path = (prepared / "media" / asset["sha256"] / Path(asset["paths"][0]).name).resolve()
            if not path.is_relative_to(prepared) or not path.is_file():
                raise BackendError("冻结原图不存在或路径超出发布目录")
            with path.open("rb") as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            if checksum != asset["sha256"] or path.stat().st_size != asset["bytes"]:
                raise BackendError("冻结原图字节哈希或长度不一致")
            with Image.open(path) as picture:
                if picture.size != (asset["width"], asset["height"]):
                    raise BackendError("原图尺寸与冻结记录不一致")
                mime = Image.MIME.get(picture.format)
            if mime is None:
                raise BackendError("不支持的冻结图片格式")
            data["assets"][aid] = dict(asset_id=aid, sha256=checksum, width=asset["width"], height=asset["height"],
                                       mime_type=mime, byte_length=asset["bytes"], path=str(path))
            self.image_checks += 1
        return data["assets"][aid]

    def resolve(self, source_id, *, planid=None, document_id=None, generation_id=None, section_idx=None):
        source = self.store.object(source_id)
        if source is None or not source_id.startswith(("visual_chunk_", "occurrence_", "document_media_occurrence_")):
            raise KBNotFound(planid, source_id=source_id)
        did = source["document_id"]
        if document_id is not None and did != document_id:
            raise InvalidArgument("媒体来源与document_id不一致")
        candidates = [generation_id] if generation_id else [self.store.generation] + sorted(set(self.store.manifests) - {self.store.generation})
        generation, data, member = None, None, None
        for candidate in candidates:
            candidate_data = self._load_generation(candidate)
            if source_id in candidate_data["sources"]:
                generation, data, member = candidate, candidate_data, candidate_data["sources"][source_id]
                break
        if generation is None:
            raise KBNotFound(planid, source_id=source_id)
        self.store.locate_generation(did, generation)
        record = member["record"]
        if record["document_id"] != did:
            raise BackendError("媒体表示的文档归属不一致")
        doc = self.store.document(did, generation)
        if planid is not None and doc.data["planid"] != planid:
            raise InvalidArgument("媒体来源不属于指定planid")
        self.store._index_document(did, generation)
        visual = record["source_type"] == "visual_chunk"
        occurrence = self._object(generation, source["occurrence_id"]) if visual else source
        if occurrence["document_id"] != did or source["asset_id"] != occurrence["asset_id"]:
            raise BackendError("图片出现位置与媒体归属不一致")
        section = next((s for s in self.store.sections[did] if s["id"] == occurrence.get("structure_id")), None)
        if section_idx is not None:
            if section_idx >= len(self.store.sections[did]):
                raise KBNotFound(doc.data["planid"], section_idx)
            if section is None or self.store.section_indices[section["id"]] != section_idx:
                raise InvalidArgument("媒体来源不属于指定章节")
        anchor, reference = occurrence.get("source_segment"), None
        if anchor is not None:
            a, b = anchor["start"], anchor["end"]
            if not doc.origin <= a < b <= len(doc.text) or doc.text[a:b] != anchor["text"]:
                raise BackendError("图片Markdown引用不能逐字还原")
            reference = DocumentAnchor(body_origin=doc.origin, char_start=a - doc.origin, char_end=b - doc.origin, content=anchor["text"])
        parent_id = data["parents"].get(occurrence["id"])
        parent = self._object(generation, parent_id) if parent_id else None
        extraction = self._object(generation, parent["extraction_id"]) if parent else None
        if parent:
            if (parent["document_id"], parent["occurrence_id"], parent["asset_id"]) != (did, occurrence["id"], occurrence["asset_id"]):
                raise BackendError("识别父块与原图来源不一致")
            if hashlib.sha256(parent["transcript"].encode()).hexdigest() != parent["transcript_hash"]:
                raise BackendError("识别转录哈希不一致")
            if extraction["asset_id"] != occurrence["asset_id"]:
                raise BackendError("识别版本与原图资产不一致")
        image = self._asset(generation, occurrence["asset_id"])
        if extraction and extraction["asset_hash"] != image["sha256"]:
            raise BackendError("识别版本不对应冻结原图字节")
        if visual:
            if parent is None or source["parent_id"] != parent["id"] or source["extraction_id"] != parent["extraction_id"]:
                raise BackendError("OCR子块不属于该发布的识别父块")
            a, b = source["derived_start"], source["derived_end"]
            if not 0 <= a < b <= len(parent["transcript"]) or parent["transcript"][a:b] != source["text"]:
                raise BackendError("OCR子块范围不能还原识别转录")
            box = source["bbox"]
            if (source["original_size"] != [image["width"], image["height"]] or len(box) != 4
                    or any(type(v) is not int for v in box) or not 0 <= box[0] < box[2] <= image["width"]
                    or not 0 <= box[1] < box[3] <= image["height"]):
                raise BackendError("识别区域像素范围无效")
        return doc, source, occurrence, parent, extraction, section, reference, image, record

    def evidence(self, source_id, *, planid=None, section_idx=None, document_id=None, generation_id=None,
                 offset=0, max_chars=6000, preview=False, context_budget=None):
        resolved = self.resolve(source_id, planid=planid, section_idx=section_idx, document_id=document_id, generation_id=generation_id)
        doc, source, occurrence, parent, extraction, section, reference, image, record = resolved
        visual = record["source_type"] == "visual_chunk"
        transcript = parent["transcript"] if parent else ""
        full_a, full_b = (source["derived_start"], source["derived_end"]) if visual else (0, len(transcript))
        warnings, contexts, span = [], [], None
        if reference is None:
            warnings.append("document_only_image_association")
        if full_a == full_b:
            if offset:
                raise InvalidArgument("无OCR文字的图片offset必须为0")
            content = occurrence.get("alt") or ""
            basis = "image_alt" if content else "none"
            next_offset, truncated = None, False
        else:
            if offset >= full_b - full_a:
                raise InvalidArgument("offset超出所选OCR转录范围")
            a, b = full_a + offset, min(full_b, full_a + offset + max_chars)
            content, basis = transcript[a:b], "ocr_transcript"
            span = OCRRange(transcript_id=parent["id"], transcript_hash=parent["transcript_hash"],
                            full_char_start=full_a, full_char_end=full_b, char_start=a, char_end=b)
            next_offset = b - full_a if b < full_b else None
            truncated = (a != full_a or b != full_b) if preview else b < full_b
            if next_offset is not None:
                warnings.append("ocr_page_incomplete")
        if visual:
            budget = self.options.max_context_chars if context_budget is None else context_budget
            for context in source.get("context_segments", []):
                a, b = context["start"], context["end"]
                if not 0 <= a < b <= len(transcript) or transcript[a:b] != context["text"]:
                    raise BackendError("OCR上下文不能还原同版转录")
                if b - a > budget:
                    warnings.append("ocr_context_budget_exceeded")
                    continue
                contexts.append(OCRContext(role=context["role"], char_start=a, char_end=b, content=transcript[a:b]))
                budget -= b - a
            if source.get("status") != "ready":
                warnings.append("upstream_status:" + source.get("status", "unknown"))
            warnings.append("region_bbox_model_estimated")
        if extraction is None:
            warnings.append("ocr_unavailable")
        elif extraction.get("status") != "recognized":
            warnings.append("ocr_status:" + extraction.get("status", "unknown"))
        warnings.append("verify_text_and_numbers_against_original_image")
        metadata = self.store.metadata(doc, section)
        for key in ("body_start", "body_origin"):
            metadata.pop(key)
        common = dict(**metadata, source_type=record["source_type"], source_id=source_id, occurrence_id=occurrence["id"],
                      asset_id=occurrence["asset_id"], visual_chunk_id=source_id if visual else None,
                      extraction_id=parent["extraction_id"] if parent else None, ocr_parent_id=parent["id"] if parent else None,
                      recognition_status=extraction["status"] if extraction else "unavailable",
                      kind=source.get("kind") if visual else (extraction.get("image_type") if extraction else None),
                      evidence_label="识别文本，未经人工逐字校对" if span else "原图；文字及数字请核对图像",
                      content=content, content_basis=basis, ocr_range=span, reference_span=reference,
                      association_basis=occurrence.get("association_basis") or ("markdown_reference" if reference else "unknown"),
                      image=dict(**image, bbox_pixels=list(source["bbox"]) if visual else None,
                                 coordinate_basis=source.get("coordinate_basis") if visual else None),
                      ocr_contexts=contexts, quality_warnings=list(dict.fromkeys(warnings)), truncated=truncated)
        if preview:
            return common
        return MediaPassageV04(**common, next_offset=next_offset)

    def hit(self, row, score, content_budget):
        record = row["record"]
        common = self.evidence(record["source_id"], generation_id=self.store.generation,
                               max_chars=min(self.options.snippet_chars, content_budget), preview=True, context_budget=0)
        used = len(common["content"])
        if used > content_budget:
            return None
        # 再按剩余总预算取完整OCR标题上下文，不能让媒体突破整次search预算。
        common = self.evidence(record["source_id"], generation_id=self.store.generation,
                               max_chars=min(self.options.snippet_chars, content_budget), preview=True,
                               context_budget=min(self.options.max_context_chars, content_budget - used))
        return MediaHitV04(**common, representation_id=record["id"], representation_type=record["representation_type"], score=score)

    def image_data(self, source_id, *, variant="original", document_id=None, generation_id=None):
        if not isinstance(variant, str) or variant not in {"original", "region"}:
            raise InvalidArgument("variant必须为original或region")
        doc, source, occurrence, parent, extraction, section, reference, image, record = self.resolve(
            source_id, document_id=document_id, generation_id=generation_id)
        path = Path(image["path"])
        prepared = Path(self.store.manifests[doc.generation]["prepared_path"]).resolve()
        if not path.resolve().is_relative_to(prepared):
            raise BackendError("原图路径已偏离冻结目录")
        if path.stat().st_size > self.options.max_image_bytes:
            raise InvalidArgument("原图超过配置的图片字节预算")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != image["sha256"]:
            raise BackendError("冻结原图在启动后发生变化")
        width, height, mime = image["width"], image["height"], image["mime_type"]
        bbox = [0, 0, width, height]
        if variant == "region":
            if record["source_type"] != "visual_chunk":
                raise InvalidArgument("整图出现位置没有独立区域；请使用visual_chunk来源")
            # 上游parse_ocr已将normalized_1000换算成像素。此处直接用存储bbox，禁止二次缩放。
            bbox = list(source["bbox"])
            with Image.open(io.BytesIO(raw)) as picture:
                cropped = picture.crop(tuple(bbox))
                if cropped.mode not in {"1", "L", "LA", "I", "I;16", "P", "RGB", "RGBA"}:
                    cropped = cropped.convert("RGB")
                buffer = io.BytesIO()
                cropped.save(buffer, format="PNG")
                raw, width, height, mime = buffer.getvalue(), cropped.width, cropped.height, "image/png"
        if len(raw) > self.options.max_image_bytes:
            raise InvalidArgument("返回图片超过配置的字节预算")
        return ImageDataV04(source_id=source_id, occurrence_id=occurrence["id"], asset_id=occurrence["asset_id"],
                            document_id=doc.data["id"], generation_id=doc.generation, variant=variant, mime_type=mime,
                            width=width, height=height, byte_length=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                            source_image_sha256=image["sha256"], bbox_pixels=bbox,
                            data_url="data:" + mime + ";base64," + base64.b64encode(raw).decode("ascii"))

    def references(self, doc, start, end, *, include_unanchored=False):
        """附上读页中已发布图片的ID。引用可定位不表示模型已经看过图片像素。"""
        data = self._load_generation(doc.generation)
        result = []
        for sid, row in data["sources"].items():
            r = row["record"]
            if r["source_type"] != "image_occurrence" or r["document_id"] != doc.data["id"]:
                continue
            source = self._object(doc.generation, sid)
            span = source.get("source_segment")
            if span is None:
                if include_unanchored:
                    result.append(MediaPointer(source_id=sid, asset_id=source["asset_id"], document_id=doc.data["id"],
                                               generation_id=doc.generation, association_basis=source.get("association_basis", "unknown"),
                                               reference_span=None, reference_complete=None))
            elif span["start"] < end and start < span["end"]:
                result.append(MediaPointer(source_id=sid, asset_id=source["asset_id"], document_id=doc.data["id"],
                                           generation_id=doc.generation, reference_complete=start <= span["start"] and span["end"] <= end,
                                           association_basis=source.get("association_basis", "markdown_reference"),
                                           reference_span=DocumentAnchor(body_origin=doc.origin, char_start=span["start"] - doc.origin,
                                                                        char_end=span["end"] - doc.origin, content=span["text"])))
        return sorted(result, key=lambda p: (p.reference_span.char_start if p.reference_span else float("inf"), p.source_id))
