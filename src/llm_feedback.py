"""LLM-powered self-learning from human feedback.

When an operator dismisses a mention (clicks 'Not relevant'), this module:
  1. Asks the LLM to analyze WHY it was a false positive.
  2. Extracts a concrete rule (lesson) the agent should apply in future.
  3. Saves the lesson to DB via save_feedback_lesson().

Lessons are then injected into the system prompt of every future analysis run,
making the agent progressively better at filtering irrelevant content.
"""

import json
import logging
from typing import Optional

from src.settings import Settings

logger = logging.getLogger(__name__)

FALSE_POSITIVE_PROMPT = """
You are a quality control analyst for a brand monitoring AI system.

The AI agent was monitoring mentions of the company "{brand}".
Description: {company_description}
Location: {location}
Keywords tracked: {keywords}

The AI incorrectly flagged the following content as relevant
and sent an alert. A human operator dismissed it as NOT relevant.

Dismissed content:
  Source: {source_name}
  URL: {url}
  Text snippet:
  ---
  {text_snippet}
  ---
{operator_explanation_block}
Your task:
1. Analyze WHY this was a false positive.
   {operator_hint}
2. Identify the error category (choose ONE):
   - "not_our_company" — completely different company or person with same/similar name
   - "old_publication" — content is too old to be actionable (operator marked as outdated)
   - "topic_mismatch" — content doesn't match the monitored search topic
   - "keyword_coincidence" — keyword appeared but in unrelated context
   - "wrong_location" — mentions a company with same/similar name in a different city
   - "other" — doesn't fit above categories
3. Write a short, concrete rule in Russian (1-2 sentences) that the agent
   should follow in future to AVOID making this mistake again.
   The rule should be specific and actionable, not generic.
   Good: "Не считать релевантными упоминания квест-комнаты 'Погружение' из Санкт-Петербурга — наша компания находится в Москве."
   Bad: "Быть внимательнее к контексту."

Return ONLY valid JSON:
{{
  "error_category": "not_our_company" | "old_publication" | "topic_mismatch" | "keyword_coincidence" | "wrong_location" | "other",
  "lesson_text": "<rule in Russian, 1-2 sentences>",
  "explanation": "<brief explanation of the mistake in Russian, 1 sentence>"
}}
"""


async def analyze_false_positive(
    settings: Settings,
    mention_id: str,
    url: Optional[str],
    source_name: Optional[str],
    text: Optional[str],
    user_explanation: Optional[str] = None,
) -> Optional[dict]:
    """Ask LLM to analyze a dismissed mention and extract a lesson.

    Args:
        user_explanation: Optional free-text reason provided by the human operator
                          via Telegram dialog. Greatly improves lesson quality when present.

    Returns a dict with keys: error_category, lesson_text, explanation.
    Returns None on failure (non-critical — lesson extraction is best-effort).
    """
    if not settings.llm_api_key:
        logger.warning("LLM_API_KEY not set, skipping feedback analysis")
        return None

    text_snippet = (text or "")[:600].strip()
    if not text_snippet:
        logger.warning("No text available for feedback analysis of mention %s", mention_id)
        return None

    # Build optional operator explanation block for the prompt
    if user_explanation and user_explanation.strip():
        operator_explanation_block = (
            f"\nOperator's explanation (WHY this is not relevant):\n"
            f"  \"{user_explanation.strip()}\"\n"
        )
        operator_hint = (
            "Pay close attention to the operator's explanation above — "
            "it is the primary source of truth for understanding the error."
        )
    else:
        operator_explanation_block = ""
        operator_hint = "Infer the reason from the content and company context."

    prompt = FALSE_POSITIVE_PROMPT.format(
        brand=settings.company_name,
        company_description=settings.company_description or "Not provided",
        location=settings.monitoring_location,
        keywords=", ".join(settings.keyword_list[:10]),
        source_name=source_name or "unknown",
        url=url or "no url",
        text_snippet=text_snippet,
        operator_explanation_block=operator_explanation_block,
        operator_hint=operator_hint,
    )

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_endpoint,
        )
        resp = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=400,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        result = json.loads(raw)

        lesson = result.get("lesson_text", "").strip()
        category = result.get("error_category", "other").strip()
        explanation = result.get("explanation", "").strip()

        if not lesson:
            logger.warning("LLM returned empty lesson for mention %s", mention_id)
            return None

        logger.info(
            "Feedback lesson extracted [%s]: %s (mention=%s)",
            category, lesson, mention_id,
        )
        return {
            "error_category": category,
            "lesson_text": lesson,
            "explanation": explanation,
        }
    except Exception as e:
        logger.error("Failed to analyze false positive for mention %s: %s", mention_id, e)
        return None
