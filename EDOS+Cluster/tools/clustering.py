"""按金标发现未覆盖模式，生成可追溯的结构化案例；不接收选择集或测试集。"""

from dataclasses import dataclass
import hashlib
import math
import random

import numpy as np
from sklearn.cluster import AgglomerativeClustering

from .task import CARDINALITY, label_id


MODES = ("random", "cluster_random", "cluster_diverse")


@dataclass(frozen=True)
class ClusterConfig:
    mode: str
    distance_threshold: float
    min_cluster_size: int
    train_similarity_threshold: float
    center_count: int
    diverse_count: int
    boundary_count: int
    negative_count: int
    train_example_count: int

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError(f"未知反馈模式：{self.mode}")
        if not math.isfinite(self.distance_threshold) or not 0 < self.distance_threshold < 2:
            raise ValueError("聚类余弦距离阈值须在 (0,2) 内")
        if not math.isfinite(self.train_similarity_threshold) or not -1 <= self.train_similarity_threshold <= 1:
            raise ValueError("训练支持相似度须在 [-1,1] 内")
        if type(self.min_cluster_size) is not int or self.min_cluster_size < 2:
            raise ValueError("每簇至少需要两条独立金标案例")
        for key in ("center_count", "diverse_count", "boundary_count", "negative_count", "train_example_count"):
            if type(getattr(self, key)) is not int or getattr(self, key) < 0:
                raise ValueError(f"{key} 必须为非负整数")
        if self.center_count < 1 or self.positive_count < 2:
            raise ValueError("至少需要一个中心和两条金标案例")

    @property
    def positive_count(self):
        return self.center_count + self.diverse_count + self.boundary_count


def abstention_masks(matrix, predictions):
    matrix, predictions = np.asarray(matrix), np.asarray(predictions)
    uncovered = (matrix == -1).all(axis=1)
    return {"all_abstain": uncovered, "tied": (~uncovered) & (predictions == -1)}


def _center(vectors):
    center = vectors.mean(axis=0)
    norm = np.linalg.norm(center)
    return center / norm if norm > 1e-8 else vectors[0]


def _signature(label, rows, indices):
    ids = sorted(rows[i]["id"] for i in indices)
    return f"label_{label}_" + hashlib.sha256("\n".join(ids).encode()).hexdigest()[:16]


def _unique(rows, indices, used=None, limit=None):
    used = set() if used is None else used
    result = []
    if limit == 0:
        return result
    for i in indices:
        key = " ".join(rows[i]["text"].casefold().split())
        if key not in used:
            result.append(int(i))
            used.add(key)
            if limit is not None and len(result) >= limit:
                break
    return result


