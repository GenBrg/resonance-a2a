"""Resonance — agent-relay platform for NeurIPS 2026.

Package layout:
  app/core      primitives: crypto, auth, rate limit, logging, errors
  app/domain    pure logic: taxonomy, k-anonymity, redaction, sandbox, scoring
  app/llm       model access: OpenAI-compatible client + deterministic engine
  app/services  orchestration that touches the database
  app/api       HTTP transport
"""

__version__ = "1.0.0"
