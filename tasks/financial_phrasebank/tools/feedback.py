"""Analyzer 的覆盖统计、未覆盖文本分组和开发历史；不返回测试信息。"""

import ast
from collections import Counter
import csv
import difflib
import json
import os
from pathlib import Path
import re

import numpy as np


def coverage_details(names, matrices):
    """独有覆盖表示仅该 LF 投票；重叠区分同票和冲突，不以重叠判定规则无用。"""
    details = {}
    for split, matrix in matrices.items():
        active = matrix != -1
        counts = active.sum(axis=1)
        n = len(matrix)
        per_lf = []
        for i, name in enumerate(names):
            exclusive = int((active[:, i] & (counts == 1)).sum())
            per_lf.append({
                "lf_name": name, "votes": int(active[:, i].sum()),
                "exclusive_votes": exclusive, "exclusive_coverage": exclusive / n if n else 0.0,
            })
        pairs = []
        for i, name in enumerate(names):
            for j in range(i + 1, len(names)):
                overlap = active[:, i] & active[:, j]
                count = int(overlap.sum())
                if count:
                    pairs.append({
                        "lfs": [name, names[j]], "overlap_votes": count,
                        "agreement_votes": int((overlap & (matrix[:, i] == matrix[:, j])).sum()),
                        "conflict_votes": int((overlap & (matrix[:, i] != matrix[:, j])).sum()),
                    })
        details[split] = {
            "n_samples": n, "n_covered": int((counts > 0).sum()),
            "single_lf_samples": int((counts == 1).sum()),
            "multiple_lf_samples": int((counts > 1).sum()),
            "per_lf": per_lf,
            "overlapping_pairs": sorted(pairs, key=lambda item: (-item["overlap_votes"], item["lfs"])),
        }
    return details


def group_uncovered(rows, matrix, names, max_groups=20, representatives=2, seed=42):
    """最大组优先二分；空特征文本隔离，代表句兼顾中心性和差异性。"""
    indices = np.flatnonzero((matrix == -1).all(axis=1)).tolist()
    if not indices or not max_groups:
        return []
    texts = [re.sub(r"\d+(?:[.,]\d+)*", " num ", rows[i]["text"].lower()) for i in indices]
    from sklearn.cluster import BisectingKMeans
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
    from threadpoolctl import threadpool_limits

    # 新闻套话、币种和数值单位容易淹没句式/业务主题，不用于划分主题。
    stop_words = sorted(ENGLISH_STOP_WORDS | {
        "num", "company", "said", "group", "finnish", "finland", "oyj", "plc",
        "eur", "mn", "mln", "million", "billion",
    })
    vectorizer = TfidfVectorizer(stop_words=stop_words, ngram_range=(1, 2), max_features=5000, sublinear_tf=True)
    try:
        features = vectorizer.fit_transform(texts)
    except ValueError as error:
        if "empty vocabulary" not in str(error):
            raise
        # 全是停用词等极小样例仍保留所有样本规模，不伪造主题。
        features = None
        assignments = np.zeros(len(indices), dtype=int)
        centers = {}
    else:
        usable = np.flatnonzero(features.getnnz(axis=1) > 0)
        empty = np.flatnonzero(features.getnnz(axis=1) == 0)
        subset = features[usable].copy()
        subset.sort_indices()
        distinct = len({tuple(zip(subset[i].indices, subset[i].data)) for i in range(len(usable))})
        # 有预算时，空特征样本独立成组，不能成为大主题组的代表。
        n_groups = min(max_groups - int(bool(len(empty)) and max_groups > 1), distinct)
        assignments = np.zeros(len(indices), dtype=int)
        if n_groups <= 1:
            centers = {0: np.asarray(subset.mean(axis=0)).ravel()}
        else:
            model = BisectingKMeans(
                n_clusters=n_groups, bisecting_strategy="largest_cluster",
                random_state=seed, n_init=3, max_iter=100,
            )
            # 分组固定使用一个线程，结束后恢复进程配置，避免影响后续模型运行。
            previous_limit = os.environ.get("LOKY_MAX_CPU_COUNT")
            os.environ["LOKY_MAX_CPU_COUNT"] = "1"
            try:
                with threadpool_limits(limits=1):
                    assignments[usable] = model.fit_predict(subset)
            finally:
                if previous_limit is None:
                    os.environ.pop("LOKY_MAX_CPU_COUNT", None)
                else:
                    os.environ["LOKY_MAX_CPU_COUNT"] = previous_limit
            centers = dict(enumerate(model.cluster_centers_))
        if len(empty) and max_groups > 1:
            assignments[empty] = n_groups

    counts = Counter(assignments.tolist())
    groups = []
    for rank, (cluster, count) in enumerate(sorted(counts.items(), key=lambda item: (-item[1], item[0])), 1):
        members = np.flatnonzero(assignments == cluster).tolist()
        usable_members = [i for i in members if features is not None and features[i].nnz]
        keywords, cohesion = [], None
        if usable_members:
            center = centers[cluster]
            terms = vectorizer.get_feature_names_out()
            top = [i for i in np.argsort(-center, kind="stable") if center[i] > 0]
            keywords = [str(terms[i]) for i in top[:6]]
            norm = np.linalg.norm(center)
            similarities = np.asarray(features[usable_members] @ center).ravel() / norm if norm else np.zeros(len(usable_members))
            cohesion = float(similarities.mean())
            chosen_positions = [int(np.argmax(similarities))] if representatives else []
            while len(chosen_positions) < min(representatives, len(usable_members)):
                selected = [usable_members[i] for i in chosen_positions]
                overlap = (features[usable_members] @ features[selected].T).toarray().max(axis=1)
                scores = .4 * similarities + .6 * (1 - overlap)
                scores[chosen_positions] = -np.inf
                chosen_positions.append(int(np.argmax(scores)))
            chosen = [usable_members[i] for i in chosen_positions]
        else:
            chosen = members[:representatives]
        examples = []
        for position, local_index in enumerate(chosen):
            index = indices[local_index]
            examples.append({
                "id": rows[index]["id"], "text": rows[index]["text"],
                "votes": dict(zip(names, matrix[index].tolist())),
                "selection": "central" if position == 0 and usable_members else "diverse" if usable_members else "lexical_empty",
            })
        groups.append({
            "group_id": f"uncovered_{rank}", "n_samples": count,
            "share_of_uncovered": count / len(indices),
            "kind": "text_similarity" if usable_members else "lexical_empty",
            "lexical_empty_samples": len(members) - len(usable_members),
            "mean_cosine_similarity": cohesion,
            "keywords": keywords, "examples": examples,
        })
    return groups