def build_cluster(result, vectors, config, *, seed, round_index, label_attempts, excluded=()):
    """result 只包含发现集 valid 和无金标 train；返回案例组及无正文目录。"""
    valid, train = result["valid"], result["train"]
    rows, train_rows = valid["rows"], train["rows"]
    v, t = np.asarray(vectors["valid"]), np.asarray(vectors["train"])
    if v.shape[0] != len(rows) or t.shape[0] != len(train_rows) or v.shape[1] != t.shape[1]:
        raise ValueError("向量行数/维数与当前训练、发现集不一致")
    masks = abstention_masks(valid["L"], valid["predictions"])
    train_mask = (np.asarray(train["L"]) == -1).all(axis=1)
    train_indices = np.flatnonzero(train_mask)
    gold = np.array([label_id(row["label"]) for row in rows])
    candidates, catalog = [], []
    rng = random.Random(f"{seed}:{round_index}:{config.mode}")
    for label in range(CARDINALITY):
        indices = sorted(np.flatnonzero(masks["all_abstain"] & (gold == label)), key=lambda i: rows[i]["id"])
        indices = _unique(rows, indices)
        if len(indices) < config.min_cluster_size:
            continue
        if config.mode == "random":
            members = sorted(rng.sample(indices, min(config.positive_count, len(indices))))
            groups = [members]
        else:
            assignments = AgglomerativeClustering(
                n_clusters=None, metric="cosine", linkage="average",
                distance_threshold=config.distance_threshold, compute_full_tree=True,
            ).fit_predict(v[indices])
            groups = [[indices[i] for i in np.flatnonzero(assignments == k)] for k in sorted(set(assignments))]
        for members in groups:
            if len(members) < config.min_cluster_size:
                continue
            members = np.asarray(members, dtype=int)
            signature = _signature(label, rows, members)
            center = _center(v[members])
            cohesion = float(np.mean(v[members] @ center))
            similarities = t[train_indices] @ center
            support = train_indices[similarities >= config.train_similarity_threshold]
            score = math.log1p(len(members)) * math.log1p(len(support)) * max(0, cohesion)
            meta = {"cluster_id": signature, "target_label": label,
                    "valid_uncovered_count": int(len(members)),
                    "train_similar_uncovered_count": int(len(support)),
                    "cohesion": cohesion, "priority_score": score,
                    "already_attempted": signature in excluded}
            catalog.append(meta)
            if signature not in excluded:
                candidates.append((meta, members, center, support))
    if not candidates:
        return None, {"eligible_clusters": catalog, "reason": "no_eligible_cluster",
                      "valid_all_abstain": int(masks["all_abstain"].sum()),
                      "train_all_abstain": int(train_mask.sum())}
    # 每类内部按价值排序，类间优先分配尝试次数较少的标签；不以多数类数量支配调度。
    available_labels = {item[0]["target_label"] for item in candidates}
    selected_label = min(available_labels, key=lambda value: (label_attempts.get(str(value), 0), value))
    candidates = [item for item in candidates if item[0]["target_label"] == selected_label]
    candidates.sort(key=lambda x: (-x[0]["priority_score"], -x[0]["valid_uncovered_count"], x[0]["cluster_id"]))
    meta, members, center, support = candidates[0]
    label = meta["target_label"]
    other = np.flatnonzero(gold != label)
    nearest_other = (v[members] @ v[other].T).max(axis=1) if len(other) else np.full(len(members), -1.)
    categories = {key: [] for key in ("center_examples", "diverse_examples", "boundary_examples", "random_examples")}
    if config.mode != "cluster_diverse":
        categories["random_examples"] = rng.sample(members.tolist(), min(config.positive_count, len(members)))
    else:
        centrality = v[members] @ center
        chosen = members[np.argsort(-centrality, kind="stable")[:config.center_count]].tolist()
        categories["center_examples"] = chosen.copy()
        remaining = [int(i) for i in members if i not in chosen]
        diverse_count = min(config.diverse_count, max(0, len(remaining) - config.boundary_count))
        for _ in range(diverse_count):
            distances = 1 - (v[remaining] @ v[chosen].T).max(axis=1)
            pick = remaining.pop(int(np.argmax(distances)))
            chosen.append(pick)
            categories["diverse_examples"].append(pick)
        # 本类簇中心相似度与异类最近邻相似度之差越小，边界越难。
        margin = {int(i): float(centrality[j] - nearest_other[j]) for j, i in enumerate(members)}
        categories["boundary_examples"] = sorted(remaining, key=lambda i: (margin[i], rows[i]["id"]))[:config.boundary_count]
    positive = [i for values in categories.values() for i in values]
    used = {" ".join(rows[i]["text"].casefold().split()) for i in positive}
    # 三种模式使用同一反例检索算法和数量预算。
    similarity = (v[other] @ v[positive].T).max(axis=1) if len(other) else np.array([])
    negatives = _unique(rows, other[np.argsort(-similarity, kind="stable")], used, config.negative_count)
    support_scores = t[support] @ center
    training = _unique(train_rows, support[np.argsort(-support_scores, kind="stable")], used, config.train_example_count)

    def example(i, *, training=False):
        split = train if training else valid
        row = split["rows"][i]
        item = {"id": row["id"], "text": row["text"],
                "source": "train_unlabeled" if training else "discovery_gold",
                "votes": {name: int(vote) for name, vote in zip(result["lf_names"], split["L"][i]) if vote != -1},
                "similarity_to_center": float((t if training else v)[i] @ center)}
        if not training:
            item.update(gold=int(gold[i]), prediction=int(valid["predictions"][i]))
        return item

    cluster = {**meta, "mode": config.mode,
               "train_support_similarity_threshold": config.train_similarity_threshold,
               "train_support_is_label_evidence": False,
               "train_support_counts_may_overlap_between_clusters": True,
               **{key: [example(i) for i in ids] for key, ids in categories.items()},
               "hard_negative_examples": [example(i) for i in negatives],
               "train_support_examples": [example(i, training=True) for i in training]}
    return cluster, {"eligible_clusters": catalog, "selected_cluster_id": meta["cluster_id"],
                     "small_clusters_are_deferred": True}
