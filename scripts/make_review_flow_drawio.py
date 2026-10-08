# -*- coding: utf-8 -*-
"""
生成 docs/评审团流程图.drawio（面试/答辩用的完整版，9 页）

为什么用脚本生成而不是手写 XML：
    这张图 9 页、上百个方框，手写 XML 少一个引号就打不开；
    而图里的口径要跟代码同步（状态只留索引、接话去重余弦 0.80、检查点体积…），
    写在脚本里改一个字重跑一次即可，比手拖可靠。

注意：它是**生成器**，重跑会覆盖 `docs/评审团流程图.drawio` 里手改过的内容。
想拖着改就直接改 .drawio；想改口径就改这里再跑一次。

用法：python scripts/make_review_flow_drawio.py
产物：docs/评审团流程图.drawio（9 页）+ 控制台打印每页的方框/连线数
"""

import html
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "评审团流程图.drawio")

# 配色（沿用原来那份 drawio 的视觉语言，评审一眼能认出来）
BLUE = ("#dae8fc", "#6c8ebf")      # 普通节点 / 步骤
PURPLE = ("#e1d5e7", "#9673a6")    # 调度中枢 / 判定
GREEN = ("#d5e8d4", "#82b366")     # 起止 / 模型
RED = ("#f8cecc", "#b85450")       # 结束 / 红线
ORANGE = ("#ffe6cc", "#d79b00")    # 一次 HTTP 请求的边界
GREY = ("#f5f5f5", "#999999")      # 说明 / 附注

EDGE = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;endFill=1;"


def esc(text):
    """转义成 drawio 的 value：XML 实体 + 换行用 &#xa;"""
    return html.escape(str(text), quote=True).replace("\n", "&#xa;")


class Page:
    """一页 drawio：负责发唯一 id、拼 mxCell"""

    def __init__(self, name, index):
        self.name = name
        self.index = index
        self.cells = []
        self.seq = 0
        self.counts = {"box": 0, "edge": 0, "note": 0}

    def _cid(self, prefix):
        self.seq += 1
        return f"p{self.index}{prefix}{self.seq}"

    def box(self, x, y, w, h, text, fill=None, stroke=None, shape="rounded",
            fs=12, bold=False, align="center", valign="middle", dashed=False):
        fill, stroke = fill or BLUE[0], stroke or BLUE[1]
        cid = self._cid("b")
        if shape in ("rounded", "rect"):
            style = f"rounded=1;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};"
        elif shape == "hexagon":
            style = f"hexagon;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};"
        elif shape == "ellipse":
            style = f"ellipse;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};"
        elif shape == "rhombus":
            style = f"rhombus;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};"
        elif shape == "cylinder":
            style = (f"shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;"
                     f"backgroundOutline=1;size=15;fillColor={fill};strokeColor={stroke};")
        else:  # text
            style = "text;html=1;strokeColor=none;fillColor=none;"
        style += f"fontSize={fs};"
        if bold:
            style += "fontStyle=1;"
        if dashed:
            style += "dashed=1;"
        style += f"align={align};verticalAlign={valign};"
        self.cells.append(
            f'        <mxCell id="{cid}" value="{esc(text)}" style="{style}" vertex="1" parent="1">\n'
            f'          <mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry" />\n'
            f'        </mxCell>'
        )
        self.counts["box"] += 1
        return cid

    def note(self, x, y, w, h, text, fs=12, bold=False, align="left", fill=None):
        """无边框文字块（说明、决策表、要点）"""
        fill = fill or "none"
        return self.box(x, y, w, h, text, fill=fill, stroke=fill, shape="text",
                        fs=fs, bold=bold, align=align, valign="top")

    def arrow(self, source, target, label="", dashed=False, color="#666666", fs=11):
        cid = self._cid("e")
        style = f"{EDGE}strokeColor={color};fontSize={fs};"
        if dashed:
            style += "dashed=1;"
        self.cells.append(
            f'        <mxCell id="{cid}" value="{esc(label)}" style="{style}" edge="1" '
            f'parent="1" source="{source}" target="{target}">\n'
            f'          <mxGeometry relative="1" as="geometry" />\n'
            f'        </mxCell>'
        )
        self.counts["edge"] += 1
        return cid

    def render(self):
        head = (
            f'  <diagram name="{esc(self.name)}" id="page-{self.index}">\n'
            f'    <mxGraphModel dx="900" dy="700" grid="1" gridSize="10" guides="1" '
            f'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
            f'pageWidth="1169" pageHeight="826" math="0" shadow="0">\n'
            f'      <root>\n'
            f'        <mxCell id="0" />\n'
            f'        <mxCell id="1" parent="0" />\n'
        )
        return head + "\n".join(self.cells) + "\n      </root>\n    </mxGraphModel>\n  </diagram>"


