"""The shape tree: encoder features to ten UMAP dimensions (McInnes, Healy & Melville 2018,
arXiv:1802.03426), then Ward linkage over every crop. Leaves are the tree cut into `LEAVES` groups and
numbered from 1 in the order SciPy's `fcluster` gives them."""
from __future__ import annotations

import numpy as np


def centred(features: np.ndarray) -> np.ndarray:
    x = features - features.mean(0)
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def build(features: np.ndarray, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Ten-dimensional coordinates and the Ward linkage over them."""
    import umap
    from scipy.cluster.hierarchy import linkage

    y = umap.UMAP(n_components=10, n_neighbors=30, min_dist=0.0, metric="cosine", random_state=seed).fit_transform(centred(features))
    return y, linkage(y, "ward")


def cut(z: np.ndarray, k: int) -> np.ndarray:
    from scipy.cluster.hierarchy import fcluster

    return fcluster(z, k, "maxclust")
