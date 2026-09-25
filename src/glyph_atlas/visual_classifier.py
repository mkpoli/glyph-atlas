"""A family-conditioned shape classifier over learned image embeddings.

Its first output is a visual group. A written character is returned only when
that group has explicit visual anchors and the image lies inside its measured
support. Similarity and margin are distances, never calibrated probabilities.
Each classifier keeps the encoder its groups were built with, beside them.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from .classify import SIZE, crop_array


class VisualClassifier:
    def __init__(self, directory: Path, *, session: Any | None = None):
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
        self._session = session if session is not None else self._build_session()

    def _build_session(self) -> Any:
        import onnxruntime as ort

        path = self.directory / "encoder.onnx"
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != self.metadata.get("encoder_sha256"):
            raise ValueError("Visual encoder does not match the groups it built")
        available = ort.get_available_providers()
        providers = ["CPUExecutionProvider"]
        if "CUDAExecutionProvider" in available:
            preload = getattr(ort, "preload_dlls", None)
            if preload is not None:
                try:
                    preload()
                except (ImportError, OSError, RuntimeError):
                    pass  # the CUDA provider then fails and the CPU provider serves
            providers = [("CUDAExecutionProvider", {"gpu_mem_limit": 512 * 1024 * 1024,
                                                   "arena_extend_strategy": "kSameAsRequested"}),
                         "CPUExecutionProvider"]
        options = ort.SessionOptions()
        options.log_severity_level = 3
        return ort.InferenceSession(str(path), sess_options=options, providers=providers)

    def embed(self, image) -> np.ndarray:
        """The normalized visual embedding of one crop, in the groups' own feature space."""
        input = self._session.get_inputs()[0]
        side = input.shape[2] if isinstance(input.shape[2], int) else SIZE
        pixels = crop_array(image, size=side)
        values = self._session.run(["features"], {input.name: pixels})[0]
        return np.asarray(values, dtype=np.float32).reshape(-1)

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