# ============================================================ 第 1 页：系统总览

def page_overview(p):
    p.note(30, 10, 900, 30, "① 系统总览：一次评审会的全链路", fs=18, bold=True)

    ui = p.box(30, 60, 430, 90,
               "浏览器（Vue2 单页，无构建）app/html/app.html\n"
               "· 左侧按钮切换两个智能体：模拟面试 / 交叉质询评审团\n"
               "· 评审会界面：评审气泡、⟶ 接话箭头、答题框、纪要 + 未答好清单")
    login = p.box(480, 60, 330, 90,
                  "login.html：邮箱 + 验证码（真发信 SMTP）\n"
                  "登录即建业务会话（user_id = email）\n"
                  "登录态存 sessionStorage")
    api = p.box(30, 190, 780, 110,
                "FastAPI · app/web/review_router\n"
                "POST /review/upload（Word / PDF / txt → 纯文本）\n"
                "POST /review/submit（抽要素表 + 落库）   GET /review/session/{id}（读回纪要 + 未答好清单）\n"
                "POST /review/meeting/start（SSE 开场）   POST /review/meeting/answer（SSE 交回答，skip=true 可跳过）",
                align="left")
    graph = p.box(30, 340, 780, 80,
                  "LangGraph ReviewGraph（与模拟面试图共用同一个 PostgreSQL 检查点，靠 thread_id 区分）\n"
                  "manager（同步调度中枢，Command(goto=…) 动态跳转）· extract · speaker · cross · judge",
                  fill=PURPLE[0], stroke=PURPLE[1])
    m1 = p.box(30, 460, 250, 80, "本地模型（优先）\nOllama qwen2.5:7b", fill=GREEN[0], stroke=GREEN[1])
    m2 = p.box(300, 460, 250, 80, "商业模型（失败降级）\nDashScope qwen3.7-flash", fill=GREEN[0], stroke=GREEN[1])
    m3 = p.box(570, 460, 240, 80, "本地 ONNX 向量模型\n接话去重（余弦 0.80）\n384 维 / 7 ms 一句", fill=GREEN[0], stroke=GREEN[1])
    db1 = p.box(30, 600, 240, 100, "PostgreSQL\n图检查点\n(AsyncPostgresSaver)\n+ 摘要记忆", shape="cylinder")
    db2 = p.box(290, 600, 250, 100, "MySQL\nreview_session（8 字段）\nreview_question（13 字段）", shape="cylinder")
    db3 = p.box(560, 600, 240, 100, "Redis\n会话 / 窗口记忆 / 画像\n/ 会话锁", shape="cylinder")
    db4 = p.box(820, 600, 240, 100, "Chroma\n长期记忆\n（模拟面试用；评审会不用）", shape="cylinder")

    p.note(840, 60, 300, 380,
           "为什么要这么分\n\n"
           "· 评审会只活在一场会议里\n"
           "  → 不需要四层记忆，只需要\n"
           "    检查点（把会议接到下一次请求）\n\n"
           "· 状态里不放问答正文\n"
           "  → 只留索引（id + 判定结果），\n"
           "    正文写 MySQL，按 id 现取\n\n"
           "· 模型本地优先、商业降级\n"
           "  → 演示走商业模型更稳；\n"
           "    本地 qwen2.5:7b 也能整场跑通",
           fs=12)

    p.arrow(login, ui, "登录后进单页", dashed=True)
    p.arrow(ui, api, "HTTP / SSE")
    p.arrow(api, graph, "驱动图")
    p.arrow(graph, m1, "调用（本地优先）", dashed=True)
    p.arrow(graph, m2, "降级", dashed=True)
    p.arrow(graph, m3, "接话去重打分", dashed=True)
    p.arrow(graph, db1, "每个超步写检查点", dashed=True)
    p.arrow(api, db2, "提交方案 / 读回纪要", dashed=True)
    p.arrow(graph, db2, "边开会边写透质询记录", dashed=True)
    p.arrow(graph, db3, "会话 / 锁", dashed=True, color="#999999")
    p.arrow(graph, db4, "（评审模块不用）", dashed=True, color="#cccccc")


# ============================================================ 第 2 页：节点拓扑 + 决策表

