"""固定模型生成正文向量；缓存绑定样本顺序、内容和完整编码配置。"""

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import numpy as np


@dataclass(frozen=True)
class EmbeddingConfig:
    model: str
    revision: str
    device: str
    batch_size: int
    max_seq_length: int
    local_files_only: bool

    def __post_init__(self):
        if not self.model or not self.revision or self.device not in {"auto", "cuda", "cpu"}:
            raise ValueError("Embedding 模型/revision 不能为空；device 必须是 auto/cuda/cpu")
        if type(self.batch_size) is not int or self.batch_size < 1 or type(self.max_seq_length) is not int or self.max_seq_length < 1:
            raise ValueError("Embedding batch_size/max_seq_length 必须为正整数")
        if type(self.local_files_only) is not bool:
            raise ValueError("local_files_only 必须是布尔值")


class EmbeddingStore:
    def __init__(self, cache_dir, config):
        self.cache_dir = Path(cache_dir)
        self.config = config
        self.model = None

    def _load_model(self):
        if self.model is None:
            import torch
            from sentence_transformers import SentenceTransformer
            device = self.config.device
            if device == "auto":
                device = "cuda" if torch.cuda.is_available() else "cpu"
            if device == "cuda" and not torch.cuda.is_available():
                raise RuntimeError("已指定 CUDA，但当前解释器的 CUDA 不可用")
            # 普通目录快照避免 Windows Hub 符号链接探测所需的额外权限。
            model_source = Path(self.config.model)
            if not model_source.is_dir():
                from huggingface_hub import snapshot_download
                snapshot_key = hashlib.sha256((self.config.model + "@" + self.config.revision).encode()).hexdigest()[:20]
                model_source = self.cache_dir / "models" / snapshot_key
                marker = model_source / "snapshot_identity.json"
                identity = {"model": self.config.model, "revision": self.config.revision}
                if not marker.is_file():
                    snapshot_download(self.config.model, revision=self.config.revision, local_dir=str(model_source),
                                      local_files_only=self.config.local_files_only,
                                      allow_patterns=["*.json", "*.safetensors", "vocab.txt", "*.model", "merges.txt"])
                    if not (model_source / "modules.json").is_file():
                        raise RuntimeError("固定 Sentence-Transformers 快照不完整")
                    temporary_marker = marker.with_name(marker.name + "." + uuid4().hex + ".pending")
                    try:
                        temporary_marker.write_text(json.dumps(identity, sort_keys=True), encoding="utf-8")
                        temporary_marker.replace(marker)
                    finally:
                        temporary_marker.unlink(missing_ok=True)
                elif json.loads(marker.read_text(encoding="utf-8")) != identity:
                    raise ValueError("本地模型快照身份与配置不一致")
            self.model = SentenceTransformer(
                str(model_source.resolve()), device=device, local_files_only=True, trust_remote_code=False,
            )
            self.model.max_seq_length = self.config.max_seq_length
        return self.model

    def encode(self, rows, split):
        if not rows:
            raise ValueError("Embedding 输入不能为空")
        from importlib.metadata import version
        identity = {"protocol": 1, "split": split, "config": asdict(self.config),
                    "sentence_transformers_version": version("sentence-transformers"),
                    "transformers_version": version("transformers"),
                    "normalize_embeddings": True,
                    "rows_sha256": hashlib.sha256(json.dumps(
                        [[r["id"], r["text"]] for r in rows], ensure_ascii=False).encode("utf-8")).hexdigest()}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        output = self.cache_dir / f"{split}-{key}.npz"
        if output.exists():
            with np.load(output, allow_pickle=False) as cached:
                if json.loads(str(cached["metadata"])) != identity or cached["ids"].tolist() != [r["id"] for r in rows]:
                    raise ValueError("Embedding 缓存身份或样本顺序不一致")
                vectors = cached["vectors"].copy()
                stats = json.loads(str(cached["stats"]))
            self._validate(vectors, len(rows))
            return vectors, {**stats, "cache_hit": True, "cache_key": key}
        model = self._load_model()
        texts = [r["text"] for r in rows]
        # 显式记录截断数；不把被截断的输入假称为已完整编码。
        lengths = model.tokenizer(texts, truncation=False, padding=False, return_length=True)["length"]
        vectors = model.encode(texts, batch_size=self.config.batch_size, show_progress_bar=True,
                               convert_to_numpy=True, normalize_embeddings=True).astype(np.float32)
        self._validate(vectors, len(rows))
        stats = {"n_samples": len(rows), "dimensions": vectors.shape[1],
                 "truncated_samples": sum(n > self.config.max_seq_length for n in lengths)}
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # 同目录原子替换，避免中断留下可被读取的半份缓存。
        temporary = output.with_name(output.stem + "." + uuid4().hex + ".pending.npz")
        try:
            np.savez_compressed(temporary, vectors=vectors, ids=np.array([r["id"] for r in rows]),
                                metadata=json.dumps(identity, sort_keys=True), stats=json.dumps(stats))
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
        return vectors, {**stats, "cache_hit": False, "cache_key": key}

    @staticmethod
    def _validate(vectors, count):
        if (vectors.ndim != 2 or vectors.shape[0] != count or vectors.shape[1] < 1
                or not np.isfinite(vectors).all()
                or not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=2e-4)):
            raise ValueError("Embedding 须为有限、非零、按行归一化的二维向量")
