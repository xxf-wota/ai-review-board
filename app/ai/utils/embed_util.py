"""
本地向量工具：把一句话变成向量，用来算语义相似度

为什么不用 Ollama 出向量（别改回去，这是实测结论）：
    这台机器上 Ollama 0.34.2 里装的 qwen2.5:7b / qwen3.5:9b 都是对话模型，
    调 POST /api/embed 直接 501 This server does not support embeddings，
    专用向量模型一个都没装；商业 embedding 也不可用（.env 里没有 DASHSCOPE_API_KEY）。

为什么用 ONNX 而不是 sentence-transformers：
    实验环境 F:\\models 下是 sentence-transformers 格式的权重，其中
    paraphrase-multilingual-MiniLM-L12-v2 自带 ONNX 导出（onnx/model.onnx），
    而本环境已经有 onnxruntime + numpy + tokenizers（现成的依赖，见 requirements.txt），
    于是可以直接跑，不必为了向量再装 torch（约 2.5GB）。

模型自己的配置决定怎么用它（modules.json + 1_Pooling/config.json）：
    输入 input_ids / attention_mask / token_type_ids
    输出 last_hidden_state [batch, seq, word_embedding_dimension]
    池化 mean tokens；modules.json 里没有 Normalize 模块，所以归一化在这里补
    归一化之后点积就是余弦，cosine() 因此只写一次点积，省掉开方和除法。

路径全部从 .env 读。读不到 / 加载失败时 available() 返回 False，
调用方应当退回字面相似度（app/ai/utils/text_util.py）—— 演示现场不能因为这个崩。
"""

import asyncio
import os
import threading

import numpy as np
from dotenv import load_dotenv

load_dotenv()

# 模型目录与导出文件。默认空串，等于"这台机器没有向量模型"，由 .env 指到本地权重
MODEL_DIR = os.getenv("EMBED_MODEL_DIR") or ""
ONNX_FILE = os.getenv("EMBED_ONNX_FILE") or "onnx/model.onnx"
# 池化方式：mean（MiniLM / gte 等）或 cls（bge 系列的中文模型常用）
POOLING = (os.getenv("EMBED_POOLING") or "mean").strip().lower()
# 一句话最多截到多少 token。评审问题都是短句，256 足够，还省时间
MAX_LEN = int(os.getenv("EMBED_MAX_LEN") or 256)

_backend = None
_backend_error = ""
_lock = threading.Lock()


class _Backend:
    """ONNX 会话 + 分词器。只在第一次用到时加载，之后常驻"""

    def __init__(self, model_dir: str) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        onnx_path = os.path.join(model_dir, ONNX_FILE)
        if not os.path.isfile(onnx_path):
            raise FileNotFoundError(f"ONNX 模型不存在：{onnx_path}")
        tokenizer_path = os.path.join(model_dir, "tokenizer.json")
        if not os.path.isfile(tokenizer_path):
            raise FileNotFoundError(f"分词器不存在：{tokenizer_path}")

        # 一个进程一个会话就够；线程数给 2，免得和其它模型抢 CPU
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        self.session = ort.InferenceSession(
            onnx_path, sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.input_names = {i.name for i in self.session.get_inputs()}
        self.tokenizer = Tokenizer.from_file(tokenizer_path)
        self.tokenizer.enable_truncation(max_length=MAX_LEN)
        # 补齐用的 pad id：XLM-R 是 <pad>=1，取不到就退回 0
        pad_id = self.tokenizer.token_to_id("<pad>")
        self.pad_id = 1 if pad_id is None else pad_id

    def encode(self, texts: list) -> np.ndarray:
        """一批文本 -> 归一化后的向量矩阵 (n, dim)"""
        texts = [t if isinstance(t, str) else str(t or "") for t in texts]
        encodings = self.tokenizer.encode_batch(texts)
        width = max((len(e.ids) for e in encodings), default=1) or 1

        input_ids = np.full((len(encodings), width), self.pad_id, dtype=np.int64)
        attention = np.zeros((len(encodings), width), dtype=np.int64)
        # 有的导出没有 token_type_ids（XLM-R 就是），有就一起喂
        need_type = "token_type_ids" in self.input_names
        types = np.zeros((len(encodings), width), dtype=np.int64) if need_type else None

        for row, enc in enumerate(encodings):
            n = len(enc.ids)
            input_ids[row, :n] = enc.ids
            attention[row, :n] = enc.attention_mask

        feeds = {"input_ids": input_ids, "attention_mask": attention}
        if need_type:
            feeds["token_type_ids"] = types
        hidden = self.session.run(None, feeds)[0]  # [n, seq, dim]

        if POOLING == "cls":
            pooled = hidden[:, 0, :]
        else:
            mask = attention[:, :, None].astype(hidden.dtype)
            pooled = (hidden * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-6, None)

        norm = np.linalg.norm(pooled, axis=1, keepdims=True)
        return pooled / np.clip(norm, 1e-12, None)


def _get_backend():
    """拿后端；不可用返回 None 并把原因记在 _backend_error 里（只尝试一次）"""
    global _backend, _backend_error
    if _backend is not None or _backend_error:
        return _backend
    with _lock:
        if _backend is not None or _backend_error:
            return _backend
        if not MODEL_DIR:
            _backend_error = "没有配置 EMBED_MODEL_DIR"
            return None
        try:
            _backend = _Backend(MODEL_DIR)
        except Exception as e:  # 缺依赖、文件不在、导出损坏，统统当作"不可用"
            _backend_error = f"{type(e).__name__}: {e}"
            _backend = None
    return _backend


def available() -> bool:
    """本地向量能不能用。调用方拿它决定要不要退回字面相似度"""
    return _get_backend() is not None


def unavailable_reason() -> str:
    """不能用时的原因，写进日志和自检报告，省得猜"""
    return _backend_error


def encode(texts: list):
    """同步取向量。不可用返回 None（不是抛错，调用方好写兜底）"""
    backend = _get_backend()
    if backend is None:
        return None
    return backend.encode(list(texts))


async def aencode(texts: list):
    """异步取向量：节点里直接 await，别让 CPU 推理卡住事件循环"""
    return await asyncio.to_thread(encode, list(texts))


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """两个已归一化向量的余弦相似度（等于点积）"""
    if a is None or b is None:
        return 0.0
    return float(np.dot(a, b))


def max_cosine(vec: np.ndarray, others) -> float:
    """一个向量与一组已归一化向量的最大余弦。others 为空返回 0"""
    if vec is None or others is None:
        return 0.0
    matrix = np.asarray(others, dtype=np.float32)
    if matrix.size == 0:
        return 0.0
    return float(np.max(matrix @ vec))
