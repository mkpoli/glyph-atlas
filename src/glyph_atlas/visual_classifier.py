"""A family-conditioned shape classifier over learned image embeddings.

Its first output is a visual group. A written character is returned only when
that group has explicit visual anchors and the image lies inside its measured
support. Similarity and margin are distances, never calibrated probabilities.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np


class VisualClassifier:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.metadata = json.loads((self.directory / "classifier.json").read_text())
        # One atomic file keeps labels and vectors from different revisions apart.
        self.centroids = np.asarray(self.metadata["centroids"], dtype=np.float32)
        self.radii = np.asarray(self.metadata["radii"], dtype=np.float32)
        self.groups = self.metadata["groups"]
        if (self.centroids.ndim != 2 or self.radii.shape != (len(self.centroids),)
                or len(self.groups) != len(self.centroids)
                or not np.isfinite(self.centroids).all() or not np.isfinite(self.radii).all()
                or not np.allclose(np.linalg.norm(self.centroids, axis=1), 1, atol=1e-4)
                or np.any((self.radii < 0) | (self.radii > .35))):
            raise ValueError("Visual classifier metadata and prototypes disagree")

    def predict(self, embedding: np.ndarray, family: str) -> dict:
        vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if vector.shape != self.centroids.shape[1:] or not np.isfinite(vector).all() or norm < 1e-9:
            raise ValueError("Invalid visual embedding")
        indexes = [i for i, group in enumerate(self.groups) if group["family"] == family]
        if not indexes:
            return {"status": "not_analyzed", "family": family, "written_character": None}
        similarities = self.centroids[indexes] @ (vector / norm)
        order = np.argsort(-similarities, kind="stable")
        best = indexes[int(order[0])]
        similarity = float(similarities[order[0]])
        margin = float(similarity - similarities[order[1]]) if len(order) > 1 else 1.0
        supported = 1 - similarity <= float(self.radii[best]) and margin >= .025
        group = self.groups[best]
        # A group whose assignment an inspected counterexample withheld proposes nothing.
        enabled = group.get("assignment_enabled", True)
        written = group.get("written_character") if supported and enabled else None
        return {"family": family, "status": "proposed" if written else "unassigned",
                "group_id": group["id"], "written_character": written,
                "similarity": round(similarity, 6), "margin": round(margin, 6),
                "within_support": supported, "verified": False,
                "assignment_enabled": group.get("assignment_enabled", True),
                "model_revision": self.metadata["model_revision"],
                "basis": "visual_model"}