def page_topology(p):
    p.note(30, 10, 900, 30, "② 节点拓扑与 manager 决策表（改调度只改这里）", fs=18, bold=True)

    start = p.box(30, 60, 90, 44, "START", shape="ellipse", fill=GREEN[0], stroke=GREEN[1], bold=True)
    mgr = p.box(30, 160, 190, 80, "manager\n调度中枢（同步、零 I/O）",
                shape="hexagon", fill=PURPLE[0], stroke=PURPLE[1], bold=True, fs=13)
    end = p.box(30, 320, 190, 48, "END（图停住）", shape="ellipse", fill=RED[0], stroke=RED[1], bold=True)

    ext = p.box(420, 60, 300, 56, "extract　方案要素抽取\n（只能模型抽不出来时给兜底要素表）", align="left")
    spk = p.box(420, 150, 300, 56, "speaker　评审发言\n主问题 与 追问共用这一个节点", align="left")
    crs = p.box(420, 240, 300, 56, "cross　接话判定 + 接话发言\n（含炒冷饭去重）", align="left")
    jdg = p.box(420, 330, 300, 56, "judge　回答判定\n由该问题的提问者判", align="left")

    p.arrow(start, mgr)
    p.arrow(mgr, ext, "1 没有要素表")
    p.arrow(mgr, spk, "2 phase = main / followup")
    p.arrow(mgr, crs, "6 phase = cross 且还有额度")
    p.arrow(mgr, jdg, "7 phase = judge")
    p.arrow(mgr, end, "3/4/5 → 停住等回答\n8/9/10/11 → 散会 / 防跑飞", dashed=True)
    for node in (ext, spk, crs, jdg):
        p.arrow(node, mgr, "", dashed=True, color="#999999")

    p.note(760, 60, 380, 700,
           "manager 决策表（自上而下第一条命中生效）\n\n"
           "1  plan_elements 为空 → extract\n"
           "2  phase ∈ {main, followup} → speaker\n"
           "3  phase=cross 且 cross_checked → 停住等回答\n"
           "4  phase=cross 且 cross_in_issue ≥ 1 → 停住\n"
           "5  phase=cross 且 cross_total ≥ 6 → 停住\n"
           "6  phase=cross（上面都不满足）→ cross\n"
           "7  phase = judge → judge\n"
           "8  phase = advance → _route_after_judge\n"
           "9  phase = await_answer → END（防跑飞）\n"
           "10 phase = done → END\n"
           "11 其它阶段 → END + done（不让会议卡死）\n\n"
           "为什么画不出自动图：\n"
           "manager 用 Command(goto=…) 动态跳转，\n"
           "不是静态 conditional edge ——\n"
           "LangGraph 自带的 draw_mermaid()\n"
           "只会画出「节点全回中枢」，\n"
           "分支一条都看不到，所以这张图是手画的。",
           fs=11)


# ============================================================ 第 3 页：meeting_phase 状态机

def page_state_machine(p):
    p.note(30, 10, 900, 30, "③ meeting_phase 状态机（会议怎么往前走）", fs=18, bold=True)

    ext = p.box(40, 70, 150, 50, "extract", fill=PURPLE[0], stroke=PURPLE[1], bold=True)
    main = p.box(240, 70, 150, 50, "main", fill=PURPLE[0], stroke=PURPLE[1], bold=True)
    cross = p.box(440, 70, 170, 50, "cross", fill=PURPLE[0], stroke=PURPLE[1], bold=True)
    await_ = p.box(680, 70, 190, 50, "await_answer", fill=ORANGE[0], stroke=ORANGE[1], bold=True)
    judge = p.box(680, 200, 190, 50, "judge", fill=PURPLE[0], stroke=PURPLE[1], bold=True)
    adv = p.box(440, 200, 170, 50, "advance", fill=PURPLE[0], stroke=PURPLE[1], bold=True)
    fup = p.box(240, 200, 150, 50, "followup", fill=PURPLE[0], stroke=PURPLE[1], bold=True)
    done = p.box(40, 200, 150, 50, "done", fill=RED[0], stroke=RED[1], bold=True)

    p.arrow(ext, main, "抽完要素表")
    p.arrow(main, cross, "主问题问完")
    p.arrow(cross, await_, "接话环节结束")
    p.arrow(await_, judge, "学生交回答\n（带同一 session_id 重新 astream）", dashed=False)
    p.arrow(judge, adv, "")
    p.arrow(adv, await_, "本议题还有没判过的问题")
    p.arrow(adv, fup, "都答完了、有人没被说服\n且本议题还没追过")
    p.arrow(fup, await_, "追问完")
    p.arrow(adv, main, "议题收尾，换下一位评审")
    p.arrow(main, done, "轮次用完，散会", dashed=True, color="#b85450")

    # cross 的自环：每议题最多接一次话
    loop_id = p._cid("b")
    p.cells.append(
        f'        <mxCell id="{loop_id}" value="{esc("有人接话（每个议题最多 1 次）")}" '
        f'style="{EDGE}strokeColor=#666666;fontSize=11;exitX=1;exitY=0.2;entryX=1;entryY=0.8;" '
        f'edge="1" parent="1" source="{cross}" target="{cross}">\n'
        f'          <mxGeometry relative="1" as="geometry" />\n'
        f'        </mxCell>'
    )

    p.note(40, 300, 1080, 120,
           "两个容易讲错的点\n\n"
           "① await_answer 不是「挂起」：LangGraph 没有挂起语义。manager 在 await_answer 处 goto=END，这次 HTTP 请求就结束了，\n"
           "   状态被写进 PostgreSQL 检查点；学生答完再发一次请求，带同一个 thread_id，图从 START 进 manager，\n"
           "   看到 phase=judge 就知道该去判定 —— 和模拟面试续多轮是同一套路，所以进程重启也接得上。\n\n"
           "② 图会停在两种 END 上，含义完全不同：await_answer = 等学生回答（前端弹答题框）；done = 真的开完了（展示纪要 + 未答好清单）。",
           fs=12)

    p.note(40, 440, 1080, 340,
           "一个议题的完整生命周期\n\n"
           "main（某位评审提主问题）\n"
           "  → cross（别的评审可以当场接话反驳，每个议题最多 1 次）\n"
           "  → await_answer（停下等学生回答；本议题可能有好几条问题，按提问顺序一条条答）\n"
           "  → judge（由**这条问题的提问者**判定：答清楚了 / 答得不全 / 没答）\n"
           "  → advance（看整个议题决定下一步）\n"
           "       · 还有没判过的问题 → 继续 await_answer\n"
           "       · 都答完、有人没被说服且本议题还没追过 → followup（追问 1 层）\n"
           "       · 否则 → 换下一位评审（清掉 _ISSUE_RESET 的 10 个议题字段）\n"
           "  → 四位都问完 → done（散会）\n\n"
           "为什么同时只能答一条：屏幕上永远只有一个「待你回答」，\n"
           "不会出现三个人一起问、学生不知道该答谁（这个坑是照着真实界面截图改的）。",
           fs=12)


