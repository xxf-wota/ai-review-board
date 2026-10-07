"""
标定脚本：把"接话是不是在炒冷饭"的判定，从字面相似度换成向量相似度之前，先量一遍

要回答的问题（都是猜不出来的，得用真数据）：
  1. 本地 ONNX 向量模型在这台机器上跑不跑得动、一句话多少毫秒
  2. 二元字组 Jaccard（现在的代码）和余弦相似度（要换的）在同一批真实问句上差多少 ——
     尤其是"同一件事换个说法"，Jaccard 会掉下去，向量应该还站得住
  3. 余弦 >= 0.7 这条线，在这些真实接话上会挡掉哪几条、有没有误杀
     （误杀 = 不同评审从各自角度问同一主题，那是交叉质询的设计意图，不该当冷饭）
  4. 换成交叉编码器（bge-reranker）之后同样的问题，它的 logit 线定在哪合适 ——
     双塔余弦"同主题不同问题"和"同一件事"挤在一起，交叉编码器能把它们拉开

只读库、只调本地模型，不改任何业务代码。报告写 data/_dedup_calibration.txt
用法：
    python scripts/calibrate_dedup.py                 # 最近一场有记录的会话
    python scripts/calibrate_dedup.py --session xxx
    python scripts/calibrate_dedup.py --all           # 所有会话汇总（推荐：单场样本太少）
"""

import os
import sys
import time

import numpy as np
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

load_dotenv(os.path.join(ROOT, ".env"))

from app.ai.agent.review_agent.store import question_store as store
from app.ai.utils import embed_util, rerank_util
from app.ai.utils.text_util import similarity as bigram

REPORT = os.path.join(ROOT, "data", "_dedup_calibration.txt")
# 阈值扫描的档位：看"0.7"是不是一个自然的断点，还是拍脑袋定的
THRESHOLDS = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]
# 线上用的线（cross_node.DEDUP_COS）。0.70 是以前字面相似度那条线，
# 换度量时一起抬到 0.80，理由见报告第五节的逐条核对
CURRENT = 0.80
# 换度量之前的旧线，报告里要能看出"抬线前后"的差别
OLD_LINE = 0.70
# 重排序是 logit，不是余弦：真重复实测 5.0~6.9，同主题不同问题 <= 0.4，所以档位在 0 附近
RANK_THRESHOLDS = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
# 交叉编码器打算用的线（探针实测：真重复 >= 5.0，同主题 <= 0.4，取中间偏保守）
CURRENT_RANK = 3.0
# --all 时最多看多少场（每场约十来条问题，够用就行，不想让脚本跑太久）
MAX_SESSIONS = 8


# ---------------- 取数据 ----------------

def mysql_conn():
    import pymysql
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT") or 3306),
        user=os.getenv("MYSQL_USER"), password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DATABASE"), charset=os.getenv("MYSQL_CHARSET") or "utf8mb4",
    )


def sessions_with_cross(limit: int = MAX_SESSIONS) -> list:
    """有接话记录的会话，最近的在前面。没有接话就没法标定"""
    conn = mysql_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("select session_id, count(*) as n from review_question "
                        "where question_type = 'cross' group by session_id "
                        "order by max(id) desc limit %s", (limit,))
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def load(session_id: str) -> list:
    from app.ai.tool.review_dao import get_questions
    rows = get_questions(session_id) or []
    return [r for r in rows if (r.get("question") or "").strip()]


# ---------------- 池子的取法：和 cross_node 生成接话时的视野对齐 ----------------

