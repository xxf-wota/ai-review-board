# -*- coding: utf-8 -*-
"""
生成 requirements.txt
版本直接从当前环境读取，不靠手写，避免写错版本号
"""
import os
import sys
from importlib import metadata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 项目实际用到的包（发行包名）
PACKAGES = [
    "fastapi",
    "uvicorn",
    "python-multipart",
    "pydantic",
    "langchain",
    "langchain-core",
    "langchain-openai",
    "langgraph",
    "httpx",
    "PyMySQL",
    "python-dotenv",
    "PyYAML",
    "python-docx",
    "pdfplumber",
    "pypdf",
    "vosk",
    "pyecharts",
    "numpy",
]

resolved, missing = [], []
for name in PACKAGES:
    try:
        resolved.append(f"{name}=={metadata.version(name)}")
    except metadata.PackageNotFoundError:
        missing.append(name)

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