# ============================================================ 第 4 页：一次完整问答走位

def page_walkthrough(p):
    p.note(30, 10, 900, 30, "④ 一次完整问答的走位（同一个议题要 4 次 HTTP 请求）", fs=18, bold=True)

    steps = [
        ("1  speaker（main）：技术评审提主问题\n     question_index += 一条（落库拿到 id）；cross_checked = False", BLUE),
        ("2  cross：成本评审接话\n     question_index += 一条（question_type = cross，带 target_speaker）；cross_in_issue = 1", BLUE),
        ("3  manager：cross → await_answer（本议题第一条没判过的是技术评审那问）   ← 请求 ① 结束", ORANGE),
        ("4  前端：弹答题框「请回答 技术评审 的质询（本议题第 1/2 问）」", GREEN),
        ("5  POST /meeting/answer：校验检查点里确实是 await_answer，写 student_answer 重新 astream   ← 请求 ②", ORANGE),
        ("6  judge：技术评审判 unresolved、severity 4、followup_hint「按多少用户算的」\n     判定回填索引 + 回答与评语写回 MySQL 那条 → phase = advance", BLUE),
        ("7  manager：本议题还有没判过的（成本评审的接话）→ 停住等第 2 问   ← 请求 ② 结束", ORANGE),
        ("8  前端：答题框「请回答 成本与进度评审 的质询（本议题第 2/2 问）」", GREEN),
        ("9  POST /meeting/answer → judge：成本评审判完自己那条   ← 请求 ③", ORANGE),
        ("10 manager：都答完了、技术评审那条 unresolved 且有 hint → phase = followup，current_speaker = tech", BLUE),
        ("11 speaker（followup）：技术评审追问（按索引取回本议题问答 + followup_hint）   ← 请求 ③ 结束", ORANGE),
        ("12 POST /meeting/answer → judge：判追问那条   ← 请求 ④", ORANGE),
        ("13 manager：本议题已追过 → _next_speaker（换成本评审，清 10 个议题字段）", BLUE),
    ]
    y = 60
    prev = None
    for text, color in steps:
        fill, stroke = color
        cur = p.box(40, y, 760, 44, text, fill=fill, stroke=stroke, fs=11, align="left")
        if prev:
            p.arrow(prev, cur)
        prev = cur
        y += 52

    p.note(830, 60, 310, 700,
           "讲这段时强调什么\n\n"
           "· 同一个议题 4 次 HTTP 请求，\n"
           "  中间隔着学生在页面上的输入\n\n"
           "· 每次请求都从 START 进 manager，\n"
           "  靠检查点里的 meeting_phase\n"
           "  决定该干哪一步\n\n"
           "· 一个议题里可能好几位评审都\n"
           "  问了话，学生按提问顺序\n"
           "  一条条答，每条由那条问题的\n"
           "  提问者判 —— 谁的疑问谁负责\n\n"
           "· 界面上永远只有一个待答问题\n\n"
           "· 接话那条也要求学生回答，\n"
           "  否则「接话问了没人判」，\n"
           "  进不了未答好清单（踩过）",
           fs=11)


