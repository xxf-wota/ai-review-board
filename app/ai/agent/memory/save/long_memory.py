import chromadb
import os
from dotenv import load_dotenv
import uuid
import asyncio


class LongMemory:

    def __init__(self):
        load_dotenv()
        path = os.getenv("CHROMA_PATH")
        self.client = chromadb.PersistentClient(path)
        self.collection = self.client.get_or_create_collection("long_memory")

    async def save(self, user_id, query):
        user_id = str(user_id)
        id = f"memory_{user_id}+{uuid.uuid4()}"
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self.collection.add(
                ids=[id],
                documents=[query],
                metadatas=[{"user_id": user_id}]
            )
        )

    async def query(self, user_id, question):
        user_id = str(user_id)
        loop = asyncio.get_event_loop()
        rs = await loop.run_in_executor(
            None,
            lambda: self.collection.query(
                query_texts=[question],
                n_results=3,
                where={"user_id": user_id}
            )
        )
        docs = rs.get("documents") or [[]]
        return docs[0] if docs else []


if __name__ == "__main__":
    async def main():
        memory = LongMemory()
        print("LongMemory 创建完成")
        rs = await memory.query("1", "我喜欢什么")
        print("查询完成：", rs)

    asyncio.run(main())