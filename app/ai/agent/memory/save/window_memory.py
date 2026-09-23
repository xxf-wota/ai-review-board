import json
import os

import redis.asyncio as redis
from dotenv import load_dotenv

load_dotenv()

"""
短期记忆之一：窗口记忆
只保留最近若干轮对话，超过就丢掉，适合放进每次请求的提示词
存在 Redis 的 list 里，带过期时间，会话结束后自动清理
"""


class WindowMemory:

    def __init__(self, session_id):
        self.redis = redis.StrictRedis(host="localhost", port=6379, db=0)
        self.session_id = session_id
        self.key = f"window_memory:{self.session_id}"
        # 保留多少条消息。原代码直接 int(os.getenv(...))，
        # 环境变量没配就 int(None) 崩掉，所以这里给默认值
        self.window_size = int(os.getenv("WINDOW_MEMORY_ROUNDS") or 40)
        # 过期时间（秒）
        self.window_limit_time = int(os.getenv("WINDOW_MEMORY_TIME") or 86400)

    # 追加一条记忆，并裁剪到窗口大小
    async def save(self, role: str, content: str):
        data = {"role": role, "content": content}
        await self.redis.rpush(self.key, json.dumps(data, ensure_ascii=False))
        # 只保留最后 window_size 条，更早的丢掉
        await self.redis.ltrim(self.key, -self.window_size, -1)
        # 每次写入都续期，活跃会话不会中途过期
        await self.redis.expire(self.key, self.window_limit_time)

    # 取出全部窗口记忆；没有就返回空列表（原代码没有 key 时返回 None，调用方会崩）
    async def query(self):
        if await self.redis.exists(self.key):
            data = await self.redis.lrange(self.key, 0, -1)
            return [json.loads(i) for i in data]
        return []
