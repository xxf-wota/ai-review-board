from langchain_openai import ChatOpenAI
import os
from dotenv import load_dotenv
from vosk import Model
load_dotenv()

"""
    基于单例模式的模型封装
"""

# 定义模型类
class MyModel:
    # 定义在线模型的私有属性
    _model = None
    # 定义本地模型的私有属性
    _local_model = None
    # 定义语音识别模型的私有属性
    _vosk_model = None

    # 获得在线模型的函数
    @staticmethod
    def get_model():
        # 如果模型为空才需要调用
        if MyModel._model is None:
            MyModel._model = ChatOpenAI(
                model=os.getenv("MODEL_LINE_NAME"),
                api_key=os.getenv("DASHSCOPE_API_KEY"),
                base_url=os.getenv("OPENAI_API_BASE"),
                streaming=True,
                # 关闭思考模式
                extra_body={
                    "enable_thinking": False,
                }
            )
        return MyModel._model

    # 获得本地模型的函数
    @staticmethod
    def get_local_model():
        # 如果模型为空才需要调用
        if MyModel._local_model is None:
            # 如果使用ChatOllama来加载大模型，则不需要配置api_key
            MyModel._local_model = ChatOpenAI(
                model=os.getenv("MODEL_LOCAL_NAME"),
                api_key="123", # 我的电脑必须配置api_key，否则会报错找不到api_key
                base_url=os.getenv("LOCAL_URL"),
                streaming=True,
                extra_body={
                    "enable_thinking": False,
                }
            )
        return MyModel._local_model

    # 获取语音识别模型的函数
    @staticmethod
    def get_vosk_model():
        # 如果模型为空才需要调用
        if MyModel._vosk_model is None:
            # 模型路径
            model_path = os.getenv("VOSK_PATH")
            # 加载模型
            MyModel._vosk_model = Model(model_path=model_path)
        return MyModel._vosk_model

if __name__ == '__main__':
    local_model = MyModel.get_local_model()
    rs = local_model.invoke("你好")
    print(rs)
    for chunk in [rs]:
        print(chunk.content, end="")