"""
本地重排序工具：判断"这两句话是不是同一个问题"

为什么不能只用双塔向量（app/ai/utils/embed_util.py）
----------------------------------------------------
双塔是把两句话**分别**压成一个向量再算余弦，句子之间没有交互。
实测这台机器上的 paraphrase-multilingual-MiniLM-L12-v2：
    同一件事换个说法   0.96
    同一主题、不同问题 0.70 ~ 0.88
    完全无关           0.49   ← 分底太高
"同主题不同问题"和"同一件事"挤在一起，绝对阈值怎么定都要误杀或漏判。

重排序模型是交叉编码器：两句话拼成一条序列一起过一次模型，输出一个 logit。
同一个模型（bge-reranker-large）在这批数据上：
    同一件事换个说法   6.4 / 6.6
    真正重复（自己问过）5.0 / 6.9
    逐字抄            9.5
    同一主题、不同问题 -3.4 ~ 0.4
    完全无关          -9.3 / -8.5
所以判"是不是炒冷饭"用它的分，阈值取 0 以上的某个数，中间有大片余量。

模型与运行方式（实测，别改回去）
--------------------------------
F:\\models\\bge-reranker-large_v1 是完整的 ONNX 导出：
    onnx/model.onnx（618KB 图）+ onnx/model.onnx_data（约 2GB 权重，外部数据）
onnxruntime 会自动把同目录的 onnx_data 读进来，不需要转换、不需要 torch。
加载约 2.6 秒，中文一问一答 35 毫秒/对（CPU，本机实测）。

路径全部从 .env 读；读不到或加载失败时 available() 返回 False，
调用方退回向量余弦或字面相似度 —— 演示现场不能因为这个崩。
"""

import asyncio
import os
import threading

import numpy as np
from dotenv import load_dotenv

load_dotenv()

# 模型目录。默认空串，等于"这台机器没有重排序模型"，由 .env 指到本地权重
MODEL_DIR = os.getenv("RERANK_MODEL_DIR") or ""
ONNX_FILE = os.getenv("RERANK_ONNX_FILE") or "onnx/model.onnx"
# 一对问题都很短，256 足够；长了白花时间
MAX_LEN = int(os.getenv("RERANK_MAX_LEN") or 256)

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

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        self.session = ort.InferenceSession(
            onnx_path, sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.input_names = {i.name for i in self.session.get_inputs()}
        self.tokenizer = Tokenizer.from_file(tokenizer_path)
        self.tokenizer.enable_truncation(max_length=MAX_LEN)
        pad_id = self.tokenizer.token_to_id("<pad>")
        self.pad_id = 1 if pad_id is None else pad_id

    def score(self, pairs: list) -> list:
        """一批句对 -> 一批 logit（越大越像同一个问题）

        pairs 里每一项是 (a, b)。分词器把两句拼成 <s> a </s></s> b </s>，
        这正是 bge-reranker 训练时的输入格式，不要自己去拼字符串
        """
        if not pairs:
            return []
        encodings = self.tokenizer.encode_batch(
            [(str(a or ""), str(b or "")) for a, b in pairs]
        )
        width = max((len(e.ids) for e in encodings), default=1) or 1
        input_ids = np.full((len(encodings), width), self.pad_id, dtype=np.int64)
        attention = np.zeros((len(encodings), width), dtype=np.int64)
        for row, enc in enumerate(encodings):
            n = len(enc.ids)
            input_ids[row, :n] = enc.ids
            attention[row, :n] = enc.attention_mask

        feeds = {"input_ids": input_ids, "attention_mask": attention}
        if "token_type_ids" in self.input_names:
            feeds["token_type_ids"] = np.zeros_like(input_ids)
        logits = self.session.run(None, feeds)[0]
        return [float(x) for x in np.asarray(logits).reshape(-1)]


def _get_backend():
    """拿后端；不可用返回 None 并把原因记在 _backend_error 里（只尝试一次）"""
    global _backend, _backend_error
    if _backend is not None or _backend_error:
        return _backend
    with _lock:
        if _backend is not None or _backend_error:
            return _backend
        if not MODEL_DIR:
            _backend_error = "没有配置 RERANK_MODEL_DIR"
            return None
        try:
            _backend = _Backend(MODEL_DIR)
        except Exception as e:  # 缺依赖、文件不在、导出损坏，统统当作"不可用"
            _backend_error = f"{type(e).__name__}: {e}"
            _backend = None
    return _backend


def available() -> bool:
    """本地重排序能不能用。调用方拿它决定要不要退回余弦/字面相似度"""
    return _get_backend() is not None


def unavailable_reason() -> str:
    """不能用时的原因，写进日志和自检报告，省得猜"""
    return _backend_error


def score_pairs(pairs: list):
    """同步打分。不可用返回 None（不是抛错，调用方好写兜底）"""
    backend = _get_backend()
    if backend is None:
        return None
    return backend.score(list(pairs))


async def ascore_pairs(pairs: list):
    """异步打分：节点里直接 await，别让 CPU 推理卡住事件循环"""
    return await asyncio.to_thread(score_pairs, list(pairs))


def max_score(text: str, others) -> tuple:
    """text 与一组句子的最高分，返回 (分数, 那句话)。没得比时返回 (None, "")

    调用方关心的不只是"像不像"，还有"像的是哪一句" —— 重试时要把这句话
    念给模型听，它才知道该避开什么
    """
    others = [o for o in (others or []) if o]
    if not text or not others:
        return None, ""
    scores = score_pairs([(text, o) for o in others])
    if scores is None:
        return None, ""
    best = int(np.argmax(scores))
    return scores[best], others[best]
