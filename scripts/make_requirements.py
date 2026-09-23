# -*- coding: utf-8 -*-
"""
生成 requirements.txt
版本直接从当前环境读取，不靠手写，避免写错版本号
"""
import os
import sys
from importlib import metadata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 项目实际用到的包。写成 (写进 requirements 的名字, 查询版本的发行包名)
# 两者不同是因为 extras 写法（psycopg[binary]）不是发行包名
PACKAGES = [
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("python-multipart", "python-multipart"),
    ("pydantic", "pydantic"),
    ("langchain", "langchain"),
    ("langchain-core", "langchain-core"),
    ("langchain-openai", "langchain-openai"),
    ("langgraph", "langgraph"),
    ("langgraph-checkpoint-postgres", "langgraph-checkpoint-postgres"),
    ("httpx", "httpx"),
    ("PyMySQL", "PyMySQL"),
    ("python-dotenv", "python-dotenv"),
    ("PyYAML", "PyYAML"),
    ("python-docx", "python-docx"),
    ("pdfplumber", "pdfplumber"),
    ("pypdf", "pypdf"),
    ("vosk", "vosk"),
    ("pyecharts", "pyecharts"),
    ("numpy", "numpy"),
    # 会话与记忆基础设施
    ("redis", "redis"),
    ("chromadb", "chromadb"),
    # [binary] 会把 libpq 一起装进来，省掉「PATH 里没有 libpq 就起不来」这类环境问题
    ("psycopg[binary]", "psycopg"),
    ("psycopg-pool", "psycopg-pool"),
]

resolved, missing = [], []
for req_name, dist_name in PACKAGES:
    try:
        resolved.append(f"{req_name}=={metadata.version(dist_name)}")
    except metadata.PackageNotFoundError:
        missing.append(dist_name)

header = [
    "# 由 scripts/make_requirements.py 从当前环境读取生成",
    "# 安装：pip install -r requirements.txt",
    "",
]
content = "\n".join(header + resolved) + "\n"

with open(os.path.join(ROOT, "requirements.txt"), "w", encoding="utf-8") as f:
    f.write(content)

report = [f"已生成 requirements.txt，{len(resolved)} 个包"]
if missing:
    report.append(f"以下包在当前环境未安装，已跳过：{missing}")
with open(os.path.join(ROOT, "data", "_deps.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(report))
print("requirements done")