def pools_for(rows: list, cross_at: int) -> dict:
    """给第 cross_at 条（一条接话）算出三类比较池。

    必须复现"生成那一刻"能看到什么：只算它**之前**的记录，不能把后文的记录算进来，
    否则标出来的分数比线上严，会误导阈值的取舍。
    """
    me = rows[cross_at]
    before = rows[:cross_at]
    issues = store.split_issues(rows)
    mine = [g for g in issues if me in g]
    issue = mine[0] if mine else []
    issue_before = [r for r in issue if r in before]

    # A 它接的那句话：议题里我这个接话之前，最后一条"抛给学生的问题"
    target = None
    for r in reversed(before):
        if r.get("question_type") in ("main", "followup"):
            target = r
            break
    # B 这位评审自己之前问过的（含主问题、追问、早先的接话）
    own = [r for r in before if r.get("speaker_role") == me.get("speaker_role")]
    # C 本议题里其他人之前问过的
    others = [r for r in issue_before if r.get("speaker_role") != me.get("speaker_role")]
    # D 全场之前问过的每一句（最宽的范围）
    #   加它是被 #176 逼出来的：tech 把 cost 的"每月调用量预计多少次"逐字复述了一遍
    #   （bigram 1.000），可那句属于别的议题，A/B/C 三个范围都看不见它
    everything = [r for r in before if (r.get("question") or "").strip()]
    return {"target": ([target] if target else []), "own": own, "issue": others,
            "all": everything}


# ---------------- 打分 ----------------

def score_crosses(session_id: str, rows: list) -> list:
    """把一场里的每条接话都算一遍：与三类池子的最大 cos / 最大重排序分、最像的那句、现在的字面分

    分数一次算完，第五节扫阈值时就只剩比大小，不用反复跑模型
    """
    vectors = embed_util.encode([r.get("question", "") for r in rows])
    scored = []
    for i, r in enumerate(rows):
        if r.get("question_type") != "cross":
            continue
        pools = pools_for(rows, i)
        text = r.get("question", "")
        item = {"session": session_id, "row": r, "best": {}, "rank": {}}
        for name in ("target", "own", "issue"):
            texts = [x.get("question", "") for x in pools[name] if (x.get("question") or "").strip()]
            if texts:
                gv = embed_util.encode(texts)
                hits = [embed_util.cosine(vectors[i], v) for v in gv]
                k = int(np.argmax(hits))
                item["best"][name] = (hits[k], texts[k])
            else:
                item["best"][name] = (0.0, "")
        now = [bigram(text, x.get("question", ""))
               for x in pools["target"] + pools["own"]]
        item["bigram_now"] = max(now) if now else 0.0

        # 重排序：一条接话要跟三类池子里的每一句都比一遍，攒成一批一次过模型
        plans = [(name, [x.get("question", "") for x in pools[name]
                         if (x.get("question") or "").strip()])
                 for name in ("target", "own", "issue")]
        pairs = [(text, t) for _, texts in plans for t in texts]
        logits = rerank_util.score_pairs(pairs) if pairs else []
        if logits is None:  # 后端不可用：全都记 None，报告里显示为 "-"
            for name, _ in plans:
                item["rank"][name] = (None, "")
        else:
            at = 0
            for name, texts in plans:
                if not texts:
                    item["rank"][name] = (None, "")
                    continue
                chunk = logits[at:at + len(texts)]
                at += len(texts)
                k = int(np.argmax(chunk))
                item["rank"][name] = (chunk[k], texts[k])

        # 最宽范围：全场之前问过的每一句。这里留着全部明细（不只最大值），
        # 因为还要模拟线上真正要跑的两段式：先用向量粗筛 top-k，再用重排序定夺
        idx = [j for j in range(i) if (rows[j].get("question") or "").strip()]
        texts_all = [rows[j].get("question", "") for j in idx]
        item["all_cos"] = sorted(
            [(embed_util.cosine(vectors[i], vectors[j]), rows[j].get("question", ""))
             for j in idx], key=lambda x: -x[0])
        ranks_all = rerank_util.score_pairs([(text, t) for t in texts_all]) if texts_all else []
        item["all_rank"] = sorted(zip(ranks_all or [], texts_all), key=lambda x: -x[0])
        item["all_rank_map"] = {t: s for s, t in zip(ranks_all or [], texts_all)}
        scored.append(item)
    return scored


