"""Validate SDK facts against the exact pages admitted by this run's reader."""
import asyncio
import time
from dataclasses import dataclass, field

from .verify import (
    _authorize_number_verified_claim,
    _authorize_quote_verified_claim,
    _fact_number_present,
    _identity_values,
    _mask_identities,
    _tokens,
    _unknown_identities,
    _unknown_titles,
)


def _source_key(ref):
    return (ref.planid, ref.document_id, ref.document_version_id, ref.generation_id,
            ref.source_hash, ref.offset_basis, ref.offset_unit, ref.chunk_id,
            ref.title, ref.area, ref.fyp, ref.section_idx, ref.section_path)


@dataclass
class ReadVerification:
    pages: dict[tuple, dict[tuple[int, int], str]] = field(default_factory=dict)
    confirmed: dict[tuple, bool] = field(default_factory=dict)
    rejected: set[tuple] = field(default_factory=set)
    unverified: set[tuple] = field(default_factory=set)
    removed_candidates: set[tuple] = field(default_factory=set)

    def observe(self, passage, registry):
        if passage.is_index or passage.char_start is None or passage.char_end is None:
            return
        if len(passage.content) != passage.char_end - passage.char_start:
            return
        for span in passage.chunks:
            ref = registry.get(span.chunk_id)
            if ref is None or ref.source != "kb_read" or ref.planid != passage.planid:
                continue
            if any(getattr(ref, name) != getattr(passage, name)
                   for name in ("document_id", "document_version_id", "generation_id",
                                "source_hash", "offset_basis", "offset_unit")):
                continue
            if not (passage.char_start <= span.char_start < span.char_end <= passage.char_end):
                continue
            if not any(start <= span.char_start and span.char_end <= end
                       for start, end in ref.observed_ranges):
                continue
            start, end = span.char_start - passage.char_start, span.char_end - passage.char_start
            self.pages.setdefault(_source_key(ref), {})[span.char_start, span.char_end] = (
                passage.content[start:end])

    async def confirm(self, state, chunk_id, quote, claim_text="", *, dimension=None):
        key = chunk_id, quote
        claim = claim_text, chunk_id, quote, dimension
        confirmed = await self._confirm(state, chunk_id, quote)
        if confirmed is True:
            self.rejected.discard(key)
            self.unverified.discard(key)
            self.removed_candidates.discard(claim)
            return True
        (self.unverified if confirmed is None else self.rejected).add(key)
        self.removed_candidates.add(claim)
        return False

    async def _confirm(self, state, chunk_id, quote):
        ref = state.run.evidence.get(chunk_id)
        if ref is None or not quote:
            return False
        source = _source_key(ref)
        cache_key = source, quote
        if cache_key in self.confirmed:
            return self.confirmed[cache_key]
        windows = self.pages.get(source, {})
        ranges = [bounds for bounds, page in windows.items() if quote in page]
        if not ranges:
            return False
        checker = getattr(state.run.kb, "verify_quote", None)
        if checker is not None:
            if state.spend_io("verify_quote"):
                return None
            timeout = min(state.deadline - time.monotonic(), state.run.settings.tool_timeout)
            if timeout <= 0:
                return None
            try:
                confirmed = await asyncio.wait_for(asyncio.to_thread(
                    checker, chunk_id, quote, ranges, document_id=ref.document_id,
                    generation_id=ref.generation_id), timeout=timeout)
            except Exception:
                return None
            if confirmed is not True:
                self.confirmed[cache_key] = False
                return False
        self.confirmed[cache_key] = True
        return True

    def verify_result(self, result, ctx):
        """No I/O at finalization, including after a model or audit timeout."""
        kept = []
        discarded = 0
        for claim in result.claims:
            refs = [ctx.evidence.get(cid) for cid in claim.chunk_ids]
            if (not refs or not claim.quotes or any(ref is None for ref in refs)
                    or not all(any(self.confirmed.get((_source_key(ref), quote))
                                   for ref in refs) for quote in claim.quotes)
                    or not all(any(self.confirmed.get((_source_key(ref), quote))
                                   for quote in claim.quotes) for ref in refs)):
                discarded += 1
                continue
            identities = _identity_values(refs)
            text = _mask_identities(claim.text, strict=identities)
            numbers = _tokens(text)
            if numbers:
                result.meta["number_verification"] = "literal_only"
            if (_unknown_identities(claim.text, identities)
                    or _unknown_titles(claim.text, {ref.title for ref in refs})
                    or any(not any(_fact_number_present(number, page) for ref in refs
                                   for page in self.pages[_source_key(ref)].values())
                           for number in numbers)):
                discarded += 1
                continue
            _authorize_quote_verified_claim(claim)
            _authorize_number_verified_claim(result, claim)
            kept.append(claim)
        result.claims = kept
        approved = sum(len(claim.quotes) for claim in kept)
        result.meta["quote_verification"] = {
            "checked": approved + len(self.rejected), "approved": approved,
            "rejected": len(self.rejected), "unverified": len(self.unverified),
            "removed_claims": discarded + len(self.removed_candidates),
        }
        if discarded:
            result.warnings.append("部分事实未通过本轮原文、身份或数字核验，已撤回。")
        result.meta["unified_claim_verification_completed"] = True
