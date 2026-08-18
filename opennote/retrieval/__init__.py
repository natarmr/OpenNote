"""Retrieval subsystem: retrieve + cite sources, LLM-free.

This is the "R" of RAG. The LLM generation layer (Phase 4) consumes the
``SearchResult`` objects produced here, so this module is the seam where a
BYOK model plugs in later.
"""