# ============================================================ 第 5 页：判定后三分支

def page_after_judge(p):
    p.note(30, 10, 900, 30, "⑤ 判定之后怎么走（_route_after_judge）", fs=18, bold=True)

    a = p.box(40, 60, 320, 48, "一次判定结束（phase = advance）", fill=PURPLE[0], stroke=PURPLE[1], bold=True)
    d1 = p.box(40, 140, 320, 80, "本议题还有\n没判过的问题吗？", shape="rhombus",
               fill=ORANGE[0], stroke=ORANGE[1])
    b1 = p.box(440, 150, 400, 60, "_await_pending：接着问下一条\n主问题、接话按提问顺序来", align="left")
    d2 = p.box(40, 260, 320, 90, "followup_depth < 1\n且本议题还没追过？", shape="rhombus",
               fill=ORANGE[0], stroke=ORANGE[1])
    b2 = p.box(440, 275, 400, 60, "_next_speaker：换下一位评审\n本轮没了就下一轮，轮次用完散会", align="left")
    d3 = p.box(40, 400, 320, 100, "找得到这样一条吗：\n判定非 resolved\n且 followup_hint 非空？", shape="rhombus",
               fill=ORANGE[0], stroke=ORANGE[1])
    b3 = p.box(440, 420, 400, 60, "goto speaker：由它的提问者追一层\nphase = followup，followup_depth = 1", align="left")

    p.arrow(a, d1)
    p.arrow(d1, b1, "有")
    p.arrow(d1, d2, "没有")
    p.arrow(d2, b2, "否")
    p.arrow(d2, d3, "是")
    p.arrow(d3, b2, "找不到")
    p.arrow(d3, b3, "找到")

    p.note(40, 540, 1080, 240,
           "_await_pending（决定学生现在答哪一条）\n"
           "    本议题（最后一条主问题往后那段）里，第一条还没判过的问题：\n"
           "        pending_question = 它的题干；pending_type = 它的类型；pending_from = 它的提问者；pending_id = 库里那条记录的 id\n"
           "        meeting_phase = await_answer，goto=END\n"
           "    兜底：一条都没找到（理论上不可能）→ 直接换下一位，不让会议卡住。\n\n"
           "_next_speaker（换人 / 换轮 / 散会）\n"
           "    speaker_index + 1 < len(speaker_order) → 换下一位，phase = main\n"
           "    本轮说完且 round < max_round        → round += 1，回到第一位\n"
           "    都不满足                            → END，phase = done\n"
           "    换人 / 换轮时把 _ISSUE_RESET 的 10 个字段一起清（漏一个就会出现「新问题沿用上一题的标记」）。",
           fs=12)


# ============================================================ 第 6 页：状态与落库

def page_state_and_db(p):
    p.note(30, 10, 900, 30, "⑥ 状态只留索引，正文放 MySQL（检查点体积降一个数量级）", fs=18, bold=True)

    state = p.box(30, 60, 330, 430,
                  "ReviewState（图的状态 = 检查点里那一份）\n\n"
                  "方案：plan_text（抽完要素即清空）、plan_elements\n"
                  "会话：session_id\n"
                  "会议控制：meeting_phase、round、max_round、\n"
                  "        speaker_order、speaker_index、current_speaker\n"
                  "当前议题：pending_question、pending_type、\n"
                  "        pending_from、pending_id、followup_depth、\n"
                  "        followup_hint、student_answer\n"
                  "防跑偏：cross_in_issue、cross_total、spoke_in_issue、\n"
                  "      cross_checked、max_cross_per_issue、max_cross_total\n"
                  "产出：question_index（全场问答的索引）\n\n"
                  "已删除的字段：messages / question_log /\n"
                  "unresolved / minutes / diagnosis",
                  align="left", fs=11)

    index = p.box(390, 60, 330, 430,
                  "question_index 的每一条（索引）\n\n"
                  "id                review_question 主键（对回正文的键）\n"
                  "round             第几轮\n"
                  "speaker_role      谁问的（tech/cost/compliance/user）\n"
                  "question_type     main / cross / followup\n"
                  "target_speaker    接话时被接的人\n"
                  "verdict           判定结果（resolved/partial/unresolved）\n"
                  "severity          1~5\n"
                  "followup_depth    追问层数\n"
                  "followup_hint     追问方向（manager 要读）\n"
                  "question          **只在这条还没答时留着**，判完即删\n\n"
                  "为什么 question 只留一会儿：\n"
                  "manager 是同步节点、零 I/O，\n"
                  "_await_pending 要把题干填进 pending_question，\n"
                  "这一下不能查库。",
                  align="left", fs=11)

    db = p.box(750, 60, 390, 430,
               "MySQL · review_question（13 字段）\n\n"
               "id, session_id, round, speaker_role, question_type,\n"
               "target_speaker, question, student_answer, verdict,\n"
               "verdict_comment, severity, followup_depth, created_at\n\n"
               "写入时机（边开会边写透）\n"
               "· store.ask()          发言一抛出来就插一条，拿回 id\n"
               "· store.save_verdict() 学生答完把回答/判定/评语写回那条\n"
               "· 散会不再整批重写（重写会把 id 全换掉）\n\n"
               "取正文的入口（store/question_store.py，唯一出口）\n"
               "· rows_of(entries)      按索引取一批（判定拼本议题、接话拼最近 6 条）\n"
               "· question_of(entry)    取单条题干（接话要针对的那句）\n"
               "· all_rows(session_id)  整场取回：散会纪要 + 接话查重\n\n"
               "实测收益（同一场真实会议，scripts/measure_review_tokens.py）\n"
               "· 检查点累计写入 428 KB → 49.5 KB（8.7 倍）\n"
               "· 状态不再随问答条数平方增长\n"
               "· 提示词一字未改（动的是状态，不是提示词）",
               align="left", fs=11)

    p.arrow(index, db, "索引里的 id = 库里那条记录的主键\n（取正文全靠它）", dashed=True)
    p.arrow(state, index, "manager 只看索引\n（每秒都跑，零 I/O）", dashed=True)


