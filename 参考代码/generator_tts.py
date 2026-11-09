import requests
import json
import base64
from pydantic import BaseModel
from typing import Optional, List, Union
from sseclient import SSEClient
import os
import numpy as np

class SpeakInfo(BaseModel):
    voice_type: str = ""
    lang: str = "cn"
    audio_format: str = "wav"
    tts_model: str = "v2_llm"
    text_prompt: Optional[str] = None
    audio_prompt: Optional[List[int]] = None

# Models for request validation
class InferenceRequest(BaseModel):
    text: str
    trace_id: str = "unknown"
    voice_params: Optional[SpeakInfo] = {}

class InferenceOutput(BaseModel):
    status_code: int = 200
    error_code: int = 200
    error_message: str = "Successed"
    data: Union[bytes, str, None] = None
    text: Optional[str] = None
    pre_process_text: Optional[dict] = None
    phoneme: Optional[str] = None
    durations: Optional[list] = None
    audio_info: dict = {}
    prosody: Optional[str] = None
    g2p_outputs: Optional[str] = None
    inferdatas: Optional[list] = None
    words_duration: Optional[list] = None
    time_log: dict = {}
    trace_id: str = "test"

class TTSGenerator:
    def __init__(self, api_url, text_prompt, audio_prompt, trace_id="rate427a-db09-4e8f-a808-78bf5a021c4f"):
        self.api_url = api_url
        self.text_prompt = text_prompt or ""
        self.audio_prompt = audio_prompt or []
        self.trace_id = trace_id

    def infer_text(self, text, output_file=None, lang="cn"):
        """流式推理函数，支持SSE流式接收，返回生成音频文件路径"""
        # 使用纯文本 + voice_params 传递 prompt，避免 SSML 属性解析失败
        request_data = InferenceRequest(
            text=text,
            trace_id=self.trace_id,
            voice_params=SpeakInfo(
                lang=lang,
                audio_format="wav",
                tts_model="v2_llm",
                text_prompt=self.text_prompt,
                audio_prompt=self.audio_prompt
            )
        )

        response = requests.post(
            url=self.api_url,
            json=request_data.model_dump(),
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=60
        )

        if response.status_code != 200:
            print(f"TTS请求失败: {response.status_code}, {response.text[:500]}")
            return None

        content_type = response.headers.get("Content-Type", "")
        chunks = []

        if "text/event-stream" in content_type:
            try:
                # 手动处理 SSE 流式响应，避免 SSEClient 版本兼容问题
                for line in response.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    
                    # SSE 格式：data: {json_data}
                    if line.startswith('data: '):
                        data_str = line[6:]  # 去掉 "data: " 前缀
                        try:
                            response_dict = json.loads(data_str)
                            response_obj = InferenceOutput.model_validate(response_dict)
                            if response_obj.data:
                                chunks.append(base64.b64decode(response_obj.data))
                        except json.JSONDecodeError as e:
                            print(f"JSON解析错误: {e}")
                        except Exception as e:
                            print(f"处理数据错误: {e}")
            except Exception as e:
                print(f"SSE流处理错误: {e}")
                try:
                    print(f"响应内容预览: {response.text[:500]}")
                except Exception:
                    pass
        else:
            # 兼容非SSE响应（JSON）
            try:
                response_json = response.json()
                if isinstance(response_json, dict) and response_json.get("data"):
                    chunks.append(base64.b64decode(response_json["data"]))
                else:
                    print(f"非SSE响应且无data字段: {str(response_json)[:500]}")
            except Exception as e:
                print(f"非SSE响应解析失败: {e}, 内容预览: {response.text[:500]}")

        if not chunks:
            return None

        if not output_file:
            output_dir = "./tornado_client_stream"
            os.makedirs(output_dir, exist_ok=True)
            output_file = os.path.join(output_dir, "tornado_audio_combined.wav")

        # 尝试按 WAV 方式拼接
        try:
            import wave
            import io as io_module
            with wave.open(io_module.BytesIO(chunks[0]), "rb") as wf:
                params = wf.getparams()
                frames = [wf.readframes(wf.getnframes())]
            for chunk in chunks[1:]:
                with wave.open(io_module.BytesIO(chunk), "rb") as wf:
                    frames.append(wf.readframes(wf.getnframes()))
            with wave.open(output_file, "wb") as out_wf:
                out_wf.setparams(params)
                for frame in frames:
                    out_wf.writeframes(frame)
            return output_file
        except Exception as e:
            print(f"WAV 拼接失败，退化为字节拼接: {e}")
            with open(output_file, "wb") as f:
                for chunk in chunks:
                    f.write(chunk)
            return output_file
