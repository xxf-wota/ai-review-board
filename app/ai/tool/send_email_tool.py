from langchain.tools import tool
from dotenv import load_dotenv
import os
from email.mime.text import MIMEText
import smtplib
from app.ai.tool.schema.send_email_schema import EmailParams

load_dotenv()

# 定义发送邮件函数
@tool("send_email_tool", args_schema=EmailParams) # args_schema：参数校验
def send_email_tool(to: str, subject: str, content: str):
    """
        发送邮件，发送通知，发送消息
    """
    try:
        # 配置发送信息
        host = os.getenv("EMAIL_HOST")
        port = int(os.getenv("EMAIL_PORT"))
        password = os.getenv("EMAIL_PASSWORD")
        sender = os.getenv("EMAIL_FROM")

        if not to or not subject or not content or not sender:
            return "参数不能为空"

        # 配置邮箱内容对象
        message = MIMEText(content)
        message['To'] = to
        message['Subject'] = subject
        message['From'] = sender

        # 发送邮件
        # 连接SMTP服务器，用这种方法可以不用开启tls，即 smtp.starttls()
        with smtplib.SMTP_SSL(host, port) as smtp:
            # 校验登录信息
            smtp.login(sender, password)
            # 发送邮件
            smtp.sendmail(sender, to, message.as_string())

        return "邮件发送成功"
    except Exception as e:
        print(f"发送邮件失败：{e}")
        return f"发送邮件失败：{e}"
if __name__ == '__main__':
    rs = send_email_tool.invoke({"to": "1359525405@qq.com", "subject": "测试邮件", "content": "这是一封测试邮件"})
    print(rs)