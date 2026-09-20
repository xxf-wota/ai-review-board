from vosk import Model, KaldiRecognizer  # Vosk语音识别核心库
import pyaudio                           # 音频输入输出库
import json                              # 处理JSON格式的识别结果
from app.ai.model.my_model import MyModel
from fastapi import WebSocket
import asyncio
class VostAgent:
    _vosk = None
    def __init__(self):
        self.model = MyModel.get_vosk_model()
        self.stream = self.get_stream()
        self.re = KaldiRecognizer(self.model, 16000)
        # 是否在说话
        self.listing = False

    @staticmethod
    def get_vosk():
        if VostAgent._vosk is None:
            VostAgent._vosk = VostAgent()
            return VostAgent._vosk
        return VostAgent._vosk

    def get_stream(self):
        p = pyaudio.PyAudio() # 创建PyAudio对象管理音频设备

        self.stream = p.open(
            format=pyaudio.paInt16, # 16位有符号整数
            channels=1, # 单声道
            rate=16000, # 采样率
            input=True, # 输入模式（录音）
            frames_per_buffer=4096, # 每次读取的音频数据帧数
        )
        return self.stream

    # 开始说话的业务
    async def speak(self, ws: WebSocket):
        # 在点击语音按钮后就发发送开始说话的消息
        # 客户端就能接收到此消息，并进行录音
        await self.send_msg("开始说话", ws)
        # 开始录音
        self.listing = True
        # 开启语音识别
        self.stream.start_stream()
        while self.listing:
            # exception_on_overflow=False：缓冲区溢出时返回空数据，而不是抛 IOError
            data = await asyncio.to_thread(self.stream.read, 4096, False)
            # 音频必须通过 AcceptWaveform 喂进识别器；Result() 只负责取出结果，
            # 本身不接收音频，没喂过音频时它永远返回空串
            if self.re.AcceptWaveform(data):
                rs = json.loads(self.re.Result())
                if rs["text"]:
                    await self.send_msg(rs["text"], ws)

    # 信息推送和语音关闭
    async def send_msg(self, text: str, ws: WebSocket):
        if "开始说话" in text:
            await ws.send_text(text)
            return
        else:
            await ws.send_text(text)
            self.listing = False