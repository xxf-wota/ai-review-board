# -*- coding: utf-8 -*-
"""
M2 验收脚本：验证四位评审能否问出四个明显不同视角的问题

不需要图、不需要前端、不需要数据库，直接调用两个核心函数：
    extract_elements()   方案全文 -> 要素表
    reviewer_question()  某位评审 -> 一个问题

验收标准：
    1. 四位评审各出一个问题，都能落地到方案的具体位置
    2. 四个问题互相不重复（两两相似度低）
    3. 每位评审都不越界（不问自己关注点以外的东西）
输出写到 data/_m2_reviewers.txt，逐步骤落盘，中途卡住也能看到已有结果
"""
import os
import re
import sys
import time
import traceback

# 脚本在 scripts/ 下，把项目根目录加进模块搜索路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai.agent.review_agent.node.extract_node import extract_elements, format_elements
from app.ai.agent.review_agent.node.speaker_node import (
    REVIEWER_NAMES,
    reviewer_question,
)

REPORT = "data/_m2_reviewers.txt"
ROLES = ["tech", "cost", "compliance", "user"]

# 每位评审的"越界词"：出现就说明人设没守住
FORBIDDEN = {
    "tech": ["预算", "成本", "费用", "多少钱", "谁出钱", "排期"],
    "cost": ["隐私", "合规", "授权", "偏见", "准确率"],
    "compliance": ["预算", "成本", "排期", "准确率提升"],
    "user": ["预算", "接口", "架构", "数据库", "选型"],
}

_lines = []


def log(text=""):
    """写报告并立即落盘，脚本被中断也能保留已有结果"""
    _lines.append(str(text))
    os.makedirs("data", exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines))
    print(str(text)[:200].encode("ascii", "replace").decode("ascii"))


def bigrams(text):
    """中文按二元字组切分，用来算相似度，不依赖分词库"""
    # 去掉所有非文字字符（标点、空白），中文按字保留
    text = re.sub(r"[^\w]", "", text)
    return {text[i:i + 2] for i in range(len(text) - 1)}


def similarity(a, b):
    """两句话的二元字组 Jaccard 相似度"""
    ga, gb = bigrams(a), bigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def main():
    log("=" * 70)
    log("M2 验收：四位评审的视角是否真的不同")
    log("=" * 70)

    # 读方案样本
    with open("data/demo_plan.txt", encoding="utf-8") as f:
        plan_text = f.read()
    log(f"\n方案样本长度：{len(plan_text)} 字")

    # 步骤一：要素抽取
    log("\n" + "-" * 70)
    log("步骤一：方案要素抽取")
    log("-" * 70)
    t0 = time.time()
    elements = extract_elements(plan_text)
    log(f"耗时 {time.time() - t0:.1f} 秒\n")
    log(format_elements(elements))
    log(f"\n抽取到的缺失项：{elements.get('missing')}")

    # 步骤二：四位评审依次提问
    log("\n" + "-" * 70)
    log("步骤二：四位评审依次提问")
    log("-" * 70)
    questions = {}
    for role in ROLES:
        name = REVIEWER_NAMES[role]
        t0 = time.time()
        try:
            q = reviewer_question(role, elements)
            questions[role] = q
            log(f"\n【{name}】({time.time() - t0:.1f} 秒)")
            log(f"  {q}")
        except Exception as e:
            log(f"\n【{name}】调用失败：{e}")
            log(traceback.format_exc())

    # 步骤三：视角多样性检查
    log("\n" + "-" * 70)
    log("步骤三：视角多样性检查（两两相似度，越低越好）")
    log("-" * 70)
    valid = [r for r in ROLES if questions.get(r)]
    pairs = []
    if len(valid) >= 2:
        for i in range(len(valid)):
            for j in range(i + 1, len(valid)):
                a, b = valid[i], valid[j]
                s = similarity(questions[a], questions[b])
                pairs.append((s, a, b))
        for s, a, b in pairs:
            flag = "  <-- 偏高，视角可能重复" if s >= 0.35 else ""
            log(f"  {REVIEWER_NAMES[a]} vs {REVIEWER_NAMES[b]}: {s:.2f}{flag}")
        log(f"\n  最高相似度：{max(p[0] for p in pairs):.2f}")
        log(f"  平均相似度：{sum(p[0] for p in pairs) / len(pairs):.2f}")

    # 步骤四：人设越界检查
    log("\n" + "-" * 70)
    log("步骤四：规范性检查（越界词 / 一问多问 / 长度）")
    log("-" * 70)
    violation = 0
    multi_q = 0
    for role in valid:
        q = questions[role]
        hits = [w for w in FORBIDDEN[role] if w in q]
        # 一句话里出现多个问号，说明把好几个问题塞在了一起
        n_ask = q.count("？") + q.count("?")
        marks = []
        if hits:
            violation += 1
            marks.append(f"越界词 {hits}")
        if n_ask > 1:
            multi_q += 1
            marks.append(f"一问多问 共{n_ask}问")
        if len(q) > 60:
            marks.append(f"偏长 {len(q)}字")
        log(f"  【{REVIEWER_NAMES[role]}】{'；'.join(marks) if marks else '合规'}")
    log(f"\n  越界评审数：{violation}/{len(valid)}")
    log(f"  一问多问数：{multi_q}/{len(valid)}")

    # 汇总结论
    log("\n" + "=" * 70)
    log("M2 结论")
    log("=" * 70)
    log(f"  出题数量：{len(valid)}/4")
    if len(valid) >= 2:
        log(f"  视角重复风险：{'高' if max(p[0] for p in pairs) >= 0.35 else '低'}")
    log(f"  人设越界数：{violation}")
    log(f"  一问多问数：{multi_q}")


if __name__ == "__main__":
    main()
