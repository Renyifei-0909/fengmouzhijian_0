from __future__ import annotations

from typing import Any

from ...models import DesignBaseline, EvidenceAsset


class StubAnalyzer:
    name = "stub"
    version = "stub-v0.1"

    def analyze(self, evidence: EvidenceAsset, baseline: DesignBaseline) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "analysis_mode": "stub",
            "evidence_grade": False,
            "analyzer": {"name": self.name, "version": self.version},
            "provenance": {
                "kind": "placeholder",
                "synthetic": False,
                "warning": "No computer-vision model is connected; no physical measurement or accuracy claim was produced.",
            },
            "input": {
                "evidence_sha256": evidence.sha256,
                "size_bytes": evidence.size_bytes,
                "content_type": evidence.content_type,
                "baseline_sha256": baseline.sha256,
            },
            "observations": {"measurements": {}, "objects": [], "events": []},
            "alignment": {
                "status": "not_evaluated",
                "baseline_version": baseline.version,
                "differences": [],
            },
            "findings": [
                {
                    "code": "MODEL_NOT_CONNECTED",
                    "severity": "info",
                    "message": "Evidence was accepted and hashed, but algorithmic verification still requires a real model adapter.",
                }
            ],
            "confidence": None,
            "accuracy_claim": None,
            "recommended_action": "manual_review",
        }
