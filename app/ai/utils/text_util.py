import re

"""
文本相似度工具
不依赖分词库：中文按二元字组切分，算 Jaccard 相似度
用来判断两句话是不是在说同一件事
"""


def bigrams(text: str) -> set:
    """去掉标点和空白后，切成相邻两字的集合"""
    cleaned = re.sub(r"[^\w]", "", text or "")
    return {cleaned[i:i + 2] for i in range(len(cleaned) - 1)}


def similarity(a: str, b: str) -> float:
    """两句话的相似度，0 到 1，越大越像"""
    ga, gb = bigrams(a), bigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)
