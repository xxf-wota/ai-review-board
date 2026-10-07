"""把某场评审会的记录倒成 UTF-8 文件（控制台是 GBK，中文会乱码）"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))
from app.ai.tool import review_dao

sid = sys.argv[1]
lines = []
for r in review_dao.get_questions(sid):
    lines.append(f"#{r['id']} [{r['speaker_role']}/{r['question_type']}] {r['question']}")
    lines.append(f"     答: {r['student_answer'][:60]}")
    lines.append(f"     判: {r['verdict']} sev={r['severity']}")
with open(os.path.join(ROOT, "data", "_last_session_rows.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print(f"rows={len(lines) // 3}")
