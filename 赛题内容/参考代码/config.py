import os

# 输出数据根目录（可通过环境变量 TARGET_DIR 覆盖；默认为项目根目录，
# 书架数据为根目录下 bookshelf.json，生成结果写入 output_dir/ 等子目录）
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TARGET_DIR = os.environ.get("TARGET_DIR", _BASE_DIR)


def _env(key, default="xxxx"):
    """从环境变量读取配置，未设置时使用占位符。"""
    return os.environ.get(key, default)


# ---------------- 服务端点配置（请通过环境变量或修改此处默认值填入） ----------------

# 声音注册（enroll）服务地址
ENROLL_SERVER_URL = _env("ENROLL_SERVER_URL")

# 语音克隆 TTS 流式接口
voice_clone_tts_api_url = _env("VOICE_CLONE_TTS_API_URL")

# NLU 问答模型（OpenAI 兼容接口）
nlu_url = _env("NLU_URL")
nlu_api_key = _env("NLU_API_KEY")
nlu_model = _env("NLU_MODEL", "qwen3-30b-a3b")

# 图像理解（VL）模型
image_analysis_url = _env("IMAGE_ANALYSIS_URL")
image_api_key = _env("IMAGE_API_KEY")
image_model = _env("IMAGE_MODEL", "qwen3-vl-30b-a3b-instruct")

# 图生视频
image2video_api_key = _env("IMAGE2VIDEO_API_KEY")
image2video_model = _env("IMAGE2VIDEO_MODEL", "doubao-seedance-1.0-lite-i2v")
image2video_url = _env("IMAGE2VIDEO_URL")

# 音效生成
text2audio_url = _env("TEXT2AUDIO_URL")
text2audio_api_key = _env("TEXT2AUDIO_API_KEY")
text2audio_model = _env("TEXT2AUDIO_MODEL", "audio1.0")

# 背景音乐生成
text2music_url = _env("TEXT2MUSIC_URL")
text2music_api_key = _env("TEXT2MUSIC_API_KEY")
text2music_model = _env("TEXT2MUSIC_MODEL", "imagination-bgm")

# 图像生成
text2image_url = _env("TEXT2IMAGE_URL")
text2image_api_key = _env("TEXT2IMAGE_API_KEY")
text2image_model = _env("TEXT2IMAGE_MODEL", "gemini-3-pro-image")

# 故事写作（OpenAI 兼容接口）
simple_story_writer_url = _env("SIMPLE_STORY_WRITER_URL")
simple_story_writer_api_key = _env("SIMPLE_STORY_WRITER_API_KEY")
simple_story_writer_model = _env("SIMPLE_STORY_WRITER_MODEL", "deepseek-v3.2")

# ASR WebSocket 服务地址
ASR_WS_SERVER_URL = _env("ASR_WS_SERVER_URL")

# TTS API 地址
tts_api_url = _env("TTS_API_URL")
