import pymysql
import os
from dotenv import load_dotenv
load_dotenv()

def get_mysql_conn():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST"),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DATABASE"),
        charset=os.getenv("MYSQL_CHARSET"),
        port=int(os.getenv("MYSQL_PORT")),
    )

def close_mysql_conn(cursor, conn):
    cursor.close()
    conn.close()


if __name__ == '__main__':
    print(get_mysql_conn())

