from pydantic import BaseModel,Field
from dotenv import load_dotenv
import os

class MySQLSchema(BaseModel):
    sql:str = Field(description="sql语句")