# ============================================================ 第 7 页：接话去重防线

def page_dedup(p):
    p.note(30, 10, 900, 30, "⑦ 接话去重（炒冷饭防线）：向量余弦 0.80 + 整场比对", fs=18, bold=True)

    a = p.box(40, 60, 300, 48, "cross 生成一句接话候选", fill=BLUE[0], stroke=BLUE[1], bold=True)
    b = p.box(40, 140, 300, 60, "取整场之前问过的每一句\nstore.all_rows(session_id)", align="left")
    c = p.box(40, 230, 300, 60, "本地向量模型算余弦\nembed_util（384 维 / 7 ms）", align="left")
    d = p.box(40, 320, 300, 70, "最高的那句 ≥ 0.80 ？", shape="rhombus", fill=ORANGE[0], stroke=ORANGE[1])
    e = p.box(420, 230, 340, 60, "重试一次\n提示词里把被重复的那句原话念给模型听", align="left")
    f = p.box(420, 330, 340, 70, "重试后仍 ≥ 0.80 ？", shape="rhombus", fill=ORANGE[0], stroke=ORANGE[1])
    g = p.box(420, 440, 340, 56, "放弃这次接话\n（不落库，cross_checked = True 继续开会）",
              fill=RED[0], stroke=RED[1], align="left")
    h = p.box(420, 60, 340, 56, "正常：落库 + 推 speaker_start/token/speaker_end",
              fill=GREEN[0], stroke=GREEN[1], align="left")

    p.arrow(a, b)
    p.arrow(b, c)
    p.arrow(c, d)
    p.arrow(d, h, "否")
    p.arrow(d, e, "是")
    p.arrow(e, f)
    p.arrow(f, g, "是")
    p.arrow(f, h, "否")

    p.note(800, 60, 340, 700,
           "为什么不用字面相似度\n\n"
           "拿 8 场真模型评审会、26 条真实接话量过：\n"
           "· 字面 Jaccard ≥ 0.7 只挡住 1 条\n"
           "  （26 条里字面分最高才 0.593 ——\n"
           "   换个说法它完全认不出来）\n"
           "· 只比「对方原话 + 自己问过的」\n"
           "  会漏掉跨议题的逐字复述\n"
           "  （那既不是接话对象、也不是自己问的）\n"
           "· 换成余弦 + 整场比对：\n"
           "    0.80 挡 13 条；0.70 挡 19 条，\n"
           "    但会把 #202(0.856)/#221(0.767)/\n"
           "    #199(0.779) 这类「不同评审从各自\n"
           "    角度追问同一主题」误杀 ——\n"
           "    那正是交叉质询要的效果\n\n"
           "· 兜底：向量模型不可用 → 退回字面\n"
           "  DEDUP_BIGRAM = 0.7（只认逐字重复）\n"
           "· 真模型实测：一场会里拦截两次，\n"
           "  放弃了一条接话，会议照常往下走\n\n"
           "标定脚本 scripts/calibrate_dedup.py\n"
           "自检脚本 scripts/check_dedup.py（17 项）",
           fs=11)


