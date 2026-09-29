"""
发邮件（只给登录验证码用）

和原来的 send_email_tool 不是一回事：那个是包装成 LangChain 工具给 agent 调的
（已经删了），这个是后端自己用的 —— 把一封纯文本邮件发出去就完事。

两点要注意：
1. smtplib 是同步的，直接调会把事件循环卡住，所以套一层 to_thread 扔到线程里跑
2. .env 里配的是 QQ 邮箱（smtp.qq.com:465），走 SSL，所以用 SMTP_SSL，
   不需要 smtp.starttls()

发不出去就让异常冒上去。登录接口要如实告诉用户"没发成功"，
不做什么"发失败就返回一个演示码"的兜底 —— 那样登录就是摆设了。
"""
import asyncio
import os
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

from dotenv import load_dotenv

load_dotenv()


def _send(to: str, subject: str, content: str):
    host = os.getenv("EMAIL_HOST")
    port = int(os.getenv("EMAIL_PORT") or 465)
    password = os.getenv("EMAIL_PASSWORD")
    sender = os.getenv("EMAIL_FROM")

    if not (host and password and sender):
        raise RuntimeError("邮件配置不全，检查 .env 里的 EMAIL_HOST / EMAIL_FROM / EMAIL_PASSWORD")

    message = MIMEText(content, "plain", "utf-8")
    message["To"] = to
    message["From"] = formataddr((str(Header("AI 答辩陪练", "utf-8")), sender))
    message["Subject"] = Header(subject, "utf-8")

    # 超时给 15 秒：SMTP 卡住的时候不能让前端一直转圈
    with smtplib.SMTP_SSL(host, port, timeout=15) as smtp:
        smtp.login(sender, password)
        smtp.sendmail(sender, [to], message.as_string())


async def send_mail(to: str, subject: str, content: str):
    """异步发一封邮件，失败直接抛异常"""
    await asyncio.to_thread(_send, to, subject, content)
