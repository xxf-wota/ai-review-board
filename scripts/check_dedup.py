"""离线自检：接话去重（炒冷饭判定）

不调大模型。向量模型有就用、没有就走字面兜底 —— 两条路径都要验：
本地向量模型装好了却加载失败时，代码会静默退回字面相似度，
那条路挡不住"换个说法再问一遍"，必须知道它到底还在不在。

    python scripts/check_dedup.py            # 全部通过 exit 0
    python scripts/check_dedup.py --reverse  # 反向验证：把线抬到 1.01，必须有检查变红

反向验证是这套自检的价值所在：如果抬线之后结果一点不变，
说明这些断言根本没验到去重逻辑（改坏了也看不出来）。
"""
import argparse
import asyncio
import os
import sys
import uuid
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.ai.agent.review_agent.node import cross_node          # noqa: E402
from app.ai.agent.review_agent.store import question_store as store  # noqa: E402
from app.ai.tool import review_dao                             # noqa: E402
from app.ai.utils import embed_util                            # noqa: E402
from app.ai.utils.text_util import similarity                  # noqa: E402

REPORT = os.path.join(ROOT, "data", "_dedup_check.txt")

# 一律用单句问号结尾，first_question() 只留第一句，不会把测试文案裁掉
Q_OLD_COST = "接入的大模型每月调用量预计多少次？"
Q_OLD_TECH = "在已有 5000 名用户规模下，大模型匹配教材需求的具体实现流程是什么？"
Q_PARA = "大模型接进来以后，一个月大概要调用多少回？"          # 与 Q_OLD_COST 同一件事，换个说法
Q_DIFF = "如果学生对推荐结果不满意，平台会提供哪些申诉渠道？"   # 不同的问题
Q_FRESH_1 = "学生上传的方案文本会保存在服务器上多长时间？"
Q_FRESH_2 = "这套系统在断网的情况下还能不能继续使用？"
Q_FRESH_3 = "学校的信息中心需要配合做哪些部署工作？"

ELEMENTS = {
    "goal": "做一个面向大学生的教材匹配助手",
    "tech": "FastAPI + LangGraph + 本地向量模型",
    "budget": "每月模型调用费控制在 2000 元以内",
    "schedule": "六周完成",
    "risks": "本地模型效果不稳定",
    "compliance": "上传的方案要学生授权",
    "users": "面向本校学生，先小范围试用",
    "missing": "没写清调用量的口径",
}


class _FakeAgent:
    """按脚本喂台词：每次 ainvoke 吐下一句，并把收到的提示词留下来"""

    def __init__(self, texts):
        self.texts = list(texts)
        self.prompts = []

    async def ainvoke(self, user_msg):
        msgs = (user_msg or {}).get("messages") or []
        self.prompts.append(str(msgs[0].content) if msgs else "")
        text = self.texts.pop(0) if self.texts else ""
        return {"messages": [SimpleNamespace(content=text)]}


def install_fakes(texts):
    agent = _FakeAgent(texts)
    cross_node.create_agent = lambda *a, **k: agent

    async def fake_judge_cross(plan_elements, recent_rows, spoke):
        return [{"role": "cost", "want": True, "severity": 4, "point": "成本口径没说清楚"}]

    cross_node.judge_cross = fake_judge_cross
    return agent


# ---------------- 第一节：判据本身 ----------------

async def check_unit(out):
    """直接调 _repeated_question，把库里的记录换成一问一答的固定两行"""
    checks = []
    rows = [{"question": Q_OLD_COST, "speaker_role": "cost"},
            {"question": Q_OLD_TECH, "speaker_role": "tech"}]
    real_all_rows = store.all_rows
    real_available = embed_util.available

    # 先记下三句话的真实余弦，报告里要能看见数
    vecs = embed_util.encode([Q_OLD_COST, Q_OLD_TECH, Q_PARA, Q_DIFF])
    if vecs is not None:
        cos_para = embed_util.cosine(vecs[0], vecs[2])
        cos_diff = embed_util.cosine(vecs[0], vecs[3])
        bigram_para = similarity(Q_OLD_COST, Q_PARA)
        out.append(f"  cos(原句, 换说法) = {cos_para:.3f}   字面 Jaccard = {bigram_para:.3f}")
        out.append(f"  cos(原句, 不同问题) = {cos_diff:.3f}")
    else:
        out.append(f"  向量模型不可用（{embed_util.unavailable_reason()}）：只验兜底路径")
        cos_para = cos_diff = 0.0

    async def fake_all_rows(session_id):
        return list(rows)

    async def empty_all_rows(session_id):
        return []

    try:
        store.all_rows = fake_all_rows

        hit = await cross_node._repeated_question(Q_OLD_COST, "unit")
        checks.append(("逐字重复要认出来", hit == Q_OLD_COST))

        hit = await cross_node._repeated_question(Q_PARA, "unit")
        checks.append((f"换说法重复要认出来（cos {cos_para:.3f} >= 线）",
                       hit == Q_OLD_COST if embed_util.available() else hit == ""))

        hit = await cross_node._repeated_question(Q_DIFF, "unit")
        checks.append((f"不同的问题不能误杀（cos {cos_diff:.3f} < 线）", hit == ""))

        hit = await cross_node._repeated_question("", "unit")
        checks.append(("空文本直接返回空", hit == ""))

        store.all_rows = empty_all_rows
        hit = await cross_node._repeated_question(Q_OLD_COST, "unit")
        checks.append(("库里没记录时不报错、返回空", hit == ""))

        # 兜底路径：向量不可用，退回字面相似度
        store.all_rows = fake_all_rows
        embed_util.available = lambda: False
        hit = await cross_node._repeated_question(Q_OLD_COST, "unit")
        checks.append(("兜底：逐字重复仍拦得住", hit == Q_OLD_COST))
        hit = await cross_node._repeated_question(Q_PARA, "unit")
        checks.append(("兜底：换说法拦不住（已知弱点，写进断言免得被当成修好了）", hit == ""))
    finally:
        store.all_rows = real_all_rows
        embed_util.available = real_available
    return checks