# ============================================================ 第 8 页：SSE 事件时序

def page_events(p):
    p.note(30, 10, 900, 30, "⑧ SSE 事件时序（前端边收边渲染）", fs=18, bold=True)

    items = [
        ("meeting_start", "session_id、max_round", GREEN),
        ("elements", "要素表正文（前端渲染成卡片）", BLUE),
        ("speaker_start → token × N → speaker_end", "谁在说、逐字吐字、完整问题（接话时带 target/target_name → 画箭头）", BLUE),
        ("verdict", "role、name、question、verdict、severity、comment", PURPLE),
        ("await_answer", "role、name、question、question_type、question_index / issue_total", ORANGE),
        ("meeting_end", "cross_total、question_log（整场记录，从库现取）、unresolved（未答好清单）", RED),
        ("done", "每条流的收尾标记，前端据此关连接", GREY),
    ]
    y = 60
    prev = None
    for name, note, color in items:
        cur = p.box(40, y, 700, 60, f"{name}\n{note}", fill=color[0], stroke=color[1], fs=11, align="left")
        if prev:
            p.arrow(prev, cur)
        prev = cur
        y += 72

    p.note(780, 60, 360, 700,
           "讲这几个接口时要说的\n\n"
           "· 用 POST + SSE 而不是 EventSource：\n"
           "  方案正文可能上万字，塞不进 URL，\n"
           "  前端用 fetch + ReadableStream 读\n\n"
           "· 两个入口串起一场会：\n"
           "  /meeting/start 开场，\n"
           "  /meeting/answer 交回答\n\n"
           "· /meeting/answer 会先校验：\n"
           "  检查点里 phase 必须是 await_answer\n"
           "  （否则 404 / 409），\n"
           "  不然乱填 session_id 会让图凭空\n"
           "  从 START 开一场空会议\n\n"
           "· 散会时 router 只把会话状态改成\n"
           "  finished —— 质询记录在开会过程中\n"
           "  已经逐条写透了，不再整批重写\n\n"
           "· 落库失败只打日志、不往上抛：\n"
           "  前端已经边收边渲染了，\n"
           "  不能把已经开完的会毁掉",
           fs=11)


# ============================================================ 第 9 页：关键数字与问答要点

def page_numbers(p):
    p.note(30, 10, 900, 30, "⑨ 关键数字与面试问答要点", fs=18, bold=True)

    p.box(30, 60, 350, 150,
          "状态与成本\n"
          "· 检查点累计写入 428 KB → 49.5 KB（8.7 倍）\n"
          "· 一场会约 18 次模型调用 / 25k 字提示词\n"
          "· 一场会 7~9 问、4 次接话、1 次追问",
          fill=BLUE[0], stroke=BLUE[1], fs=12, align="left")

    p.box(400, 60, 350, 150,
          "接话去重（8 场 26 条真实接话标定）\n"
          "· 字面 Jaccard 0.7：只挡 1 条\n"
          "· 向量余弦 0.80（整场比对）：挡 13 条\n"
          "· 余弦 0.70：挡 19 条但误杀 3 条\n"
          "· 重排序（备选，未启用）：1 条不漏",
          fill=BLUE[0], stroke=BLUE[1], fs=12, align="left")

    p.box(770, 60, 370, 150,
          "防跑偏与质量\n"
          "· 每议题最多接话 1 次（MAX_CROSS_PER_ISSUE）\n"
          "· 全场最多接话 6 次（MAX_CROSS_TOTAL）\n"
          "· 同议题已发言者不再接话\n"
          "· 每议题最多追问 1 层（MAX_FOLLOWUP）\n"
          "· 跳过 → 不追问（短路不调模型）",
          fill=BLUE[0], stroke=BLUE[1], fs=12, align="left")

    p.box(30, 240, 350, 150,
          "M1~M5（全部已验收）\n"
          "M1 方案提交 + 要素抽取（8 字段 + 缺失项）\n"
          "M2 四位评审（相似度最高 0.07、越界 0/4）\n"
          "M3 交叉质询（5 请求 116 事件、配对 8/8）\n"
          "M4 作答 + 追问（跳过 0 追问、封顶 1 层）\n"
          "M5 纪要 + 未答好清单（7 条记录 / 4 条未答好）",
          fill=GREEN[0], stroke=GREEN[1], fs=12, align="left")

    p.box(400, 240, 350, 150,
          "验收脚本（都能变红）\n"
          "· check_startup.py  16 项（真起服务，跑 8801）\n"
          "· check_page.py     17 项（页面不变式）\n"
          "· check_review_index.py 29 项（离线索引化）\n"
          "· check_dedup.py    17 项（去重，--reverse 反向验）\n"
          "· dry_run_demo.py   真模型预跑（--expect-cross）",
          fill=GREEN[0], stroke=GREEN[1], fs=12, align="left")

    p.box(770, 240, 370, 150,
          "模型选型\n"
          "· 本地固定 qwen2.5:7b\n"
          "  （qwen3.5:9b 抽要素 313.8 秒，\n"
          "   且不认 response_format，会静默失败）\n"
          "· 正式演示走商业 qwen3.7-flash\n"
          "· 向量模型走本地 ONNX（不占显存、不装 torch）",
          fill=GREEN[0], stroke=GREEN[1], fs=12, align="left")

    p.note(30, 420, 1110, 360,
           "被问到这些时的答法\n\n"
           "问：为什么评审会不用四层记忆？\n"
           "答：一场评审会一次性结束，不需要跨会话记住谁；它需要的是「别忘了刚才说到哪」，那是检查点的事。\n"
           "    把学生的长期画像喂进来反而会干扰判定（评审该按方案本身判）。所以是「一套记忆基础设施、两种用法」。\n\n"
           "问：四个 AI 会不会说一样的话、或者变成无限互怼？\n"
           "答：两件事都有硬约束。视角不重复靠写死且互斥的关注点清单（实测两两相似度最高 0.07）；\n"
           "    互怼有三道额度锁 + 追问封顶 1 层 + 接话语义去重（余弦 0.80，判定标准是拿 8 场真数据量出来的）。\n\n"
           "问：为什么判定不给总分？\n"
           "答：一句话里塞三个问题是判不出「答没答到点上」的，所以我们只保留第一个问句、逐问题判定（答了 / 答偏了 / 没答），\n"
           "    分数只作参考维度。最终产出是「未答好的问题清单」，学生能当改稿工单用。\n\n"
           "问：这套东西的局限？\n"
           "答：① 接话内容质量随模型能力波动（机制、额度、箭头全部可复现，内容是概率性的）；\n"
           "    ② 评审会的 session_id 没有归属校验（面试那半有）；③ 画像与评估两处只走商业模型，没有本地兜底。\n"
           "    这三条都写进 README 的已知事项了 —— 宁可自己先说。",
           fs=12)


