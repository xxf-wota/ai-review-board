import redis.asyncio as redis

"""
用户画像记忆存储
"""
class ProfileMemory:

    def __init__(self,user_id):
        self.redis = redis.StrictRedis(host="localhost", port=6379, db=0, socket_timeout=3)
        self.key = f"profile:{user_id}"
    #保存
    async def save(self ,hashkey,value):
        await self.redis.hset(self.key, hashkey, value)
    #查询
    async def query(self):
        #查询某个用户的用户画像
        rs = await self.redis.hgetall(self.key)
        data =""
        if rs:
            for hashkey,value in rs.items():
                value = await self.redis.hget(self.key, hashkey)
                data += f"{hashkey.decode()}:{value.decode()}\n"
        return data
if __name__ =="__main__":
   p = ProfileMemory(1)
   p.save("name","张三")
   p.save("age",23)
   #查询
   p.query()





