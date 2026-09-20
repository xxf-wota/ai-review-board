from langchain.tools import tool
from app.ai.tool.schema.mysql_schema import MySQLSchema
from dotenv import load_dotenv
import os
import pymysql

load_dotenv()
@tool(args_schema=MySQLSchema)
def mysql_tool(sql:str) -> str:
    """
     执行 sql 语句查询
     数据库模型：
          user_info 用户信息表，字段：user_id 用户编号 user_name 用户名 email 邮箱 department 部门 num 限制数量
     规则:
          - 禁止生成DELETE,UPDATE,INSERT,DROP,CREATE,ALTER 语句
    """
    con = None
    cursor = None
    try:
        host = os.getenv("MYSQL_HOST")
        port = os.getenv("MYSQL_PORT")
        user = os.getenv("MYSQL_USER")
        password = os.getenv("MYSQL_PASSWORD")
        db_name = os.getenv("MYSQL_DATABASE")
        if not host or not port or not user or not password or not db_name:
            return "数据库连接参数未配置"

        #兜底操作
        if  "DELETE" in sql.upper() or "UPDATE" in sql.upper() or "INSERT" in sql.upper() or "DROP" in sql.upper() or "CREATE" in sql.upper() or "ALTER" in sql.upper():
            return "禁止执行非法sql语句操作"
        con =pymysql.connect(
            host =host,
            port=int(port),
            user=user,
            password=password,
            db=db_name,
            charset="utf8"#中文编码设置
        )
        cursor =con.cursor()
        #执行sql
        cursor.execute(sql)
        #事务提交，非查询操作
        #con.commit()
        rs = cursor.fetchall()
        return str(rs)
    except Exception as e:
        return str(e)
    finally:
        if cursor:
            cursor.close()
        if con:
            con.close()
if __name__ =='__main__':
    print(mysql_tool.invoke({
        "sql":f"select * from question_bank where question_subject='python' ORDER BY RAND() desc LIMIT 1"
    }))