PAGES = [
    ("1. 系统总览", page_overview),
    ("2. 节点拓扑与决策表", page_topology),
    ("3. meeting_phase 状态机", page_state_machine),
    ("4. 一次完整问答走位", page_walkthrough),
    ("5. 判定后怎么走", page_after_judge),
    ("6. 状态与落库", page_state_and_db),
    ("7. 接话去重防线", page_dedup),
    ("8. SSE 事件时序", page_events),
    ("9. 关键数字与问答要点", page_numbers),
]


def verify(path, expected_pages):
    """自己验一遍再交出去：drawio 少一个引号就打不开，不能只靠肉眼看

    查三件事：XML 能不能解析、页数对不对、每页有没有重复 id（重复会让 drawio 报错或连线乱飞）
    """
    import xml.etree.ElementTree as ET

    problems = []
    try:
        root = ET.parse(path).getroot()
    except Exception as e:
        return [f"XML 解析失败：{type(e).__name__}: {e}"]
    pages = root.findall("diagram")
    if len(pages) != expected_pages:
        problems.append(f"页数不对：解析出 {len(pages)} 页，应当是 {expected_pages}")
    for page in pages:
        name = page.get("name")
        model = page.find("mxGraphModel")
        if model is None:
            problems.append(f"{name}：缺 mxGraphModel")
            continue
        ids = [cell.get("id") for cell in model.iter("mxCell")]
        dup = sorted({i for i in ids if i and ids.count(i) > 1})
        if dup:
            problems.append(f"{name}：重复 id {dup}")
        if "0" not in ids or "1" not in ids:
            problems.append(f"{name}：缺根 cell id=0 / id=1")
    return problems


def main():
    diagrams = []
    print(f"生成 {OUT}")
    for i, (name, builder) in enumerate(PAGES, start=1):
        page = Page(name, i)
        builder(page)
        diagrams.append(page.render())
        print(f"  第 {i} 页 {name}：方框 {page.counts['box']} / 连线 {page.counts['edge']}")
    xml = ('<mxfile host="app.diagrams.net" pages="%d">\n' % len(PAGES)
           + "\n".join(diagrams) + "\n</mxfile>\n")
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(xml)
    print(f"完成：{len(xml)} 字符 / {xml.count(chr(10)) + 1} 行 / {len(PAGES)} 页")

    problems = verify(OUT, len(PAGES))
    if problems:
        for item in problems:
            print(f"  [不通过] {item}")
        return 1
    print("自检：XML 可解析、页数与根 cell 齐全、无重复 id —— 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