def prefilter_survives(item: dict, threshold: float, top_k: int = 4) -> bool:
    """两段式模拟：向量粗筛 top_k 句，重排序只给这几句打分，还能不能挡住

    线上如果全量重排序（一条接话跟十几句比，每句 64 毫秒）会明显拖慢会议，
    所以打算先向量粗筛。这里要确认粗筛不会把该挡的漏掉
    """
    short = [t for _, t in item["all_cos"][:top_k]]
    scores = [item["all_rank_map"].get(t) for t in short]
    scores = [s for s in scores if s is not None]
    return bool(scores) and max(scores) >= threshold


def killed(item: dict, threshold: float, wide: bool = False) -> bool:
    """这条接话在给定阈值下会不会被挡掉。

    wide=True 用最宽范围（全场之前问过的每一句）；否则是保守范围（对方原话 + 自己问过的）
    """
    if wide:
        return bool(item.get("all_cos")) and item["all_cos"][0][0] >= threshold
    names = ("target", "own")
    return max(item["best"][n][0] for n in names) >= threshold


def rank_of(item: dict, name: str):
    """这条接话与某个池子的最高重排序分；后端不可用时是 None"""
    pair = (item.get("rank") or {}).get(name)
    return None if not pair else pair[0]


def rank_killed(item: dict, threshold: float, wide: bool = False) -> bool:
    """重排序口径下会不会被挡掉（logit 越大越像，所以是 >=）。

    wide=True 用最宽范围（全场之前问过的每一句），否则是对方原话 + 自己问过的
    """
    if wide:
        return bool(item.get("all_rank")) and item["all_rank"][0][0] >= threshold
    best = rank_max(item, ("target", "own"))
    return best is not None and best >= threshold


def rank_max(item: dict, names):
    """这几个池子里的最高重排序分；全都没有（后端不可用）返回 None"""
    vals = [rank_of(item, n) for n in names]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def fmt_rank(value) -> str:
    return "   -  " if value is None else f"{value:.3f}"


def paraphrase(text: str) -> str:
    """让本地模型换个说法。失败返回空串，标定继续做别的"""
    try:
        from app.ai.model.my_model import MyModel
        rs = MyModel.get_local_model().invoke(
            "把下面这句话换一种说法（意思是同一件事，用词尽量不同），只输出改写后的那一句话：\n"
            + text
        )
        return str(getattr(rs, "content", "") or "").strip()
    except Exception as e:
        print(f"calibrate: paraphrase failed {type(e).__name__}")
        return ""


def write(lines: list):
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"report: {os.path.relpath(REPORT, ROOT)}")


# ---------------- 主流程 ----------------

