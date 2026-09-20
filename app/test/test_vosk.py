from vosk import Model, KaldiRecognizer  # Vosk语音识别核心库
import pyaudio                           # 音频输入输出库
import json                              # 处理JSON格式的识别结果
from dotenv import load_dotenv           # 环境变量管理
import os                                # 操作系统接口
import time

load_dotenv()
def test():
    model_path= os.getenv("VOSK_PATH")
    print("模型路径:", model_path)
    #加载模型
    model = Model(model_path=model_path)
    #------------加载本地音频配置--------------
    p = pyaudio.PyAudio()  # 创建PyAudio实例管理音频设备

    stream = p.open(
        format=pyaudio.paInt16,  # 16位整数格式
        channels=1,  # 单声道
        rate=16000,  # 16kHz采样率
        input=True,  # 输入模式（录音）
        frames_per_buffer=4096  # 每个缓冲区4096帧
    )
    #加载音频配置，获取vosk识别器，采样率和模型采样率必须保持一致
    re = KaldiRecognizer(model,16000)

    #提示用户开始说话
    print("开始说话...")
    #开始音频
    stream.start_stream()
    try:
        #开始识别
        # 在模型打印前不要使用print，否则会出现无法提取text中内容的情况
        while True:
            data = re.AcceptWaveform(stream.read(4096)) # 读取音频数据
            if data:
                rs = json.loads(re.Result())
                if rs["text"]:
                    print(f"{rs['text']}")
    except Exception as e: # 捕获ctrl+c中断
        print(f"停止录音")
    finally:  # 无论是否异常都会执行
        stream.stop_stream()  # 停止音频流
        stream.close()  # 关闭音频流
        p.terminate()  # 释放PyAudio资源

if __name__ =="__main__":
    test()