# ---------------- 第二节：节点级 ----------------

async def run_scenario(texts, out):
    """起一个真会话（真写 MySQL）、喂台词跑一次 cross_node，返回现场"""
    sid = "dedupchk-" + uuid.uuid4().hex[:10]
    index = []
    for role, question in (("cost", Q_OLD_COST), ("tech", Q_OLD_TECH)):
        entry = store.new_entry(1, role, "main", question=question)
        await store.ask(sid, entry)
        index.append(entry)
    agent = install_fakes(texts)
    state = {
        "session_id": sid,
        "round": 1,
        "plan_elements": ELEMENTS,
        "question_index": index,
        "spoke_in_issue": [],
        "cross_in_issue": 0,
        "cross_total": 0,
        "speaker_order": ["tech", "cost", "compliance", "user"],
        "current_speaker": "tech",
    }
    try:
        res = await cross_node.cross_node(state) or {}
    finally:
        rows = review_dao.get_questions(sid)
        review_dao.delete_questions(sid)
    crosses = [r for r in rows if r.get("question_type") == "cross"]
    return agent, res, crosses


async def check_node(out):
    checks = []

    # 1) 第一次是冷饭 → 重试时把被重复的原句念给模型 → 第二次改好 → 落库
    agent, res, crosses = await run_scenario([Q_OLD_COST, Q_FRESH_1], out)
    out.append(f"  场景1 模型收到 {len(agent.prompts)} 次提示；落库的接话={[r.get('question') for r in crosses]}")
    checks.append(("炒冷饭要重试一次", len(agent.prompts) == 2))
    checks.append(("重试提示里要念出被重复的那句原话",
                   len(agent.prompts) == 2 and f"「{Q_OLD_COST}」" in agent.prompts[1]))
    checks.append(("改好的接话要正常落库", res.get("cross_total") == 1
                   and [r.get("question") for r in crosses] == [Q_FRESH_1]))
    checks.append(("索引里要留下这条接话", len(res.get("question_index") or []) == 3))

    # 2) 重试仍然是冷饭 → 放弃这次接话，一条都不落库
    agent, res, crosses = await run_scenario([Q_OLD_COST, Q_OLD_COST], out)
    out.append(f"  场景2 模型收到 {len(agent.prompts)} 次提示；落库的接话={crosses}")
    checks.append(("重试仍重复要放弃接话（不发 cross_total）",
                   "cross_total" not in res and res.get("cross_checked") is True))
    checks.append(("放弃的接话不能落库", crosses == []))

    # 3) 本来就不是冷饭 → 不许重试（别白白多花一次模型调用）
    agent, res, crosses = await run_scenario([Q_FRESH_2], out)
    out.append(f"  场景3 模型收到 {len(agent.prompts)} 次提示；落库的接话={[r.get('question') for r in crosses]}")
    checks.append(("不重复就不重试", len(agent.prompts) == 1))
    checks.append(("不重复的接话直接落库", res.get("cross_total") == 1
                   and [r.get("question") for r in crosses] == [Q_FRESH_2]))

    # 4) 兜底路径：向量模型不可用，字面判据照样要能拦住逐字重复
    real_available = embed_util.available
    embed_util.available = lambda: False
    try:
        agent, res, crosses = await run_scenario([Q_OLD_COST, Q_FRESH_3], out)
    finally:
        embed_util.available = real_available
    out.append(f"  场景4（兜底）模型收到 {len(agent.prompts)} 次提示；落库的接话={[r.get('question') for r in crosses]}")
    checks.append(("向量不可用时兜底判据仍要拦住逐字重复", len(agent.prompts) == 2))
    checks.append(("兜底路径改好之后照样落库", res.get("cross_total") == 1
                   and [r.get("question") for r in crosses] == [Q_FRESH_3]))
    return checks


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reverse", action="store_true",
                        help="反向验证：把线抬到 1.01，断言必须变红")
    args = parser.parse_args()

    out = []
    out.append("=== 接话去重自检（不调大模型）===")
    out.append(f"判据：cos >= {cross_node.DEDUP_COS}（兜底字面 >= {cross_node.DEDUP_BIGRAM}）；"
               f"范围：整场之前问过的每一句")
    out.append(f"向量模型：{'可用' if embed_util.available() else '不可用 —— ' + embed_util.unavailable_reason()}")
    if args.reverse:
        cross_node.DEDUP_COS = 1.01
        out.append("【反向验证】把 DEDUP_COS 抬到 1.01 —— 断言必须变红")

    out.append("\n-- 第一节 判据本身 --")
    checks = await check_unit(out)
    out.append("\n-- 第二节 节点级（真写 MySQL）--")
    checks += await check_node(out)

    failed = [(name, ok) for name, ok in checks if not ok]
    out.append("\n-- 结论 --")
    for name, ok in checks:
        out.append(f"  [{'通过' if ok else '不通过'}] {name}")

    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")

    passed = len(checks) - len(failed)
    # 反向验证是自检的一部分：抬线之后必须真变红，否则说明断言没验到去重逻辑
    broke = len(failed)
    print(f"dedup check: {passed}/{len(checks)} passed"
          + (f"; reverse mode, {broke} red as expected" if args.reverse else ""))
    if args.reverse:
        return 0 if broke >= 2 else 1
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