def main():
    out = [
        "接话去重标定：字面相似度 vs 向量相似度（只读库，只调本地模型）",
        "",
        "=" * 74,
        "一、本地向量后端",
        "=" * 74,
        f"  模型目录：{embed_util.MODEL_DIR or '（未配置）'}",
        f"  导出文件：{embed_util.ONNX_FILE}    池化：{embed_util.POOLING}",
    ]
    if not embed_util.available():
        out += [f"  不可用：{embed_util.unavailable_reason()}",
                "", "结论：本地向量模型加载不了，标定做不下去。"]
        write(out)
        return 1

    probes = [
        ("同一件事换个说法", "每月大模型调用量大概多少次", "每个月要调用大语言模型多少回"),
        ("同一主题不同角度", "每月大模型调用量大概多少次", "这笔调用费用由谁承担"),
        ("完全无关", "每月大模型调用量大概多少次", "用户个人信息存多久"),
    ]
    vecs = embed_util.encode([p[1] for p in probes] + [p[2] for p in probes])
    t0 = time.time()
    embed_util.encode(["在已有 5000 名用户规模下，匹配一次大概调用几次模型"])
    per_ms = (time.time() - t0) * 1000
    dim = len(vecs[0])
    out += [f"  维度：{dim}；一句话耗时：{per_ms:.0f} 毫秒",
            "",
            "  参考对照（这几句不是库里的数据，是给分数找感觉用的）：",
            "    关系                  cos     bigram"]
    for i, (label, a, b) in enumerate(probes):
        out.append(f"    {label:20s} {embed_util.cosine(vecs[i], vecs[len(probes) + i]):.3f}   "
                   f"{bigram(a, b):.3f}")
    print(f"calibrate: backend ok dim={dim} {per_ms:.0f}ms/sentence")

    # 重排序后端：交叉编码器，判"是不是同一个问题"用它比双塔余弦靠谱
    out += ["", "  —— 重排序后端（交叉编码器）——"]
    if not rerank_util.available():
        out.append(f"  不可用：{rerank_util.unavailable_reason()}")
        print("calibrate: rerank unavailable, skip rank scores")
    else:
        t0 = time.time()
        rlogits = rerank_util.score_pairs([(p[1], p[2]) for p in probes])
        per_pair = (time.time() - t0) / max(len(probes), 1) * 1000
        out += [f"  模型目录：{rerank_util.MODEL_DIR}",
                f"  导出文件：{rerank_util.ONNX_FILE}；一对问题约 {per_pair:.0f} 毫秒",
                "",
                "  参考对照（logit，越大越像同一个问题；不相关的能压到很负）：",
                "    关系                  logit"]
        for (label, a, b), s in zip(probes, rlogits or []):
            out.append(f"    {label:20s} {float(s):8.3f}")
        print(f"calibrate: rerank ok {per_pair:.0f}ms/pair")

    session_id = os.getenv("CALIB_SESSION") or ""
    want_all = False
    for i, arg in enumerate(sys.argv):
        if arg == "--session" and i + 1 < len(sys.argv):
            session_id = sys.argv[i + 1]
        elif arg == "--all":
            want_all = True
    if session_id:
        session_ids = [session_id]
    elif want_all:
        session_ids = sessions_with_cross()
    else:
        session_ids = sessions_with_cross(1)

    all_scored, all_pairs = [], []
    for sid in session_ids:
        rows = load(sid)
        if len(rows) < 2:
            continue
        scored = score_crosses(sid, rows)
        all_scored += scored
        # 二、真实问句两两比较：看两种度量在真数据上的分歧
        vectors = embed_util.encode([r.get("question", "") for r in rows])
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                all_pairs.append({
                    "cos": embed_util.cosine(vectors[i], vectors[j]),
                    "bigram": bigram(rows[i].get("question", ""), rows[j].get("question", "")),
                    "a": rows[i], "b": rows[j], "session": sid,
                })

    if not all_scored:
        out += ["", "库里没有接话记录（question_type='cross'），标定到此为止。"]
        write(out)
        return 1

    all_pairs.sort(key=lambda p: -p["cos"])
    out += [
        "",
        "=" * 74,
        f"二、真实问句两两相似度（{len(session_ids)} 场，按 cos 降序前 15 对）",
        "=" * 74,
        "  cos     bigram  字面判据（线 0.70）认不认",
    ]
    for p in all_pairs[:15]:
        verdict = ("认（也算冷饭）" if p["bigram"] >= OLD_LINE
                   else "漏（换个说法就骗过去了）")
        out += [
            f"  {p['cos']:.3f}   {p['bigram']:.3f}  {verdict}",
            f"      A[{p['a'].get('question_type')}/{p['a'].get('speaker_role')}] "
            f"{p['a'].get('question', '')[:60]}",
            f"      B[{p['b'].get('question_type')}/{p['b'].get('speaker_role')}] "
            f"{p['b'].get('question', '')[:60]}",
        ]

    # 三、校准：换个说法
    first_rows = load(session_ids[0])
    source = first_rows[0].get("question", "") if first_rows else ""
    out += [
        "",
        "=" * 74,
        "三、校准：让本地模型把一句话换个说法（同一件事，用词不同）",
        "=" * 74,
    ]
    rewritten = paraphrase(source) if source else ""
    if not rewritten:
        out.append("  （本地模型没有返回改写结果，跳过）")
    else:
        pv = embed_util.encode([source, rewritten])
        out += [
            f"  原句　：{source[:70]}",
            f"  改写后：{rewritten[:70]}",
            f"  cos    = {embed_util.cosine(pv[0], pv[1]):.3f}   ← 同一件事，向量应当仍然很高",
            f"  bigram = {bigram(source, rewritten):.3f}   ← 换说法后掉下去，现在代码就放过了",
        ]

    # 四、逐条接话：这条线到底会挡掉什么
    out += [
        "",
        "=" * 74,
        f"四、逐条接话（{len(all_scored)} 条，来自 {len(session_ids)} 场）",
        f"    cos_原话 = 与它接的那句话；cos_自己 = 与这位评审自己之前问过的；",
        f"    cos_他人 = 与本议题内其他人之前问过的；bigram = 换度量前的字面判据（线 {OLD_LINE}）",
        "=" * 74,
    ]
    for item in all_scored:
        r = item["row"]
        out += [
            f"  [{item['session']} #{r.get('id')}] {r.get('speaker_role')} 接 "
            f"{r.get('target_speaker')}：{r.get('question', '')[:56]}",
            f"      cos_原话={item['best']['target'][0]:.3f}  cos_自己={item['best']['own'][0]:.3f}"
            f"  cos_他人={item['best']['issue'][0]:.3f}  bigram={item['bigram_now']:.3f}"
            f"  → {'挡掉' if killed(item, CURRENT) else '放过'}",
            f"      rank_原话={fmt_rank(rank_of(item, 'target'))}"
            f"  rank_自己={fmt_rank(rank_of(item, 'own'))}"
            f"  rank_他人={fmt_rank(rank_of(item, 'issue'))}"
            f"  → {'挡掉' if rank_killed(item, CURRENT_RANK) else '放过'}"
            f"（线 {CURRENT_RANK}）",
        ]
        for name, label in (("target", "最像的原话"), ("own", "最像的自己的旧问"),
                            ("issue", "最像的议题内他人问题")):
            if item["best"][name][1]:
                out.append(f"        {label}（cos {item['best'][name][0]:.3f} / "
                           f"rank {fmt_rank(rank_of(item, name))}）："
                           f"{item['best'][name][1][:56]}")

    # 五、汇总 + 阈值扫描
    n = len(all_scored)
    out += [
        "",
        "=" * 74,
        f"五、汇总（{n} 条接话）",
        "=" * 74,
        f"  阈值 {CURRENT} 下，各范围会挡掉几条：",
    ]
    for names, label in ((("target",), "只比对方原话"),
                         (("own",), "只比自己问过的"),
                         (("issue",), "只比议题内其他人问过的"),
                         (("target", "own"), "对方 + 自己（保守范围）"),
                         (("target", "own", "issue"), "对方 + 自己 + 议题他人")):
        hit = sum(1 for it in all_scored if max(it["best"][k][0] for k in names) >= CURRENT)
        out.append(f"    {label:24s}{hit} / {n}")
    legacy = sum(1 for it in all_scored if it["bigram_now"] >= CURRENT)
    out.append(f"    {'换度量前的字面 Jaccard':24s}{legacy} / {n}")
    out.append(f"    {'全场之前问过的每一句（最宽）':24s}"
               f"{sum(1 for it in all_scored if killed(it, CURRENT, wide=True))} / {n}")

    out += [
        "",
        "  阈值扫描（每档挡住几条，并列出被挡的接话，便于逐条看是不是误杀）：",
        "  阈值   保守范围   最宽范围   现在代码   被挡掉的（保守范围）",
    ]
    for t in THRESHOLDS:
        cons = [it for it in all_scored if killed(it, t)]
        wide = sum(1 for it in all_scored if killed(it, t, wide=True))
        leg = sum(1 for it in all_scored if it["bigram_now"] >= t)
        mark = "  ← 线上用的线" if abs(t - CURRENT) < 1e-9 else ""
        ids = ", ".join(f"#{it['row'].get('id')}({max(it['best']['target'][0], it['best']['own'][0]):.2f})"
                        for it in cons) or "—"
        out.append(f"  {t:.2f}   {len(cons):^8d}   {wide:^9d}   {leg:^8d}   {ids}{mark}")

    # 五之二、重排序口径下的汇总（后端不可用就整段跳过）
    if any(rank_of(it, "own") is not None for it in all_scored):
        out += [
            "",
            f"  换成交叉编码器（logit，线取 {CURRENT_RANK}）会挡掉几条：",
        ]
        for names, label in ((("target",), "只比对方原话"),
                             (("own",), "只比自己问过的"),
                             (("issue",), "只比议题内其他人问过的"),
                             (("target", "own"), "对方 + 自己（保守范围）"),
                             (("target", "own", "issue"), "对方 + 自己 + 议题他人")):
            hit = sum(1 for it in all_scored
                      if (rank_max(it, names) or -99) >= CURRENT_RANK)
            out.append(f"    {label:24s}{hit} / {n}")
        out.append(f"    {'全场之前问过的每一句（最宽）':24s}"
                   f"{sum(1 for it in all_scored if rank_killed(it, CURRENT_RANK, wide=True))}"
                   f" / {n}")

        out += [
            "",
            "  重排序阈值扫描（logit 不是余弦，档位跨 0）：",
            "  阈值   保守范围   最宽范围   被挡掉的（保守范围，括号里是它的最大 logit）",
        ]
        for t in RANK_THRESHOLDS:
            cons = [it for it in all_scored if rank_killed(it, t)]
            wide = sum(1 for it in all_scored if rank_killed(it, t, wide=True))
            mark = "  ← 打算用的线" if abs(t - CURRENT_RANK) < 1e-9 else ""
            ids = ", ".join(
                f"#{it['row'].get('id')}({max(v for v in (rank_of(it, 'target'), rank_of(it, 'own')) if v is not None):.2f})"
                for it in cons) or "—"
            out.append(f"  {t:.2f}   {len(cons):^8d}   {wide:^9d}   {ids}{mark}")

        # 两段式可行性：线上不打算把十几句全喂给重排序，先用向量粗筛
        full = [it for it in all_scored if rank_killed(it, CURRENT_RANK, wide=True)]
        missed = [it for it in full if not prefilter_survives(it, CURRENT_RANK)]
        out += [
            "",
            f"  两段式（向量粗筛 top-4 → 只给这几句跑重排序，线 {CURRENT_RANK}）：",
            f"    最宽范围共命中 {len(full)} 条；粗筛后仍挡住 {len(full) - len(missed)} 条，"
            f"被粗筛漏掉 {len(missed)} 条"
            + ("：" + ", ".join(f"#{it['row'].get('id')}" for it in missed) if missed else "（一条都不漏）"),
        ]

    out += ["",
            "报告结束：结论要结合第四节里「最像的那句」一起看 ——",
            "分数高但两句话说的不是一件事，那是误杀；分数低而两句话是一件事，那是漏判。",
            "另外注意第一节那个「完全无关」的分：它是这台模型的分底（中文上不低），",
            "底越高，绝对阈值就越不好定 —— 这决定了是不是该用相对判据。",
            "",
            "重排序那一节同理，但它的分底低得多：不相关的句子是负的，所以阈值能落在",
            "「真重复（>=5）」和「同主题不同问题（<=0.4）」中间，左右都有余量。"]
    write(out)
    print(f"calibrate: sessions={len(session_ids)} crosses={n} killed@{CURRENT}="
          f"{sum(1 for it in all_scored if killed(it, CURRENT))} "
          f"widest@{CURRENT}="
          f"{sum(1 for it in all_scored if killed(it, CURRENT, wide=True))} "
          f"rank_killed@{CURRENT_RANK}="
          f"{sum(1 for it in all_scored if rank_killed(it, CURRENT_RANK))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
