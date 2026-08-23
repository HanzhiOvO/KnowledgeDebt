from __future__ import annotations

import re

from ..models import (
    Confidence,
    EvaluationResult,
    KnowledgePointDraft,
    KnowledgePointEvaluation,
    LearningStepDraft,
    MasteryEvidenceType,
    QuestionDraft,
    ReconstructionDraft,
    RemediationDraft,
    SourceRef,
    TranscriptSegment,
)
from .base import ProviderNotConfigured


class LocalRuleProvider:
    """Deterministic zero-configuration engine for local-first use.

    This provider deliberately never calls a remote model. It extracts a small,
    reviewable set of learning targets from the evidence chunks that the retrieval
    layer has already selected, cites their real chunk locators, and evaluates
    answers with a transparent keyword/length heuristic.

    It is the default only while no API key is configured. It is not advertised as
    semantic AI and its outputs are marked ``inferred`` so the reconstruction score
    and UI can communicate that limitation honestly.
    """

    requires_external_upload = False
    name = "local_rule"

    async def analyze_session(self, session: dict, evidence: list[dict]) -> ReconstructionDraft:
        points = self._extract_points(session, evidence)
        learning_path = self._build_learning_path(points)
        inferred = [point.title for point in points]
        return ReconstructionDraft(
            title=session.get("title") or "Untitled Session",
            summary=(
                f"本地规则引擎从 {len(evidence)} 份可读取资料中整理出 {len(points)} 个待掌握要点。"
                "该结果完全由检索到的资料块生成，不包含模型推断，也不声称还原了老师现场讲授。"
            ),
            topics=list(inferred),
            timeline=[],
            teacher_emphasis=[],
            examples=[],
            confirmed=[],
            inferred=inferred,
            knowledge_points=points,
            learning_path=learning_path,
        )

    async def generate_questions(
        self, session: dict, evidence: list[dict], knowledge_points: list[dict]
    ) -> list[QuestionDraft]:
        del session, evidence
        questions: list[QuestionDraft] = []
        for point in knowledge_points:
            sources = self._point_sources(point)
            description = str(point.get("description") or point.get("title") or "").strip()
            expected_mastery = int(point.get("expected_mastery") or point.get("expected_mastery_level") or 2)
            for level, prompt_suffix in (
                (
                    "understanding",
                    "它成立的关键条件或核心机制是什么？请结合你上传的资料，用自己的话解释。",
                ),
                (
                    "application",
                    "请举一个具体例子说明它如何应用，并写出你的推理步骤。",
                ),
            ):
                questions.append(
                    QuestionDraft(
                        knowledge_point_titles=[point["title"]],
                        prompt=f"请围绕「{point['title']}」回答：{prompt_suffix}",
                        level=level,
                        question_type="application" if level == "application" else "diagnostic",
                        expected_mastery_level=expected_mastery,
                        reference_answer=description[:1200],
                        rubric=[
                            "准确复述关键条件或核心机制",
                            "引用了资料中的依据或例子",
                            "能用自己的话表达，而不是复制原文",
                        ],
                        source_refs=list(sources),
                    )
                )
        return questions

    async def evaluate_answer(self, question: dict, answer: str, evidence: list[dict]) -> EvaluationResult:
        del evidence
        reference = str(question.get("reference_answer") or question.get("prompt") or "")[:180]
        coverage = self._coverage(reference, answer)
        length_bonus = min(1.0, len(answer.strip()) / 32.0)
        score = round(min(1.0, 0.25 + coverage * 0.45 + length_bonus * 0.30), 2)
        if self._looks_empty(answer):
            score = min(score, 0.25)
        met: list[str] = []
        missing: list[str] = []
        if coverage >= 0.30:
            met.append("答案与资料关键概念相关")
        else:
            missing.append("未覆盖资料中的关键条件或机制")
        if len(answer.strip()) >= 24:
            met.append("给出了足够的解释")
        else:
            missing.append("解释过于简略")
        if not self._looks_empty(answer):
            met.append("尝试用自己的话回答")
        else:
            missing.append("未给出有效回答")
        passed = score >= 0.75
        level = str(question.get("level") or "").lower()
        evidence_type = (
            MasteryEvidenceType.APPLICATION
            if level in {"application", "apply", "problem_solving"}
            else MasteryEvidenceType.UNDERSTANDING
        )
        return EvaluationResult(
            score=score,
            verdict="mastered" if passed else "not_yet",
            met_criteria=met,
            missing_criteria=missing,
            feedback=(
                "回答已覆盖核心概念，可以进入下一题。"
                if passed
                else "回答还不够完整。请回到资料页，找出关键条件或机制，再用一个例子解释。"
            ),
            point_results=[
                KnowledgePointEvaluation(
                    knowledge_point_title=self._question_point_title(question),
                    score=score,
                    evidence_type=evidence_type,
                    feedback="本地规则评分，仅用于零配置演示。",
                )
            ],
        )

    async def remediate(self, knowledge_point: dict, reason: str, evidence: list[dict]) -> RemediationDraft:
        del reason
        source_refs = self._point_sources(knowledge_point)
        if not source_refs:
            source_refs = [self._source_ref(item, self._chunks_for(item)[0]) for item in evidence if self._chunks_for(item)][:1]
        description = str(knowledge_point.get("description") or "").strip()
        title = str(knowledge_point.get("title") or "该知识点").strip()
        chunk_text = self._first_explanation(source_refs, evidence)
        explanation = chunk_text or description or f"先重新阅读「{title}」对应的原始资料，再尝试完成一道最小例题。"
        return RemediationDraft(
            knowledge_point_title=title,
            diagnosis=f"当前「{title}」的验收证据不足；本地模式无法判断具体迷思，请对照原始资料补上关键条件。",
            simpler_explanation=explanation[:500],
            analogy="先确认条件和结论，再把条件逐条代入例子；条件不满足时结论不能使用。",
            worked_example="从资料中选一个最小场景，逐条核对条件，再复述结论。",
            quick_check=f"不看资料，写下「{title}」成立需要满足的条件。",
            sources=source_refs,
        )

    # ------------------------------------------------------------------
    # Evidence helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _chunks_for(item: dict) -> list[dict]:
        for key in ("reconstruction_chunks", "learning_chunks", "chunks"):
            value = item.get(key)
            if value:
                return [chunk for chunk in value if isinstance(chunk, dict)]
        return []

    @staticmethod
    def _rank(item: dict) -> tuple[int, int]:
        level, kind = item.get("evidence_level"), item.get("type")
        if level == "classroom":
            return (1, 0)
        if level == "official" and kind in {"slides", "assignment", "note", "audio", "video"}:
            return (2, 0)
        if level == "official":
            return (3, 0)
        return (4, 0)

    @classmethod
    def _extract_points(cls, session: dict, evidence: list[dict]) -> list[KnowledgePointDraft]:
        selected: list[dict] = []
        seen_titles: set[str] = set()
        for item in sorted(evidence, key=cls._rank):
            chunks = cls._chunks_for(item)
            if not chunks:
                continue
            title = cls._extract_title(chunks, item, session)
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            selected.append(item)
            if len(selected) >= 5:
                break
        if not selected:
            return []

        points: list[KnowledgePointDraft] = []
        for item in selected:
            chunks = cls._chunks_for(item)
            title = cls._extract_title(chunks, item, session)
            sources = [cls._source_ref(item, chunks[0])]
            if len(chunks) > 1:
                sources.append(cls._source_ref(item, chunks[1]))
            description = " ".join(
                cls._clean_chunk_text(chunk.get("text") or "") for chunk in chunks[:2]
            )[:400]
            points.append(
                KnowledgePointDraft(
                    title=title,
                    description=description or f"从资料《{item.get('name', 'resource')}》中整理出的核心内容。",
                    prerequisites=[],
                    importance=cls._importance(item),
                    expected_mastery_level=2,
                    confidence=Confidence.INFERRED,
                    sources=sources,
                )
            )
        return points

    @staticmethod
    def _build_learning_path(points: list[KnowledgePointDraft]) -> list[LearningStepDraft]:
        return [
            LearningStepDraft(
                position=index,
                title=f"从零理解：{point.title}",
                brief_explanation=point.description[:180] or "对照原始资料，先找出关键条件。",
                full_explanation=point.description or "对照原始资料，逐条理解关键条件、结论和适用边界。",
                knowledge_point_titles=[point.title],
                estimated_minutes=max(5, point.importance * 3),
                confidence=Confidence.SUPPLEMENTARY,
                sources=list(point.sources),
            )
            for index, point in enumerate(points, start=1)
        ]

    @staticmethod
    def _importance(item: dict) -> int:
        level, kind = item.get("evidence_level"), item.get("type")
        if level == "classroom":
            return 5
        if level == "official" and kind in {"slides", "assignment"}:
            return 4
        if level == "official":
            return 3
        return 2

    @classmethod
    def _extract_title(cls, chunks: list[dict], item: dict, session: dict) -> str:
        stop_markers = ("[pdf page", "[ppt slide", "[this page", "[this slide")
        candidates: list[str] = []
        for chunk in chunks[:4]:
            text = str(chunk.get("text") or "")
            for part in re.split(r"[\n。！？!?；;]", text):
                part = cls._clean_chunk_text(part)
                low = part.lower()
                if not part or low.startswith(stop_markers):
                    continue
                heading = re.split(r"[:：–—-]", part, maxsplit=1)[0].strip()
                for candidate in (heading, part):
                    candidate = re.sub(r"^(第[一二三四五六七八九十\d]+[章节讲]|lecture\s*\d+|chapter\s*\d+)\s*[·:：\- ]*", "", candidate, flags=re.I).strip()
                    if 2 <= len(candidate) <= 64 and cls._looks_like_title(candidate):
                        candidates.append((len(candidate), candidate))
        if candidates:
            return min(candidates)[1]
        session_title = re.split(r"[·|–—-]", str(session.get("title") or ""))[-1].strip()
        fallback = str(item.get("name") or "").rsplit(".", 1)[0].strip()
        session_is_specific = cls._looks_like_title(session_title) and not cls._is_generic_session_title(session_title)
        if session_is_specific:
            return session_title[:64]
        if cls._looks_like_title(fallback):
            return fallback[:64]
        return fallback[:64] or session_title[:64]

    @staticmethod
    def _is_generic_session_title(value: str) -> bool:
        lowered = value.lower().strip()
        if re.fullmatch(r"(第[一二三四五六七八九十\d]+[课节讲]|lecture\s*\d+|chapter\s*\d+|session\s*\d+)", lowered):
            return True
        return lowered in {"新课堂", "未命名", "untitled session"}

    @staticmethod
    def _looks_like_title(value: str) -> bool:
        if not value:
            return False
        if len(value) > 64 or any(marker in value.lower() for marker in ("http", "page", "slide", "chunk")):
            return False
        return bool(re.search(r"[\u4e00-\u9fff]|[A-Za-z]{3,}", value))

    @staticmethod
    def _clean_chunk_text(text: str) -> str:
        lines = [re.sub(r"^\[(PDF page|PPT slide)[^\]]*\]$", "", line.strip()) for line in text.splitlines()]
        return " ".join(line.strip() for line in lines if line.strip())

    @classmethod
    def _source_ref(cls, item: dict, chunk: dict) -> SourceRef:
        locator_type = chunk.get("locator_type")
        page = chunk.get("page")
        slide = chunk.get("slide")
        if locator_type == "page" and page:
            return SourceRef(
                resource_id=item["id"],
                label=str(item.get("name") or "resource"),
                locator=f"第 {page} 页",
                locator_type="page",
                page=int(page),
            )
        if locator_type == "slide" and slide:
            return SourceRef(
                resource_id=item["id"],
                label=str(item.get("name") or "resource"),
                locator=f"第 {slide} 页",
                locator_type="slide",
                slide=int(slide),
            )
        return SourceRef(
            resource_id=item["id"],
            label=str(item.get("name") or "resource"),
            locator=f"chunk {str(chunk.get('id'))[:8]}",
            locator_type="chunk",
            chunk_id=str(chunk.get("id")),
        )

    @classmethod
    def _point_sources(cls, point: dict) -> list[SourceRef]:
        raw_sources = point.get("sources") or []
        sources: list[SourceRef] = []
        for source in raw_sources:
            try:
                sources.append(SourceRef.model_validate(source))
            except Exception:
                continue
        return sources[:2]

    @staticmethod
    def _first_explanation(source_refs: list[SourceRef], evidence: list[dict]) -> str:
        chunk_ids = {source.chunk_id for source in source_refs if source.chunk_id}
        for item in evidence:
            for chunk in item.get("retrieved_chunks", item.get("chunks", [])):
                if chunk.get("id") in chunk_ids:
                    text = " ".join(
                        line.strip()
                        for line in re.split(r"[\n]", chunk.get("text") or "")
                        if line.strip()
                    )
                    return text[:500]
        return ""

    @classmethod
    def _question_point_title(cls, question: dict) -> str:
        titles = question.get("knowledge_point_titles") or []
        if titles:
            return str(titles[0])
        prompt = str(question.get("prompt") or "")
        match = re.search(r"「(.+?)」", prompt)
        if match:
            return match.group(1)
        return prompt[:80] or "Knowledge Point"

    @staticmethod
    def _tokens(text: str) -> set[str]:
        lowered = text.lower()
        tokens = set(re.findall(r"[a-z0-9]{2,}", lowered))
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
        tokens.update(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
        if 0 < len(chinese) <= 18:
            tokens.update(chinese)
        return tokens

    @staticmethod
    def _chars(text: str) -> set[str]:
        return set(re.findall(r"[\u4e00-\u9fff]", text.lower()))

    @classmethod
    def _coverage(cls, reference: str, answer: str) -> float:
        ref_tokens = cls._tokens(reference)
        if not ref_tokens:
            return 0.0
        token_overlap = len(cls._tokens(answer) & ref_tokens) / len(ref_tokens)
        ref_chars = cls._chars(reference)
        char_overlap = len(cls._chars(answer) & ref_chars) / len(ref_chars) if ref_chars else token_overlap
        return 0.6 * token_overlap + 0.4 * char_overlap

    @staticmethod
    def _looks_empty(answer: str) -> bool:
        lowered = answer.strip().lower()
        if len(lowered) < 8:
            return True
        return any(
            phrase in lowered
            for phrase in ("不知道", "不会", "不懂", "没学", "i don't know", "no idea", "idk")
        )


class LocalASRProvider:
    """Explicit no-op ASR fallback for zero-config local mode.

    Local audio transcription is intentionally not faked. The provider surfaces a
    clear configuration error instead of pretending that speech was transcribed.
    """

    requires_external_upload = False
    name = "local_rule"

    async def transcribe(self, path: str, mime_type: str | None) -> list[TranscriptSegment]:
        del path, mime_type
        raise ProviderNotConfigured(
            "本地模式不包含语音转写引擎。请在 .env 中配置 OPENAI_API_KEY，"
            "或将 KNOWLEDGEDEBT_ASR_PROVIDER 指向可用的 ASR Provider。"
        )
