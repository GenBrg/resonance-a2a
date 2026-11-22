"""Wire the configured engine."""

from __future__ import annotations

import logging
from functools import lru_cache

from app.config import settings
from app.llm.base import ChatModel
from app.llm.deterministic import DeterministicEngine
from app.llm.engine import LiveEngine
from app.llm.openai_compat import OpenAICompatModel, price_of

logger = logging.getLogger("resonance.llm")


@lru_cache
def chat_model() -> ChatModel | None:
    if not settings.llm_enabled:
        return None
    return OpenAICompatModel()


@lru_cache
def engine():
    model = chat_model()
    if model is None:
        logger.info({"event": "engine_selected", "engine": "deterministic", "reason": "no api key configured"})
        return DeterministicEngine()
    logger.info(
        {
            "event": "engine_selected",
            "engine": "live",
            "base_url": settings.llm_base_url,
            "model_small": settings.llm_model_small,
            "model_large": settings.llm_model_large,
        }
    )
    return LiveEngine(model)


def reset_engine() -> None:
    engine.cache_clear()
    chat_model.cache_clear()


__all__ = ["engine", "chat_model", "reset_engine", "price_of"]