def previously_covered_examples(folder, current_version, rows, matrix, names, rounds=5, limit=10):
    """展示当前未覆盖、近期曾有票的未标注文本；旧票是回归线索，不是金标。"""
    if folder is None or not rounds or not limit:
        return {"n_samples": 0, "examples": []}
    uncovered = {rows[i]["id"]: i for i in np.flatnonzero((matrix == -1).all(axis=1))}
    number = int(current_version[1:])
    found = {}
    for n in range(number - 1, max(0, number - rounds - 1), -1):
        path = Path(folder) / f"versions/v{n}/results/lf_votes.csv"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for record in csv.DictReader(stream):
                if record["split"] != "unlabeled" or record["id"] not in uncovered or record["id"] in found:
                    continue
                votes = {key: int(value) for key, value in record.items() if key not in ("id", "split") and value != "-1"}
                if votes:
                    index = uncovered[record["id"]]
                    found[record["id"]] = {
                        "id": record["id"], "text": rows[index]["text"],
                        "previous_version": f"v{n}", "previous_votes": votes,
                        "current_votes": dict(zip(names, matrix[index].tolist())),
                    }
    return {"n_samples": len(found), "examples": list(found.values())[:limit]}


def _lf_sources(path):
    code = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(code)
    names = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "LFS" for t in node.targets):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                names = [item.id for item in node.value.elts if isinstance(item, ast.Name)]
    sources = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            source = ast.get_source_segment(code, node)
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body.pop(0)
            sources[node.name] = (ast.dump(node, include_attributes=False), source)
    return sources


def _changes(previous, current):
    before, after = _lf_sources(previous), _lf_sources(current)
    changes = []
    for name in dict.fromkeys([*before, *after]):
        if name not in before:
            action = "add"
        elif name not in after:
            action = "delete"
        elif before[name][0] != after[name][0]:
            action = "modify"
        else:
            continue
        diff = "\n".join(difflib.unified_diff(
            before.get(name, ("", ""))[1].splitlines(), after.get(name, ("", ""))[1].splitlines(),
            fromfile="previous", tofile="current", lineterm="", n=1,
        ))
        changes.append({"action": action, "target": name, "diff": diff[:1600], "diff_truncated": len(diff) > 1600})
    return changes


def _development_summary(metrics):
    """明确白名单；不复制 metrics.test、标签模型或任意历史报告正文。"""
    labeled = metrics["labeled"]
    return {
        "labeled": {
            **{key: labeled[key] for key in ("n_samples", "coverage", "correct", "incorrect_labeled", "n_abstained")},
            "accuracy": labeled["all_samples"]["accuracy"],
            "macro_f1": labeled["all_samples"]["macro_f1"],
        },
        "unlabeled": {key: metrics["unlabeled"][key] for key in ("n_samples", "lf_coverage", "coverage", "conflict_rate")},
    }


def load_recent_history(folder, current_version, rounds=5):
    """只读取当前版本及以前的相邻转移；缺失历史跳过，绝不包含未来版本或测试指标。"""
    if rounds < 0:
        raise ValueError("历史轮数不能为负数")
    if folder is None or not rounds:
        return []
    if not re.fullmatch(r"v[1-9]\d*", current_version):
        raise ValueError("历史查询版本名称无效")
    versions = Path(folder) / "versions"
    number = int(current_version[1:])
    history = []
    for n in range(max(2, number - rounds + 1), number + 1):
        previous, current = versions / f"v{n - 1}", versions / f"v{n}"
        paths = [previous / "lfs.py", current / "lfs.py", previous / "results/metrics.json", current / "results/metrics.json"]
        if not all(path.is_file() for path in paths):
            continue
        old_metrics, metrics = [json.loads(path.read_text(encoding="utf-8")) for path in paths[2:]]
        if old_metrics["lf_version"] != previous.name or metrics["lf_version"] != current.name:
            raise ValueError("历史指标与版本目录不一致")
        before, after = _development_summary(old_metrics), _development_summary(metrics)
        delta_keys = {"labeled": ("coverage", "accuracy", "macro_f1"), "unlabeled": ("lf_coverage", "coverage", "conflict_rate")}
        history.append({
            "previous_version": previous.name, "version": current.name,
            "changes": _changes(paths[0], paths[1]),
            "development_metrics": after,
            "delta": {split: {key: after[split][key] - before[split][key] for key in keys} for split, keys in delta_keys.items()},
        })
    return history
