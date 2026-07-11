"""Candidate-only technical signatures.

These detectors feed replay and the judgment ledger.  They are deliberately
not imported by presentation, alert, briefing, or gauge modules.
"""

from app.structure.candidates.engine import detect_candidate_signatures, detect_stage2_template

__all__ = ["detect_candidate_signatures", "detect_stage2_template"]
