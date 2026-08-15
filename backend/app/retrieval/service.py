from __future__ import annotations

import math
import re
from enum import StrEnum

from ..database import Database
from ..providers.base import EmbeddingProvider
from ..scoring import evidence_channel


class RetrievalPolicy(StrEnum):
    RECONSTRUCTION = "reconstruction"
    LEARNING = "learning"


def _tokens(text: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9_]{2,}", text.lower()))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    words.update(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    return words


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=False)) / (left_norm * right_norm)


class SessionRetriever:
    def __init__(self, db: Database, embeddings: EmbeddingProvider):
        self.db = db
        self.embeddings = embeddings

    async def index_resource(self, resource_id: str) -> int:
        chunks = self.db.list_document_chunks(resource_id)
        if not chunks:
            return 0
        vectors = await self.embeddings.embed_texts([chunk["text"] for chunk in chunks])
        self.db.update_chunk_embeddings(
            {chunk["id"]: vector for chunk, vector in zip(chunks, vectors, strict=True)}
        )
        return len(vectors)

    async def retrieve(
        self,
        session_id: str,
        query: str,
        policy: RetrievalPolicy,
        limit: int = 18,
    ) -> list[dict]:
        chunks = self.db.list_session_chunks(session_id)
        if not chunks:
            return []
        query_tokens = _tokens(query)
        query_vector = (await self.embeddings.embed_texts([query]))[0]
        scored: list[tuple[float, dict]] = []
        for chunk in chunks:
            text_tokens = _tokens(chunk["text"])
            lexical = len(query_tokens & text_tokens) / max(1, len(query_tokens))
            semantic = _cosine(query_vector, chunk.get("embedding") or [])
            trust = self._policy_weight(chunk, policy)
            score = trust * (0.15 + lexical * 0.45 + max(0.0, semantic) * 0.40)
            scored.append((score, chunk))
        scored.sort(key=lambda item: (-item[0], item[1]["position"]))
        return [{**chunk, "retrieval_score": round(score, 5)} for score, chunk in scored[:limit]]

    @staticmethod
    def _policy_weight(chunk: dict, policy: RetrievalPolicy) -> float:
        resource = {"type": chunk["resource_type"], "evidence_level": chunk["evidence_level"]}
        channel = evidence_channel(resource)
        if policy == RetrievalPolicy.RECONSTRUCTION:
            return {
                "classroom": 1.0,
                "official_session": 0.9,
                "course_context": 0.5,
                "supplementary": 0.25,
            }[channel]
        if chunk["resource_type"] == "textbook" and chunk["evidence_level"] == "official":
            return 1.0
        if chunk["resource_type"] == "slides" and chunk["evidence_level"] == "official":
            return 0.95
        return {
            "classroom": 0.65,
            "official_session": 0.9,
            "course_context": 0.95,
            "supplementary": 0.8,
        }[channel]

    @staticmethod
    def attach_to_resources(resources: list[dict], chunks: list[dict]) -> list[dict]:
        by_resource: dict[str, list[dict]] = {}
        for chunk in chunks:
            by_resource.setdefault(chunk["resource_id"], []).append(chunk)
        return [
            {**resource, "retrieved_chunks": by_resource.get(resource["id"], [])}
            for resource in resources
            if resource.get("transcript_segments")
            or by_resource.get(resource["id"])
            or resource.get("extracted_text")
        ]
