from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import requests
import base64
import time
import os
import json
import numpy as np

import asyncio
import websockets
from PIL import Image as PILImage
from flask import Response
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pydantic import BaseModel
from typing import Optional, List, Dict
# 关闭Flask/Werkzeug的HTTP请求日志
import logging

from ai_story_generate import GeminiAIStroyWriter, parse_output, Gemini3ProAgent, split_sfx_text, parse_list
from prompt_zh import chapter_writer_system, en_chapter_writer_system
from text import OpenAIStoryWriter as SimpleStoryWriter, parse_output as parse_output_simple
from generate_audio import AudioGenerator
from baike_kw_generate import GeminiAIBaikeGene
from exam_paper_generate import GeminiAIPaperGene
from generate_video import VideoGenerator, upload_image_to_oss
from generate_music import MusicGenerator
from generator_tts import TTSGenerator
# 直接使用 gemini_analyze.py（使用本地服务）
from gemini_analyze import ImageObjectAnalyzer
# 使用 ImageCapture 截取帧
from ImageCapture import ImageCapture
from generate_image import OpenAIImageGenerator
from nlu import QAAssistant
from config import *

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)
CORS(app)  # 允许跨域请求

# 配置静态文件服务
app.static_folder = '.'  # 设置静态文件目录为当前目录
app.static_url_path = ''  # 静态文件URL路径为空，允许直接通过根URL访问



# ========== NLU 问答 API ==========
video_executor = ThreadPoolExecutor(max_workers=5)
## nlu 问答 API生成器实例
# qa_assistant = QAAssistant(base_url=mlops_nlu_url, model=mlops_nlu_model)
# analyzer = ImageObjectAnalyzer(base_url=mlops_image_analysis_url, model=mlops_image_analysis_model)
qa_assistant = QAAssistant(base_url=nlu_url, api_key=nlu_api_key, model=nlu_model)
analyzer = ImageObjectAnalyzer(base_url=image_analysis_url, api_key=image_api_key, model=image_model)

# 视频生成器实例（全局）
video_generator = VideoGenerator(base_url=image2video_url, api_key=image2video_api_key, model=image2video_model, verbose=True)
# 全局视频生成线程池（避免每次调用都创建新的线程池）

# 音效生成器实例
sfx_generator = AudioGenerator(base_url=text2audio_url, api_key=text2audio_api_key, model=text2audio_model, verbose=False)
music_generator = MusicGenerator(base_url=text2music_url, api_key=text2music_api_key, model=text2music_model, verbose=False)
image_generator = OpenAIImageGenerator(base_url=text2image_url, api_key=text2image_api_key, model=text2image_model)

simple_story_writer = SimpleStoryWriter(base_url=simple_story_writer_url, api_key=simple_story_writer_api_key, model=simple_story_writer_model)

# 初始化试卷生成器
exam_generator = GeminiAIPaperGene(base_url=simple_story_writer_url, api_key=simple_story_writer_api_key, model=simple_story_writer_model)
# 百科问答生成器实例
baike_generator = GeminiAIBaikeGene(base_url=simple_story_writer_url, api_key=simple_story_writer_api_key, model=simple_story_writer_model)

deep_story_writer = GeminiAIStroyWriter(base_url=simple_story_writer_url, api_key=simple_story_writer_api_key, model=simple_story_writer_model)
                
cn_chapter_writer = Gemini3ProAgent(base_url=simple_story_writer_url, api_key=simple_story_writer_api_key, model=simple_story_writer_model, system_prompt=chapter_writer_system, track_history=False)
en_chapter_writer = Gemini3ProAgent(base_url=simple_story_writer_url, api_key=simple_story_writer_api_key, model=simple_story_writer_model, system_prompt=en_chapter_writer_system, track_history=False)

# 输出目录
OUTPUT_DIR = f"{TARGET_DIR}/output_dir"
BOOKSHELF_FILE = f"{TARGET_DIR}/bookshelf.json"
PROMPT_DIR = os.path.join(TARGET_DIR, "prompt_dir")
os.makedirs(PROMPT_DIR, exist_ok=True)
VOICE_CLONE_TTS_API_URL = globals().get('voice_clone_tts_api_url', tts_api_url)

class SpeakInfo(BaseModel):
    voice_type: str = "xiaosi"
    emotion: str = "neutral"
    lang: str = "cn"
    audio_format: str = "wav"
    rate: str = "1"
    pitch: str = "1"
    volume: str = "100"
    tts_model: str = "v2_llm"
    voice_clone: str = "false"
    text_prompt: Optional[str] = None
    audio_prompt: Optional[List[int]] = None


# 音色映射
VOICE_MAPPING = {
    'xiaosi': 'xiaosi',
    'xiaoyuan': 'tianmeixiaoyuan',
    'M001': 'M001',
    'F001': 'F001',
    'niudun': 'niudun',
    'oula': 'oula',
    # 英文音色
    'en_thinkie': 'thinkie',
    'en_F001': 'F001',
    'en_F002': 'F002',
}


def get_lang_by_voice(voice: str) -> str:
    """根据选择的音色决定语言参数"""
    return "en" if voice and voice.startswith("en") else "cn"


def resolve_voice_type(voice: str) -> str:
    """解析音色：优先映射内置音色，其次尝试自定义音色"""
    if voice in VOICE_MAPPING:
        return VOICE_MAPPING[voice]
    if voice:
        custom_path = os.path.join(PROMPT_DIR, f"{voice}.json")
        if os.path.exists(custom_path):
            return voice
    return VOICE_MAPPING.get('xiaosi', 'xiaosi')


def is_custom_voice(voice: str) -> bool:
    if not voice:
        return False
    custom_path = os.path.join(PROMPT_DIR, f"{voice}.json")
    return os.path.exists(custom_path)


def load_custom_voice_profile(voice: str) -> Optional[dict]:
    if not voice:
        return None
    profile_path = os.path.join(PROMPT_DIR, f"{voice}.json")
    if not os.path.exists(profile_path):
        return None
    try:
        with open(profile_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        text_prompt = data.get('text', '')
        token = data.get('token', [])
        audio_prompt = [int(x) for x in token]
        return {
            'text_prompt': text_prompt,
            'audio_prompt': audio_prompt
        }
    except Exception as e:
        print(f"加载自定义音色失败: {e}")
        return None


def generate_custom_audio(text: str, voice: str, output_path: str, lang: str = "cn") -> Optional[str]:
    profile = load_custom_voice_profile(voice)
    if not profile:
        return None
    if not output_path:
        fallback_dir = os.path.join(TARGET_DIR, "history_res")
        os.makedirs(fallback_dir, exist_ok=True)
        output_path = os.path.join(fallback_dir, f"custom_{int(time.time())}_{voice}.wav")
    generator = TTSGenerator(
        api_url=VOICE_CLONE_TTS_API_URL,
        text_prompt=profile['text_prompt'],
        audio_prompt=profile['audio_prompt'],
        trace_id=f"custom_{int(time.time())}_{voice}"
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    return generator.infer_text(text, output_file=output_path, lang=lang)



def get_session_dir():
    """创建以时间命名的会话目录"""
    
    # timestamp = datetime.now().strftime("%Y%m%d%H%M")
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3]  # 取前3位毫秒
    session_dir = os.path.join(OUTPUT_DIR, timestamp)

    tts_session_dir = os.path.join(session_dir, "tts")
    music_session_dir = os.path.join(session_dir, "music")
    images_session_dir = os.path.join(session_dir, "images")
    story_session_dir = os.path.join(session_dir, "story")
    combined_session_dir = os.path.join(session_dir, "combined")
    baike_session_dir = os.path.join(session_dir, "baike")
    overlay_session_dir = os.path.join(session_dir, "overlay")
    sfx_session_dir = os.path.join(session_dir, "sfx")
    exam_session_dir = os.path.join(session_dir, "sfx")
    # 创建子目录
    os.makedirs(tts_session_dir, exist_ok=True)
    os.makedirs(music_session_dir, exist_ok=True)
    os.makedirs(images_session_dir, exist_ok=True)
    os.makedirs(story_session_dir, exist_ok=True)
    os.makedirs(combined_session_dir, exist_ok=True)
    os.makedirs(baike_session_dir, exist_ok=True)
    os.makedirs(overlay_session_dir, exist_ok=True)
    os.makedirs(sfx_session_dir, exist_ok=True)
    os.makedirs(exam_session_dir, exist_ok=True)
    
    return session_dir, tts_session_dir, music_session_dir, images_session_dir, story_session_dir, combined_session_dir, baike_session_dir, overlay_session_dir, sfx_session_dir, exam_session_dir

@app.route('/tts_v2.html')
@app.route('/tts-story-book/tts_v2.html')
def serve_tts_html():
    return send_from_directory('.', 'tts_v2.html')


@app.route('/voice_register.html')
@app.route('/tts-story-book/voice_register.html')
def serve_voice_register_html():
    return send_from_directory('.', 'voice_register.html')


@app.route('/favicon.ico')
def favicon():
    """处理favicon请求（不带前缀）"""
    # 返回404响应，避免502错误
    return jsonify({'error': 'favicon not found'}), 404


@app.route('/tts-story-book/favicon.ico')
def favicon_with_prefix():
    """处理带部署前缀的favicon请求"""
    # 返回404响应，避免502错误
    return jsonify({'error': 'favicon not found'}), 404


@app.route('/api/enroll', methods=['POST'])
def enroll_voice():
    """自定义音色注册"""
    try:
        audio_file = request.files.get('audio')
        prompt_text = request.form.get('text')
        spkname = request.form.get('spkname', 'judy')

        if not audio_file or not prompt_text:
            return jsonify({"code": 400, "message": "缺少音频或文本"}), 400

        audio_bytes = audio_file.read()
        audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')

        request_data = {
            'prompt_audio': audio_base64,
            'prompt_text': prompt_text,
            'request_id': f'web_{int(time.time())}',
            'mode': 'enroll',
            'config': {
                'min_mos_score': 2.7,
                'max_wer': 0.15,
                'min_speed': 2.0,
                'max_speed': 8.0,
            }
        }

        response = requests.post(f"{ENROLL_SERVER_URL}/enroll", json=request_data, timeout=60)

        if response.status_code == 200:
            result = response.json()
            print(result)
            # 保存 token
            token = result.get('token')
            save_path = ""
            if token:
                save_path = f"{PROMPT_DIR}/{spkname}.json"
                payload = {"text": prompt_text, "token": [str(int(x)) for x in token]}
                with open(save_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False)
                result['save_status'] = f"已保存到 {save_path}"
            else:
                # 打印整个结果看看 token 到底在哪
                print(f"DEBUG: 远程返回结果中没有找到 'token' 字段。完整结果: {result}")
                result['save_status'] = "未保存：返回结果中缺少 token 字段"
            
            return jsonify(result)
        else:
            return jsonify({"code": response.status_code, "message": response.text}), response.status_code


        return jsonify({"code": response.status_code, "message": response.text}), response.status_code
    except Exception as e:
        return jsonify({"code": 500, "message": str(e)}), 500


@app.route('/api/custom-voices', methods=['GET'])
def list_custom_voices():
    """获取自定义音色列表"""
    try:
        if not os.path.exists(PROMPT_DIR):
            return jsonify({'success': True, 'voices': []})
        voices = []
        for filename in os.listdir(PROMPT_DIR):
            if filename.endswith('.json'):
                voices.append(os.path.splitext(filename)[0])
        voices.sort()
        return jsonify({'success': True, 'voices': voices})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

def load_bookshelf():
    """加载书架数据"""
    if os.path.exists(BOOKSHELF_FILE):
        try:
            with open(BOOKSHELF_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return []
    return []

def save_bookshelf(bookshelf):
    """保存书架数据"""
    with open(BOOKSHELF_FILE, 'w', encoding='utf-8') as f:
        json.dump(bookshelf, f, ensure_ascii=False, indent=2)



def infer_text(tts_api_url, text, voice_params=None, trace_id="test", max_retries=2, save_path=None):
    """调用TTS接口生成语音（带重试机制）"""
    if voice_params is None:
        voice_params = {
            "voice_type": "xiaosi",
            "emotion": "neutral",
            "lang": "cn",
            "audio_format": "wav",
            "rate": "1",
            "pitch": "1",
            "volume": "100",
        }
    
    # 如果voice_params是SpeakInfo对象，转换为字典
    if hasattr(voice_params, 'model_dump'):
        voice_params = voice_params.model_dump()

    # 构建请求数据
    request_data = {"text": text, "voice_params": voice_params, "trace_id": trace_id}

    # 重试机制
    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                print(f"==>> TTS 重试第 {attempt} 次...")
            
            # 发送 POST 请求到服务器
            start_response = time.time()
            response = requests.post(tts_api_url, json=request_data, timeout=30)  # 120秒超时
            
            end_response = time.time()
            print(f"==>> TTS response time: {end_response - start_response:.2f}秒")

            # 解析响应数据
            response_data = response.json()

            base64_data = response_data.get("data")
            if base64_data is None:
                raise Exception("No audio data in response")

            data = base64.b64decode(base64_data)

            # 保存音频文件（如果传入 save_path 就直接落到目标目录）
            target_path = save_path or f"{TARGET_DIR}/output_{int(time.time())}_{voice_params['emotion']}.{voice_params['audio_format']}"
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "wb") as f:
                print(f"==>> save_path: {target_path}")
                f.write(data)

            end = time.time()
            print(f"Total TTS time: {end - start_response:.2f}秒")

            return target_path, response_data
        
        except requests.exceptions.Timeout as e:
            print(f"TTS 请求超时 (尝试 {attempt + 1}/{max_retries + 1}): {str(e)}")
            if attempt >= max_retries:
                raise Exception(f"TTS 请求超时，已重试 {max_retries} 次")
            time.sleep(0.5)  # 等待2秒后重试
        
        except Exception as e:
            print(f"Error in infer_text (尝试 {attempt + 1}/{max_retries + 1}): {str(e)}")
            if attempt >= max_retries:
                raise
            time.sleep(0.5)  # 等待2秒后重试


@app.route('/api/generate-story-stream', methods=['POST'])
def generate_story_stream():
    """流式生成故事接口 - 每生成一个段落就返回"""
    
    data = request.json
    story_topic = data.get('story_topic', '')
    main_role = data.get('main_role', '')
    scene = data.get('scene', '')
    deep_think = data.get('deep_think', False)
    lang = data.get('lang', 'cn')
    knowledge_points = data.get('knowledge_points', '')

    language_dict = {"cn": "中文", "en":"英文"}
    language = language_dict.get(lang, "cn")

    
    
    if not story_topic or not main_role or not scene:
        return jsonify({'error': '请填写完整的故事参数'}), 400
    
    def generate():
        try:
            if deep_think:
                # 深度思考模式：逐章节生成
                print("🧠 [流式] 启用深度思考模式...")
                
                if language == "中文":
                    chapter_writer = cn_chapter_writer
                else:
                    chapter_writer = en_chapter_writer

                # 先生成大纲
                params = {
                    "story_title": story_topic,
                    "main_role": main_role,
                    "scene": scene,
                    "knowledge_points": knowledge_points,  # 新增：知识点参数
                    "language": language
                }
                outline = deep_story_writer.generate_outline(params)
                total_chapters = len(outline.get("story_outline", []))
                
                # 发送大纲信息
                yield f"data: {json.dumps({'type': 'outline', 'total': total_chapters})}\n\n"
                
                # 逐章节生成并立即返回
                
                all_pages = []
                for idx, chapter in enumerate(outline["story_outline"]):
                    print(f"   生成第 {idx + 1}/{total_chapters} 章...")

                    chapter_detail, success = chapter_writer.call(
                        json.dumps({
                            "completed_story": all_pages,
                            "current_chapter": chapter
                        }, ensure_ascii=False),
                        success_check_fn=parse_list
                    )
                    
                    # 重试直到成功
                    retry_count = 0
                    while success is False and retry_count < 5:
                        chapter_detail, success = chapter_writer.call(
                            json.dumps({
                                "completed_story": all_pages,
                                "current_chapter": chapter
                            }, ensure_ascii=False),
                            success_check_fn=parse_list
                        )
                        retry_count += 1
                    
                    if success:
                        pages = [page.strip() for page in eval(chapter_detail)]
                        page_content = "".join(pages)
                        all_pages.append(page_content)
                        
                        # 解析这一章的内容
                        parsed_items = split_sfx_text(page_content)
                        pure_text = "".join([item[0] for item in parsed_items if item[1] == 0])
                        
                        # 立即返回这一章
                        yield f"data: {json.dumps({'type': 'chapter', 'index': idx, 'content': page_content, 'pure_text': pure_text, 'parsed_items': parsed_items}, ensure_ascii=False)}\n\n"
                
                # 发送完成信号
                yield f"data: {json.dumps({'type': 'complete', 'total': len(all_pages)})}\n\n"
                
            else:
                # 简易模式：一次性生成（无法流式）
                print("📝 [流式] 简易模式生成故事...")
                
                result = simple_story_writer.call({
                    "story_topic": story_topic,
                    "main_role": main_role,
                    "scene": scene,
                    "knowledge_points": knowledge_points,  # 新增：知识点参数
                    "language": language
                })
                
                # 逐段返回
                parsed_pages = parse_output_simple(result['pages'])
                total = len(parsed_pages)
                
                yield f"data: {json.dumps({'type': 'outline', 'total': total})}\n\n"
                
                for page_num in sorted(parsed_pages.keys(), key=lambda x: int(x)):
                    page_items = parsed_pages[page_num]
                    pure_text = "".join([item[0] for item in page_items if item[1] == 0])
                    
                    yield f"data: {json.dumps({'type': 'chapter', 'index': int(page_num) - 1, 'pure_text': pure_text, 'parsed_items': page_items}, ensure_ascii=False)}\n\n"
                
                yield f"data: {json.dumps({'type': 'complete', 'total': total})}\n\n"
                
        except Exception as e:
            print(f"流式生成错误: {e}")
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    
    return Response(generate(), mimetype='text/event-stream')


@app.route('/api/generate-story', methods=['POST'])
def generate_story():
    """生成故事接口"""
    try:
        data = request.json
        story_topic = data.get('story_topic', '')
        main_role = data.get('main_role', '')
        scene = data.get('scene', '')
        deep_think = data.get('deep_think', False)
        lang = data.get('lang', 'cn')
        knowledge_points = data.get('knowledge_points', '')
        
        language_dict = {"cn": "中文", "en":"英文"}
        language = language_dict.get(lang, "中文")
        
        if not story_topic or not main_role or not scene:
            return jsonify({'error': '请填写完整的故事参数'}), 400
        
        if deep_think:
            # 深度思考模式：使用 GeminiAIStroyWriter
            print("🧠 启用深度思考模式...")
            
            # 生成故事
            pages_raw = deep_story_writer.call({
                "story_title": story_topic,
                "main_role": main_role,
                "scene": scene,
                "knowledge_points": knowledge_points,  # 新增：知识点参数
                "language": language
            })
            
            # 解析输出，获取带有音效标记的格式
            # 格式: {'1': [(text, 0/1), ...], '2': [...], ...}
            parsed_pages = parse_output(pages_raw)
            
            # 提取纯文本内容（type=0的文本），用于生成图片和背景音乐
            pure_text_pages = []
            for page_num in sorted(parsed_pages.keys(), key=lambda x: int(x)):
                page_items = parsed_pages[page_num]
                # 只取type=0的文本（普通文本）
                text_parts = [item[0] for item in page_items if item[1] == 0]
                pure_text_pages.append("".join(text_parts))
            
            # 合并为完整故事内容（纯文本版本）
            content = "\n\n".join(pure_text_pages)
            
            print(f"📖 深度思考模式生成 {len(parsed_pages)} 个段落")
            for page_num, items in parsed_pages.items():
                sfx_count = sum(1 for item in items if item[1] == 1)
                text_count = sum(1 for item in items if item[1] == 0)
                print(f"   段落{page_num}: {text_count}段文本, {sfx_count}个音效")
            
            return jsonify({
                'success': True,
                'story_content': content,
                'content': content,
                'pages': pure_text_pages,          # 纯文本段落列表，用于图片和背景音乐
                'parsed_pages': parsed_pages,       # 带音效标记的完整格式
                'deep_think': True,
                'message': '故事生成成功（深度思考模式）'
            })
        else:
            # 简易模式：使用 SimpleStoryWriter (text_new.py)
            print("📝 简易模式生成故事...")
            
            # 生成故事
            result = simple_story_writer.call({
                "story_topic": story_topic,
                "main_role": main_role,
                "scene": scene,
                "knowledge_points": knowledge_points,
                "language": language
            })
            
            # 解析输出，获取带有音效标记的格式（和深度思考模式相同）
            parsed_pages = parse_output_simple(result['pages'])
            
            # 提取纯文本内容（type=0的文本），用于生成图片和背景音乐
            pure_text_pages = []
            for page_num in sorted(parsed_pages.keys(), key=lambda x: int(x)):
                page_items = parsed_pages[page_num]
                # 只取type=0的文本（普通文本）
                text_parts = [item[0] for item in page_items if item[1] == 0]
                pure_text_pages.append("".join(text_parts))
            
            # 合并为完整故事内容（纯文本版本）
            content = "\n\n".join(pure_text_pages)
            
            print(f"📖 简易模式生成 {len(parsed_pages)} 个段落")
            for page_num, items in parsed_pages.items():
                sfx_count = sum(1 for item in items if item[1] == 1)
                text_count = sum(1 for item in items if item[1] == 0)
                print(f"   段落{page_num}: {text_count}段文本, {sfx_count}个音效")
            
            return jsonify({
                'success': True,
                'story_content': content,
                'content': content,
                'pages': pure_text_pages,           # 纯文本段落列表，用于图片和背景音乐
                'parsed_pages': parsed_pages,       # 带音效标记的完整格式
                'deep_think': False,                # 标记为简易模式
                'message': '故事生成成功（简易模式）'
            })
    
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/generate-image', methods=['POST'])
def generate_image_api():
    """单独生成图片接口 - 支持吉卜力风格图片生成"""
    try:
        data = request.json
        prompt = data.get('prompt', '')
        index = data.get('index', 0)
        story_title = data.get('story_title', '儿童故事')
        main_role = data.get('main_role', '可爱的小动物')
        scene = data.get('scene', '美丽的大自然')
        prev_image_path = data.get('prev_image_path', None)
        session_dir = data.get('session_dir', None)
        
        if not prompt:
            return jsonify({'error': '请提供图片生成提示'}), 400
        
        print(f"\n🎨 [图片生成] 开始生成第 {index + 1} 段的吉卜力风格图片")
        print(f"   故事: {story_title}, 主角: {main_role}, 场景: {scene}")
        print(f"   内容: {prompt[:50]}...")
        
        
        # 读取上一张图片的 base64（如果有）
        prev_image_base64 = None
        if prev_image_path and os.path.exists(prev_image_path):
            try:
                with open(prev_image_path, 'rb') as f:
                    prev_image_base64 = base64.b64encode(f.read()).decode('utf-8')
                print(f"   使用上一张图片保持风格一致性: {prev_image_path}")
            except Exception as e:
                print(f"   读取上一张图片失败: {e}")
        
        
        
        # 确保session_dir是绝对路径
        if session_dir and not os.path.isabs(session_dir):
            session_dir = os.path.join(TARGET_DIR, session_dir)
        
        # 为单段生成一张吉卜力风格图片
        result = image_generator.call({
            "story_text": prompt,
            "story_title": story_title,
            "main_role": main_role,
            "scene": scene,
            "prev_image_base64": prev_image_base64,
            "paragraph_index": index,
            "session_dir": session_dir
        })
        
        image_paths = result.get('image_paths', [])
        image_path = image_paths[0] if image_paths else None
        
        if image_path:
            print(f"   ✅ 第 {index + 1} 段图片生成成功: {image_path}")
            
            # 图片生成成功后，立即在后台启动视频生成
            if session_dir and os.path.exists(image_path):
                print(f"   🎬 立即启动第 {index + 1} 段视频生成...")
                try:
                    video_executor.submit(generate_video_task, prompt, image_path, session_dir, index)
                    print(f"   ✅ 第 {index + 1} 段视频生成任务已提交\n")
                except Exception as ve:
                    print(f"   ⚠️ 第 {index + 1} 段视频生成任务提交失败: {ve}\n")
            else:
                print(f"   ⚠️ 跳过视频生成（session_dir: {session_dir}, 图片存在: {os.path.exists(image_path) if image_path else False}）\n")
        else:
            print(f"   ⚠️ 第 {index + 1} 段图片生成未返回路径\n")
        
        # 将绝对路径转换为相对于 TARGET_DIR 的相对路径
        relative_image_path = image_path.replace(f'{TARGET_DIR}/', '') if image_path and image_path.startswith(f'{TARGET_DIR}/') else image_path
        
        return jsonify({
            'success': True,
            'image_path': relative_image_path,
            'message': '图片生成成功'
        })
    
    except Exception as e:
        print(f"   ❌ 第 {index + 1} 段图片生成失败: {str(e)}\n")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/generate-music', methods=['POST'])
def generate_music_api():
    """单独生成音乐接口"""
    
    # 音乐生成开关（如果遇到问题可以改为 False）
    ENABLE_MUSIC = True
    
    if not ENABLE_MUSIC:
        print(f"⚠️  [音乐生成] 已禁用，跳过")
        return jsonify({
            'success': True,
            'music_path': None,
            'message': '音乐生成已禁用'
        })
    
    try:
        data = request.json
        prompt = data.get('prompt', '')
        session_dir = data.get('session_dir', None)
        index = data.get('index', 0)
        duration = data.get('duration', 30)  # 支持自定义时长，默认30秒
        
        if not prompt:
            return jsonify({'error': '请提供音乐生成提示'}), 400
        
        print(f"\n🎵 [音乐生成] 开始生成音乐")
        print(f"   提示词: {prompt[:50]}...")
        print(f"   时长: {duration}秒")
        
        # 确保session_dir是绝对路径，并提前确定目标落盘路径
        if session_dir and not os.path.isabs(session_dir):
            session_dir = os.path.join(TARGET_DIR, session_dir)
        target_music_path = None
        if session_dir:
            music_dir = os.path.join(session_dir, "music")
            os.makedirs(music_dir, exist_ok=True)
            target_music_path = os.path.join(music_dir, f"music_{index}.wav")
        
        music_path = music_generator.generate(
            story_content=prompt,
            duration=int(duration),  # 使用传入的时长
            seed=int(time.time()) % 1000,
            output_file=target_music_path  # 直接写入目标目录
        )
        
        if music_path:
            print(f"   ✅ 音乐生成成功: {music_path}\n")
            # 将绝对路径转换为相对于 TARGET_DIR 的相对路径
            relative_music_path = music_path.replace(f'{TARGET_DIR}/', '') if music_path.startswith(f'{TARGET_DIR}/') else music_path
            return jsonify({
                'success': True,
                'music_path': relative_music_path,
                'message': '音乐生成成功'
            })
        else:
            print(f"   ⚠️  音乐生成失败，返回空路径\n")
            # 返回成功但音乐路径为空，让前端可以继续
            return jsonify({
                'success': True,
                'music_path': None,
                'message': '音乐生成失败，已跳过'
            })
    
    except Exception as e:
        print(f"   ❌ 音乐生成异常: {str(e)}\n")
        import traceback
        traceback.print_exc()
        # 返回成功但音乐路径为空，让前端可以继续
        return jsonify({
            'success': True,
            'music_path': None,
            'message': f'音乐生成失败: {str(e)}'
        })


@app.route('/api/generate-audio', methods=['POST'])
def generate_audio_api():
    """单独生成语音接口"""
    try:
        data = request.json
        text = data.get('text', '')
        voice = data.get('voice', 'xiaosi')
        session_dir = data.get('session_dir', None)
        index = data.get('index', 0)
        
        if not text:
            return jsonify({'error': '请提供要转换的文本'}), 400
        
        print(f"🎤 [语音生成] 开始生成语音")
        print(f"   文本: {text[:50]}...")
        print(f"   音色: {voice}")
        
        lang = get_lang_by_voice(voice)
        voice_type = resolve_voice_type(voice)

        # 构建speak标签格式的文本
        speak_text = f'<speak voice_type="{voice_type}" emotion="neutral" volume="90" rate="1.1" pitch="1.0" lang="{lang}" audio_format="wav" tts_model="v2_llm">{text.replace(">"," ").strip()}</speak>'

        # 设置语音参数
        voice_params = SpeakInfo(
            voice_type=voice_type,
            emotion="neutral",
            rate="1.1",
            pitch="1.0",
            volume="90",
            lang=lang,
            audio_format="wav",
            tts_model="v2_llm",
        )
        
        # 生成唯一的trace_id
        trace_id = f"audio_{int(time.time())}_{voice}"
        
        # 确定目标落盘路径（直接写入目标目录，不再搬运）
        if session_dir and not os.path.isabs(session_dir):
            session_dir = os.path.join(TARGET_DIR, session_dir)
        target_audio_path = None
        if session_dir:
            tts_dir = os.path.join(session_dir, "tts")
            os.makedirs(tts_dir, exist_ok=True)
            target_audio_path = os.path.join(tts_dir, f"tts_{index}.wav")
        
        # 自定义音色走克隆TTS流程
        if is_custom_voice(voice):
            audio_path = generate_custom_audio(text, voice, target_audio_path, lang=lang)
            if not audio_path:
                raise Exception("自定义音色生成失败")
        else:
            # 调用TTS接口，直接写入目标路径
            audio_path, tts_response = infer_text(
                tts_api_url,
                speak_text,
                voice_params,
                trace_id,
                save_path=target_audio_path
            )
        
        print(f"   ✅ 语音生成成功: {audio_path}\n")
        
        # 将绝对路径转换为相对于 TARGET_DIR 的相对路径
        relative_audio_path = audio_path.replace(f'{TARGET_DIR}/', '') if audio_path.startswith(f'{TARGET_DIR}/') else audio_path
        
        return jsonify({
            'success': True,
            'audio_path': relative_audio_path,
            'message': '语音生成成功'
        })
    
    except Exception as e:
        print(f"   ❌ 语音生成失败: {str(e)}\n")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/tts', methods=['POST'])
def generate_tts():
    """TTS生成接口（原有功能）"""
    try:
        data = request.json
        text = data.get('text', '')
        voice = data.get('voice', 'xiaomei')
        session_dir = data.get('session_dir', None)
        subfolder = data.get('subfolder', 'tts')  # 支持自定义子文件夹
        index = data.get('index', 0)
        
        if not text:
            return jsonify({'error': '文本不能为空'}), 400
        
        # 获取对应的音色
        voice_type = resolve_voice_type(voice)
        lang = get_lang_by_voice(voice)
        custom_voice = is_custom_voice(voice)
        
        
        # 构建speak标签格式的文本
        speak_text = f'<speak voice_type="{voice_type}" emotion="neutral" volume="90" rate="1.1" pitch="1.0" lang="{lang}" audio_format="wav" tts_model="v2_llm">{text.replace(">"," ").strip()}</speak>'
        
        # 设置语音参数
        voice_params = SpeakInfo(
            voice_type=voice_type,
            emotion="neutral",
            rate="1.1",
            pitch="1.0",
            volume="90",
            lang=lang,
            audio_format="wav",
            tts_model="v2_llm",
        )
        
        # 生成唯一的trace_id
        trace_id = f"web_{int(time.time())}_{voice}"
        
        # 确定目标落盘路径（直接写入目标目录，不再搬运）
        if session_dir and not os.path.isabs(session_dir):
            session_dir = os.path.join(TARGET_DIR, session_dir)
        target_audio_path = None
        if session_dir:
            target_dir = os.path.join(session_dir, subfolder)  # 默认 tts，小助手用 assistant
            os.makedirs(target_dir, exist_ok=True)
            timestamp = int(time.time() * 1000)
            target_audio_path = os.path.join(target_dir, f"{subfolder}_{timestamp}.wav")
        
        # 自定义音色走克隆TTS流程
        if custom_voice:
            audio_path = generate_custom_audio(text, voice, target_audio_path, lang=lang)
            if not audio_path:
                raise Exception("自定义音色生成失败")
        else:
            # 调用TTS接口，直接写入目标路径
            audio_path, response_data = infer_text(
                tts_api_url,
                speak_text,
                voice_params,
                trace_id,
                save_path=target_audio_path
            )
        
        # 将绝对路径转换为相对于 TARGET_DIR 的相对路径
        relative_path = audio_path.replace(f'{TARGET_DIR}/', '') if audio_path.startswith(f'{TARGET_DIR}/') else audio_path
        
        return jsonify({
            'success': True,
            'audio_path': relative_path,
            'message': '语音生成成功'
        })
    
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/audio/<path:filepath>')
def get_audio(filepath):
    """获取音频文件（支持子目录路径）"""
    try:
        # 添加调试输出
        print(f"📝 接收到的audio filepath: {filepath}")
        
        # 处理URL编码的路径
        import urllib.parse
        filepath = urllib.parse.unquote(filepath)
        filepath = filepath.replace('%5C', '/')
        # 将相对路径转换为绝对路径
        file_path = os.path.join(TARGET_DIR, filepath)
        print(f"📝 转换后的audio绝对路径: {file_path}")
        
        if os.path.exists(file_path):
            print(f"📝 音频文件存在，返回: {file_path}")
            return send_file(file_path, mimetype='audio/wav')
        else:
            print(f"📝 音频文件不存在: {file_path}")
            return jsonify({'error': '文件不存在', 'file_path': file_path}), 404
    except Exception as e:
        print(f"📝 音频请求错误: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/image/<path:filepath>')
def get_image(filepath):
    """获取图片文件（支持子目录路径）"""
    try:
        # 处理URL编码的路径
        import urllib.parse
        filepath = urllib.parse.unquote(filepath)
        filepath = filepath.replace('%5C', '/')
        # 将相对路径转换为绝对路径
        file_path = os.path.join(TARGET_DIR, filepath)
        if os.path.exists(file_path):
            return send_file(file_path, mimetype='image/png')
        else:
            return jsonify({'error': '文件不存在'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/video/<path:filepath>')
def get_video(filepath):
    """获取视频文件（支持子目录路径）"""
    try:
        # 处理URL编码的路径
        import urllib.parse
        filepath = urllib.parse.unquote(filepath)
        filepath = filepath.replace('%5C', '/')
        # 将相对路径转换为绝对路径
        file_path = os.path.join(TARGET_DIR, filepath)
        
        # print(f"📹 请求视频文件: {file_path}")
        if os.path.exists(file_path):
            # print(f"   ✅ 视频文件存在，返回文件")
            return send_file(file_path, mimetype='video/mp4')
        else:
            print(f"   ❌ 视频文件不存在: {file_path}")
            return jsonify({'error': '文件不存在'}), 404
    except Exception as e:
        print(f"   ❌ 获取视频文件错误: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/health')
def health():
    """健康检查"""
    return jsonify({'status': 'ok'})


# ========== 会话目录管理 API ==========
@app.route('/api/create-session', methods=['POST'])
def create_session():
    """创建新的会话目录"""
    try:
        session_dir, tts_session_dir, music_session_dir, images_session_dir, story_session_dir, combined_session_dir, baike_session_dir, overlay_session_dir, sfx_session_dir, exam_session_dir = get_session_dir()
        # 将绝对路径转换为相对于 TARGET_DIR 的相对路径
        relative_session_dir = session_dir.replace(f'{TARGET_DIR}/', '')
        return jsonify({
            'success': True,
            'session_dir': relative_session_dir
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/save-story-text', methods=['POST'])
def save_story_text():
    """保存故事文本"""
    try:
        data = request.json
        session_dir = data.get('session_dir', '')
        story_text = data.get('story_text', '')
        story_params = data.get('story_params', {})
        
        if not session_dir or not story_text:
            return jsonify({'success': False, 'error': '缺少必要参数'}), 400
        
        # 确保session_dir是绝对路径
        if not os.path.isabs(session_dir):
            session_dir = os.path.join(TARGET_DIR, session_dir)
        
        # 保存故事文本
        story_file = os.path.join(session_dir, "story", "story.txt")
        with open(story_file, 'w', encoding='utf-8') as f:
            f.write(story_text)
        
        # 保存故事参数
        params_file = os.path.join(session_dir, "story", "params.json")
        with open(params_file, 'w', encoding='utf-8') as f:
            json.dump(story_params, f, ensure_ascii=False, indent=2)
        
        return jsonify({
            'success': True,
            'story_file': story_file,
            'params_file': params_file
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/bookshelf', methods=['GET'])
def get_bookshelf():
    """获取书架列表"""
    try:
        bookshelf = load_bookshelf()
        return jsonify({
            'success': True,
            'bookshelf': bookshelf
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/bookshelf/load', methods=['POST'])
def load_book_from_shelf():
    """从书架加载绘本内容"""
    try:
        data = request.json
        session_dir = data.get('session_dir', '')
        
        # 处理session_dir，确保使用绝对路径
        if session_dir:
            # 如果是相对路径，转换为绝对路径
            if not os.path.isabs(session_dir):
                session_dir = os.path.join(TARGET_DIR, session_dir)
        
        if not session_dir or not os.path.exists(session_dir):
            return jsonify({'success': False, 'error': '目录不存在'}), 400
        
        # 读取故事文本
        story_file = os.path.join(session_dir, "story", "story.txt")
        story_text = ""
        if os.path.exists(story_file):
            with open(story_file, 'r', encoding='utf-8') as f:
                story_text = f.read()
        
        # 读取故事参数
        params_file = os.path.join(session_dir, "story", "params.json")
        story_params = {}
        if os.path.exists(params_file):
            with open(params_file, 'r', encoding='utf-8') as f:
                story_params = json.load(f)
        
        # 获取图片列表
        images_dir = os.path.join(session_dir, "images")
        images = []
        if os.path.exists(images_dir):
            for f in sorted(os.listdir(images_dir)):
                if f.endswith('.png'):
                    # 将绝对路径转换为相对于 TARGET_DIR 的相对路径
                    image_path = os.path.join(session_dir, "images", f)
                    relative_path = image_path.replace(f'{TARGET_DIR}/', '')
                    images.append(relative_path)
        
        # 获取TTS列表
        tts_dir = os.path.join(session_dir, "tts")
        # 分段故事（先定义，后面需要使用）
        paragraphs = [p.strip() for p in story_text.split('\n\n') if p.strip()]
        
        # 过滤掉音效标记，生成纯文本段落（用于字幕显示）
        import re
        def remove_sfx_tags(text):
            """移除 <SFX>...</SFX> 标记，只保留纯文本"""
            return re.sub(r'<SFX>.*?</SFX>', '', text).strip()
        
        pure_paragraphs = [remove_sfx_tags(p) for p in paragraphs]
        
        tts_files = []
        if os.path.exists(tts_dir):
            for f in sorted(os.listdir(tts_dir)):
                if f.endswith('.wav'):
                    # 将绝对路径转换为相对于 TARGET_DIR 的相对路径
                    audio_path = os.path.join(session_dir, "tts", f)
                    relative_path = audio_path.replace(f'{TARGET_DIR}/', '')
                    tts_files.append(relative_path)
        
        # 获取音乐列表（按段落索引匹配）
        music_dir = os.path.join(session_dir, "music")
        music_files = []
        if os.path.exists(music_dir):
            # 使用已定义的 pure_paragraphs 获取段落数量
            total_paragraphs = len(pure_paragraphs)
            # 按段落索引顺序匹配音乐文件
            for i in range(total_paragraphs):
                music_file = os.path.join(music_dir, f"music_{i}.wav")
                if os.path.exists(music_file):
                    # 将绝对路径转换为相对于 TARGET_DIR 的相对路径
                    relative_path = music_file.replace(f'{TARGET_DIR}/', '')
                    music_files.append(relative_path)
                else:
                    music_files.append(None)  # 该段落没有音乐文件
        
        # 读取试卷数据（如果存在）
        exam_data = None
        exam_file = os.path.join(session_dir, "exam", "exam.json")
        if os.path.exists(exam_file):
            try:
                with open(exam_file, 'r', encoding='utf-8') as f:
                    exam_data = json.load(f)
                print(f"   ✅ 加载试卷数据: {len(exam_data)} 道题")
            except Exception as e:
                print(f"   ⚠️ 读取试卷数据失败: {e}")
        
        # 获取合成后的音频文件（combined目录）
        combined_dir = os.path.join(session_dir, "combined")
        combined_files = []
        if os.path.exists(combined_dir):
            for f in sorted(os.listdir(combined_dir)):
                if f.endswith('.wav'):
                    # 将绝对路径转换为相对于 TARGET_DIR 的相对路径
                    audio_path = os.path.join(session_dir, "combined", f)
                    relative_path = audio_path.replace(f'{TARGET_DIR}/', '')
                    combined_files.append(relative_path)

        # 获取视频文件列表
        videos_dir = os.path.join(session_dir, "videos")
        video_files = []
        if os.path.exists(videos_dir):
            for f in sorted(os.listdir(videos_dir)):
                if f.endswith('.mp4'):
                    # 将绝对路径转换为相对于 TARGET_DIR 的相对路径
                    video_path = os.path.join(session_dir, "videos", f)
                    relative_path = video_path.replace(f'{TARGET_DIR}/', '')
                    video_files.append(relative_path)
        
        # 优先使用combined音频（包含音效），如果没有则用tts音频
        audio_files = combined_files if combined_files else tts_files
        
        # 加载百科数据（用于弹幕显示）
        baike_data = {}
        baike_dir = os.path.join(session_dir, "baike")
        if os.path.exists(baike_dir):
            for f in sorted(os.listdir(baike_dir)):
                if f.endswith('.json') and f.startswith('baike_'):
                    try:
                        # 从文件名提取段落索引，如 baike_0.json -> 0
                        idx = int(f.replace('baike_', '').replace('.json', ''))
                        baike_file = os.path.join(baike_dir, f)
                        with open(baike_file, 'r', encoding='utf-8') as bf:
                            baike_data[str(idx)] = json.load(bf)
                        print(f"   ✅ 加载百科数据: 段落{idx}, {len(baike_data[str(idx)])} 个问答")
                    except Exception as e:
                        print(f"   ⚠️ 加载百科文件 {f} 失败: {e}")
        
        # 加载用户答案（如果存在）
        exam_result_file = os.path.join(session_dir, "exam", "exam_result.json")
        exam_result = None
        if os.path.exists(exam_result_file):
            try:
                with open(exam_result_file, 'r', encoding='utf-8') as f:
                    exam_result = json.load(f)
                print(f"   ✅ 加载用户答案: {len(exam_result.get('user_answers', {}))} 道题")
            except Exception as e:
                print(f"   ⚠️ 读取用户答案失败: {e}")
        
        return jsonify({
            'success': True,
            'story_text': story_text,
            'story_params': story_params,
            'paragraphs': pure_paragraphs,  # 返回过滤后的纯文本段落
            'images': images,
            'tts_files': audio_files,  # 优先使用combined音频
            'music_files': music_files,
            'video_files': video_files,  # 添加视频文件路径
            'exam_data': exam_data,  # 添加试卷数据
            'baike_data': baike_data,  # 添加百科数据（弹幕）
            'exam_result': exam_result  # 添加用户答案和完成状态
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/bookshelf', methods=['POST'])
def add_to_bookshelf():
    """添加绘本到书架"""
    try:
        data = request.json
        book_name = data.get('book_name', '')
        session_dir = data.get('session_dir', '')
        story_params = data.get('story_params', {})
        
        if not book_name or not session_dir:
            return jsonify({'success': False, 'error': '缺少必要参数'}), 400
        
        # 创建书架目录
        bookshelf_dir = TARGET_DIR + '/bookshelf'
        if not os.path.exists(bookshelf_dir):
            os.makedirs(bookshelf_dir)
            print(f"📚 创建书架目录: {bookshelf_dir}")
        
        # 清理书名，移除不合法的文件名字符
        safe_book_name = "".join(c for c in book_name if c not in r'\/:*?"<>|')
        safe_book_name = safe_book_name.strip()
        if not safe_book_name:
            safe_book_name = f"绘本_{time.strftime('%Y%m%d%H%M%S')}"
        
        # 目标目录路径
        target_dir = os.path.join(bookshelf_dir, safe_book_name)
        
        # 如果目标目录已存在，添加时间戳后缀
        if os.path.exists(target_dir):
            target_dir = os.path.join(bookshelf_dir, f"{safe_book_name}_{time.strftime('%H%M%S')}")
        
        # 复制原始目录到书架目录
        source_dir = session_dir
        source_dir = os.path.join(TARGET_DIR, source_dir)
        if os.path.exists(source_dir):
            import shutil
            shutil.copytree(source_dir, target_dir)
            print(f"📚 复制绘本目录: {source_dir} -> {target_dir}")
            new_session_dir = target_dir
        else:
            print(f"⚠️ 原始目录不存在: {source_dir}，使用原路径")
            new_session_dir = session_dir
        
        bookshelf = load_bookshelf()
        
        # 添加新书（使用新的目录路径）
        new_book = {
            'id': len(bookshelf) + 1,
            'name': book_name,
            'session_dir': new_session_dir.replace(TARGET_DIR+"/", ''),
            'original_session_dir': session_dir,  # 保留原始路径
            'story_topic': story_params.get('story_topic', ''),
            'main_role': story_params.get('main_role', ''),
            'scene': story_params.get('scene', ''),
            'created_at': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        
        bookshelf.append(new_book)
        save_bookshelf(bookshelf)
        
        return jsonify({
            'success': True,
            'book': new_book
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/bookshelf/remove', methods=['POST'])
def remove_from_bookshelf():
    """从书架移除绘本"""
    try:
        data = request.json
        session_dir = data.get('session_dir', '')
        delete_files = data.get('delete_files', True)  # 是否同时删除文件
        
        if not session_dir:
            return jsonify({'success': False, 'error': '缺少 session_dir 参数'}), 400
        
        bookshelf = load_bookshelf()
        
        # 查找并移除对应的书
        original_length = len(bookshelf)
        removed_book = None
        new_bookshelf = []
        for book in bookshelf:
            if book.get('session_dir') == session_dir:
                removed_book = book
            else:
                new_bookshelf.append(book)
        
        if len(new_bookshelf) == original_length:
            return jsonify({'success': False, 'error': '未找到该绘本'}), 404
        
        save_bookshelf(new_bookshelf)
        
        # 如果需要删除文件，且目录在 bookshelf 文件夹内，则删除
        if delete_files:
            import shutil
            # 处理相对路径和绝对路径的情况
            if not os.path.isabs(session_dir):
                session_dir = os.path.join(TARGET_DIR, session_dir)
            
            # 检查是否在bookshelf目录下
            if f'{TARGET_DIR}/bookshelf/' in session_dir and os.path.exists(session_dir):
                shutil.rmtree(session_dir)
                print(f"📚 绘本文件夹已删除: {session_dir}")
        
        print(f"📚 绘本已从书架移除: {session_dir}")
        
        return jsonify({
            'success': True,
            'message': '绘本已从书架移除'
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/history_res/<path:filepath>')
def get_history_res(filepath):
    """获取历史资源文件（支持项目根目录下history_res目录的资源）"""
    try:
        # 构建完整的文件路径 - 优先从项目根目录下的history_res获取
        file_path = os.path.join(TARGET_DIR, 'history_res', filepath)

        if os.path.exists(file_path):
            # 自动检测文件类型
            if file_path.endswith('.png') or file_path.endswith('.jpg') or file_path.endswith('.jpeg'):
                return send_file(file_path, mimetype='image/png')
            elif file_path.endswith('.wav') or file_path.endswith('.mp3'):
                return send_file(file_path, mimetype='audio/wav')
            else:
                # 默认返回二进制文件
                return send_file(file_path)
        else:
            print(f"历史资源文件不存在: {file_path}")
            return jsonify({'error': '文件不存在'}), 404
    except Exception as e:
        print(f"获取历史资源错误: {e}")
        return jsonify({'error': str(e)}), 500

# ========== ASR 语音识别 API ==========
async def asr_send_json(websocket, json_data):
    await websocket.send(json.dumps(json_data))

async def asr_send_audio(websocket, audio_chunk):
    await websocket.send(audio_chunk)
    await asyncio.sleep(0.001)

async def asr_send_data(websocket, audio_data, init_json, end_json):
    try:
        if init_json:
            await asr_send_json(websocket, init_json)
        
        audio_chunks = [audio_data[i:i+3840] for i in range(0, len(audio_data), 3840)]
        for audio_chunk in audio_chunks:
            await asr_send_audio(websocket, audio_chunk)
        
        if end_json:
            await asr_send_json(websocket, end_json)
    except websockets.exceptions.ConnectionClosedOK:
        pass

async def asr_receive_data(websocket):
    offline_texts = []
    while True:
        data = await websocket.recv()
        if isinstance(data, str):
            response_json = json.loads(data)
            if response_json["code"] != 0:
                raise Exception(f"ASR error: {response_json}")
            
            if "data" in response_json and "showText" in response_json["data"] and response_json["type"] == "offline_asr":
                offline_text = response_json["data"]["showText"]
                offline_texts.append(offline_text)
            
            if "data" in response_json and response_json["data"]["end"]:
                await websocket.close()
                break
    return offline_texts

async def process_asr(audio_data):
    request_id = f"asr_{int(time.time() * 1000)}"
    
    init_json = {
        "type": "start",
        "request_id": request_id,
        "asr_wake_up_type": "action_wake_up",
        "continuous": "false",
        "oneshot_replace_word": "true",
        "enable_punc": "true",
        "max_start_silence": "7000",
        "max_end_silence": "800"
    }
    end_json = {
        "type": "end",
        "request_id": request_id
    }
    
    try:
        async with websockets.connect(ASR_WS_SERVER_URL, ping_interval=None) as websocket:
            await asyncio.gather(
                asr_send_data(websocket, audio_data, init_json, end_json),
                asr_receive_data(websocket)
            )
            # 重新连接获取结果
            async with websockets.connect(ASR_WS_SERVER_URL, ping_interval=None) as ws2:
                await asr_send_data(ws2, audio_data, init_json, end_json)
                offline_texts = await asr_receive_data(ws2)
                return offline_texts
    except Exception as e:
        print(f"ASR error: {e}")
        return []


@app.route('/api/asr', methods=['POST'])
def asr_recognize():
    """语音识别API - 接收音频文件，返回识别文本"""
    try:
        if 'audio' not in request.files:
            return jsonify({'success': False, 'error': '没有上传音频文件'}), 400
        
        audio_file = request.files['audio']
        
        # 保存临时文件
        temp_path = f"temp_asr_{int(time.time() * 1000)}.wav"
        audio_file.save(temp_path)
        
        # 读取音频数据（跳过wav头部44字节）
        with open(temp_path, 'rb') as f:
            f.seek(44)
            audio_data = f.read()
        
        # 调用ASR服务
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            offline_texts = loop.run_until_complete(process_asr(audio_data))
        finally:
            loop.close()
        
        # 删除临时文件
        if os.path.exists(temp_path):
            os.remove(temp_path)
        
        # 合并识别结果
        text = ' '.join(offline_texts) if offline_texts else ''
        
        return jsonify({
            'success': True,
            'text': text
        })
    except Exception as e:
        print(f"ASR API error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500




def overlay_sfx_on_tts(tts_file, sfx_file, output_file, sfx_volume=0.5, max_sfx_duration=3.0):
    """将音效叠加到TTS音频的末尾部分
    
    Args:
        tts_file: TTS音频文件路径
        sfx_file: 音效文件路径
        output_file: 输出文件路径
        sfx_volume: 音效音量（相对于TTS，默认0.5即一半）
        max_sfx_duration: 音效最大时长（秒），超过此时长只取最后这么多秒
        
    Returns:
        bool: 成功返回True，失败返回False
    """
    import wave
    import struct
    import array
    
    try:
        if not os.path.exists(tts_file):
            print(f"⚠️ TTS文件不存在: {tts_file}")
            return False
        if not os.path.exists(sfx_file):
            print(f"⚠️ 音效文件不存在: {sfx_file}")
            # 如果音效不存在，直接复制TTS
            import shutil
            shutil.copy(tts_file, output_file)
            return True
        
        # 读取TTS音频
        with wave.open(tts_file, 'rb') as tts_wf:
            tts_params = tts_wf.getparams()
            tts_frames = tts_wf.readframes(tts_wf.getnframes())
            tts_nframes = tts_wf.getnframes()
            tts_sample_width = tts_wf.getsampwidth()
            tts_framerate = tts_wf.getframerate()
            tts_nchannels = tts_wf.getnchannels()
        
        # 读取音效
        with wave.open(sfx_file, 'rb') as sfx_wf:
            sfx_frames = sfx_wf.readframes(sfx_wf.getnframes())
            sfx_nframes = sfx_wf.getnframes()
            sfx_sample_width = sfx_wf.getsampwidth()
            sfx_framerate = sfx_wf.getframerate()
            sfx_nchannels = sfx_wf.getnchannels()
        
        # 计算TTS时长（秒）
        tts_duration = tts_nframes / tts_framerate
        # 计算音效时长（秒）
        sfx_duration = sfx_nframes / sfx_framerate
        
        print(f"      TTS时长: {tts_duration:.2f}秒, 音效时长: {sfx_duration:.2f}秒")
        
        # 1. 音效只取最后3秒（如果超过3秒）
        max_sfx_frames = int(max_sfx_duration * sfx_framerate)
        if sfx_nframes > max_sfx_frames:
            # 只取音效的最后3秒
            sfx_start_frame = sfx_nframes - max_sfx_frames
            sfx_nframes = max_sfx_frames
            sfx_frames = sfx_frames[sfx_start_frame * sfx_sample_width * sfx_nchannels:]
            print(f"      音效截取最后 {max_sfx_duration} 秒")
        
        # 2. 如果TTS时长小于音效时长，将音效截取到TTS的时长
        if tts_nframes < sfx_nframes:
            # 截取音效到TTS的时长
            sfx_nframes = tts_nframes
            sfx_frames = sfx_frames[:sfx_nframes * sfx_sample_width * sfx_nchannels]
            print(f"      音效截取到TTS时长: {tts_duration:.2f}秒")
        
        # 计算音效在TTS中的起始位置（TTS末尾减去音效时长）
        sfx_duration_frames = sfx_nframes
        overlay_start = max(0, tts_nframes - sfx_duration_frames)
        
        # 转换为数组进行处理
        if tts_sample_width == 2:
            fmt = 'h'  # 16-bit
        else:
            fmt = 'b'  # 8-bit
        
        tts_samples = array.array(fmt, tts_frames)
        sfx_samples = array.array(fmt, sfx_frames)
        
        # 调整音效音量
        sfx_samples = array.array(fmt, [int(s * sfx_volume) for s in sfx_samples])
        
        # 叠加音效到TTS末尾
        samples_per_frame = tts_nchannels
        overlay_start_sample = overlay_start * samples_per_frame
        
        for i in range(len(sfx_samples)):
            tts_idx = overlay_start_sample + i
            if tts_idx < len(tts_samples):
                # 叠加并限制范围
                mixed = tts_samples[tts_idx] + sfx_samples[i]
                if tts_sample_width == 2:
                    mixed = max(-32768, min(32767, mixed))
                else:
                    mixed = max(-128, min(127, mixed))
                tts_samples[tts_idx] = mixed
        
        # 写入输出文件
        with wave.open(output_file, 'wb') as out_wf:
            out_wf.setparams(tts_params)
            out_wf.writeframes(tts_samples.tobytes())
        
        print(f"✅ 音效叠加成功: {output_file}")
        return True
        
    except Exception as e:
        print(f"❌ 音效叠加失败: {e}")
        import traceback
        traceback.print_exc()
        # 失败时返回原TTS
        try:
            import shutil
            shutil.copy(tts_file, output_file)
            return True
        except:
            return False

def concatenate_audio_files(audio_files, output_file):
    """拼接多个音频文件
    
    Args:
        audio_files: 音频文件路径列表
        output_file: 输出文件路径
        
    Returns:
        bool: 成功返回True，失败返回False
    """
    import wave
    import struct
    
    try:
        # 收集所有音频数据
        all_frames = []
        params = None
        
        for audio_file in audio_files:
            if not os.path.exists(audio_file):
                print(f"⚠️ 音频文件不存在: {audio_file}")
                continue
                
            with wave.open(audio_file, 'rb') as wf:
                if params is None:
                    params = wf.getparams()
                frames = wf.readframes(wf.getnframes())
                all_frames.append(frames)
        
        if not all_frames or params is None:
            print("❌ 没有可用的音频数据")
            return False
        
        # 写入拼接后的音频
        with wave.open(output_file, 'wb') as out_wf:
            out_wf.setparams(params)
            for frames in all_frames:
                out_wf.writeframes(frames)
        
        print(f"✅ 音频拼接成功: {output_file}")
        return True
        
    except Exception as e:
        print(f"❌ 音频拼接失败: {e}")
        return False

@app.route('/api/generate-paragraph-audio', methods=['POST'])
def generate_paragraph_audio_api():
    """为一个段落生成完整音频（包含TTS和音效拼接）
    
    请求格式:
    {
        "items": [["文本内容", 0], ["音效描述", 1], ...],
        "voice": "xiaosi",
        "session_dir": "output_dir/xxx",
        "paragraph_index": 0
    }
    
    - type=0: 普通文本，调用TTS
    - type=1: 音效描述，调用音效生成
    """
    try:
        data = request.json
        items = data.get('items', [])
        voice = data.get('voice', 'xiaosi')
        session_dir = data.get('session_dir', None)
        paragraph_index = data.get('paragraph_index', 0)
        
        if not items:
            return jsonify({'error': '请提供段落内容'}), 400
        
        print(f"\n🎬 [段落音频生成] 段落 {paragraph_index}")
        print(f"   共 {len(items)} 个片段，并行生成中...")
        
        voice_type = resolve_voice_type(voice)
        lang = get_lang_by_voice(voice)
        custom_voice = is_custom_voice(voice)
        
        # 确保session_dir是绝对路径
        if session_dir and not os.path.isabs(session_dir):
            session_dir = os.path.join(TARGET_DIR, session_dir)
        
        # 确定输出目录
        if session_dir:
            tts_dir = os.path.join(session_dir, 'tts')
            sfx_dir = os.path.join(session_dir, 'sfx')
            os.makedirs(tts_dir, exist_ok=True)
            os.makedirs(sfx_dir, exist_ok=True)
        else:
            tts_dir = os.path.join(TARGET_DIR, 'history_res')
            sfx_dir = os.path.join(TARGET_DIR, 'history_res')
            os.makedirs(os.path.join(TARGET_DIR, 'history_res'), exist_ok=True)
        
        # 统计TTS和音效的索引
        tts_count = 0
        sfx_count = 0
        for item in items:
            if item[1] == 0:
                tts_count += 1
            else:
                sfx_count += 1
        
        print(f"   TTS片段: {tts_count}, 音效片段: {sfx_count}")
        
        # 定义生成单个片段的函数
        def generate_single_item(idx, text, item_type, tts_idx, sfx_idx, custom_voice_enabled):
            """生成单个音频片段，返回 (原始索引, 音频路径)"""
            if item_type == 0:
                # TTS生成（带重试机制）
                max_retries = 3
                for retry in range(max_retries):
                    try:
                        output_file = os.path.join(tts_dir, f'tts_{paragraph_index}_{tts_idx}.wav')
                        if custom_voice_enabled:
                            audio_path = generate_custom_audio(text, voice, output_file, lang=lang)
                            if audio_path:
                                print(f"   [{idx}] ✅ 自定义TTS (尝试{retry+1}): {text[:20]}...")
                                return (idx, audio_path)
                            raise Exception("自定义音色生成失败")

                        speak_text = f'<speak voice_type="{voice_type}" emotion="neutral" volume="90" rate="1.1" pitch="1.0" lang="{lang}" audio_format="wav" tts_model="v2_llm">{text.replace(">"," ").strip()}</speak>'
                        
                        voice_params = SpeakInfo(
                            voice_type=voice_type,
                            emotion="neutral",
                            rate="1.1",
                            pitch="1.0",
                            volume="90",
                            lang=lang,
                            audio_format="wav",
                            tts_model="v2_llm"
                        )
                        
                        trace_id = f"para_{paragraph_index}_{tts_idx}_{int(time.time() * 1000)}_{idx}_{retry}"
                        audio_path, _ = infer_text(tts_api_url, speak_text, voice_params, trace_id)
                        
                        if audio_path:
                            import shutil
                            shutil.move(audio_path, output_file)
                            print(f"   [{idx}] ✅ TTS (尝试{retry+1}): {text[:20]}...")
                            return (idx, output_file)
                        else:
                            print(f"   [{idx}] ⚠️ TTS无数据 (尝试{retry+1}/{max_retries}): {text[:20]}...")
                            if retry < max_retries - 1:
                                time.sleep(0.5)  # 短暂等待后重试
                                continue
                    except Exception as e:
                        print(f"   [{idx}] ❌ TTS失败 (尝试{retry+1}/{max_retries}): {e}")
                        if retry < max_retries - 1:
                            time.sleep(0.5)
                            continue
                
                print(f"   [{idx}] ❌ TTS彻底失败: {text[:20]}...")
                return (idx, None)
            else:
                # 音效生成
                try:
                    output_file = os.path.join(sfx_dir, f'sfx_{paragraph_index}_{sfx_idx}.wav')
                    result_file = sfx_generator.generate(
                        prompt=text,
                        duration=5,
                        output_file=output_file
                    )
                    if result_file:
                        print(f"   [{idx}] ✅ 音效: {text[:20]}...")
                        return (idx, result_file)
                    else:
                        print(f"   [{idx}] ⚠️ 音效失败: {text[:20]}...")
                        return (idx, None)
                except Exception as e:
                    print(f"   [{idx}] ❌ 音效失败: {e}")
                    return (idx, None)
        
        # 并行生成所有片段
        results = [None] * len(items)
        tts_idx = 0
        sfx_idx = 0
        
        # 准备任务参数
        tasks = []
        for idx, item in enumerate(items):
            text = item[0]
            item_type = item[1]
            if item_type == 0:
                tasks.append((idx, text, item_type, tts_idx, 0, custom_voice))
                tts_idx += 1
            else:
                tasks.append((idx, text, item_type, 0, sfx_idx, custom_voice))
                sfx_idx += 1
        
        # 打印任务列表（调试）
        print(f"   📋 任务列表:")
        for task in tasks:
            task_idx, task_text, task_type, task_tts_idx, task_sfx_idx, _ = task
            type_label = "TTS" if task_type == 0 else "SFX"
            idx_label = f"tts_idx={task_tts_idx}" if task_type == 0 else f"sfx_idx={task_sfx_idx}"
            print(f"      [{task_idx}] {type_label} ({idx_label}): {task_text[:20]}...")
        
        # 使用线程池并行执行
        start_time = time.time()
        with ThreadPoolExecutor(max_workers=min(len(items), 10)) as executor:
            futures = {
                executor.submit(generate_single_item, *task): task[0]
                for task in tasks
            }
            
            for future in as_completed(futures):
                idx, audio_path = future.result()
                results[idx] = audio_path
                print(f"      完成 [{idx}]: {audio_path}")
        
        elapsed = time.time() - start_time
        print(f"   ⏱️ 并行生成完成，耗时: {elapsed:.2f}秒")
        
        # 打印结果顺序（调试）
        print(f"   📋 结果顺序:")
        for i, path in enumerate(results):
            print(f"      [{i}]: {path}")
        
        # 处理音效叠加：如果TTS后面有音效，将音效叠加到TTS末尾
        print(f"   🔧 开始处理音效叠加...")
        processed_files = []
        i = 0
        while i < len(items):
            item_type = items[i][1]
            audio_path = results[i]
            item_text = items[i][0][:20] if items[i][0] else "(空)"
            
            print(f"      处理 [{i}] type={item_type} text={item_text}... path={audio_path}")
            
            if item_type == 0 and audio_path:  # TTS
                # 检查后面是否紧跟音效
                if i + 1 < len(items) and items[i + 1][1] == 1:
                    sfx_path = results[i + 1]
                    if sfx_path:
                        # 将音效叠加到TTS末尾
                        if session_dir:
                            overlay_dir = os.path.join(session_dir, 'overlay')
                            os.makedirs(overlay_dir, exist_ok=True)
                            overlay_file = os.path.join(overlay_dir, f'overlay_{paragraph_index}_{len(processed_files)}.wav')
                        else:
                            overlay_file = os.path.join(TARGET_DIR, 'history_res', f'overlay_{paragraph_index}_{len(processed_files)}.wav')
                        
                        print(f"         🔊 叠加: TTS[{i}] + SFX[{i+1}] -> {overlay_file}")
                        if overlay_sfx_on_tts(audio_path, sfx_path, overlay_file, sfx_volume=0.25):
                            processed_files.append(overlay_file)
                        else:
                            processed_files.append(audio_path)
                        i += 2  # 跳过已处理的音效
                        continue
                
                # TTS后面没有音效，直接添加
                print(f"         ➕ 直接添加TTS: {audio_path}")
                processed_files.append(audio_path)
            elif item_type == 1 and audio_path:  # 独立的音效（前面没有TTS）
                # 单独的音效，降低音量后直接添加
                print(f"         ➕ 独立音效: {audio_path}")
                processed_files.append(audio_path)
            else:
                print(f"         ⏭️ 跳过（audio_path为空或已处理）")
            
            i += 1
        
        print(f"   📦 处理后共 {len(processed_files)} 个音频文件:")
        for idx, pf in enumerate(processed_files):
            print(f"      [{idx}]: {pf}")
        
        # 拼接所有处理后的音频
        audio_files = processed_files
        
        # 拼接所有音频
        if audio_files:
            if session_dir:
                combined_dir = os.path.join(session_dir, 'combined')
                os.makedirs(combined_dir, exist_ok=True)
                combined_file = os.path.join(combined_dir, f'paragraph_{paragraph_index}.wav')
            else:
                combined_file = os.path.join(TARGET_DIR, 'history_res', f'paragraph_{paragraph_index}.wav')
            
            if concatenate_audio_files(audio_files, combined_file):
                print(f"   ✅ 段落音频拼接完成: {combined_file}")
                # 返回相对路径，而不是绝对路径
                relative_path = combined_file.replace(f'{TARGET_DIR}/', '')
                return jsonify({
                    'success': True,
                    'audio_path': relative_path,
                    'audio_files': [f.replace(f'{TARGET_DIR}/', '') for f in audio_files],
                    'message': f'段落 {paragraph_index} 音频生成成功'
                })
            else:
                # 如果拼接失败，返回第一个音频文件
                first_audio_path = audio_files[0].replace(f'{TARGET_DIR}/', '') if audio_files else None
                return jsonify({
                    'success': True,
                    'audio_path': first_audio_path,
                    'audio_files': [f.replace(f'{TARGET_DIR}/', '') for f in audio_files],
                    'message': '音频拼接失败，返回单个文件'
                })
        else:
            return jsonify({
                'success': False,
                'error': '没有生成任何音频'
            }), 500
            
    except Exception as e:
        print(f"❌ 段落音频生成错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/generate-sfx', methods=['POST'])
def generate_sfx_api():
    """生成音效接口"""
    try:
        data = request.json
        prompt = data.get('prompt', '')
        duration = data.get('duration', 5)
        session_dir = data.get('session_dir', None)
        paragraph_index = data.get('paragraph_index', 0)
        sfx_index = data.get('sfx_index', 0)
        
        if not prompt:
            return jsonify({'error': '请提供音效描述'}), 400
        
        print(f"🔊 [音效生成] 开始生成音效")
        print(f"   描述: {prompt}")
        print(f"   时长: {duration}秒")
        
        # 确保session_dir是绝对路径
        if session_dir and not os.path.isabs(session_dir):
            session_dir = os.path.join(TARGET_DIR, session_dir)
        
        # 确定输出文件路径
        if session_dir:
            sfx_dir = os.path.join(session_dir, 'sfx')
            os.makedirs(sfx_dir, exist_ok=True)
            output_file = os.path.join(sfx_dir, f'sfx_{paragraph_index}_{sfx_index}.wav')
        else:
            output_file = os.path.join(TARGET_DIR, 'history_res', f'sfx_{paragraph_index}_{sfx_index}.wav')
            os.makedirs(os.path.join(TARGET_DIR, 'history_res'), exist_ok=True)
        
        # 生成音效
        result_file = sfx_generator.generate(
            prompt=prompt,
            duration=duration,
            output_file=output_file
        )
        
        if result_file:
            print(f"✅ 音效生成成功: {result_file}")
            # 返回相对路径，而不是绝对路径
            relative_path = result_file.replace(f'{TARGET_DIR}/', '')
            return jsonify({
                'success': True,
                'audio_path': relative_path,
                'message': '音效生成成功'
            })
        else:
            return jsonify({
                'success': False,
                'error': '音效生成失败'
            }), 500
            
    except Exception as e:
        print(f"❌ 音效生成错误: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500



@app.route('/api/generate-baike', methods=['POST'])
def generate_baike():
    """生成百科问答接口"""
    try:
        data = request.json
        story_title = data.get('story_title', '')
        main_role = data.get('main_role', '')
        scene = data.get('scene', '')
        chapter = data.get('chapter', '')
        paragraph_index = data.get('paragraph_index', 0)
        session_dir = data.get('session_dir', None)
        
        # 确保session_dir是绝对路径
        if session_dir and not os.path.isabs(session_dir):
            session_dir = os.path.join(TARGET_DIR, session_dir)
        
        if not chapter:
            return jsonify({'error': '请提供故事段落内容'}), 400
        
        print(f"\n📚 [百科问答生成] 段落 {paragraph_index}")
        print(f"   内容: {chapter[:50]}...")
        
        params = {
            "story_title": story_title or "儿童故事",
            "main_role": main_role or "小动物",
            "scene": scene or "森林"
        }
        
        # 调用百科问答生成
        result = baike_generator.call(params, chapter)
        
        # 解析JSON结果
        try:
            qa_list = json.loads(result)
        except json.JSONDecodeError:
            print(f"   ⚠️ JSON解析失败，尝试修复")
            # 尝试提取JSON数组
            import re
            match = re.search(r'\[.*\]', result, re.DOTALL)
            if match:
                qa_list = json.loads(match.group())
            else:
                qa_list = []
        
        print(f"   ✅ 生成 {len(qa_list)} 个百科问答")
        for i, qa in enumerate(qa_list):
            print(f"      Q{i+1}: {qa.get('question', '')[:30]}...")
        
        # 保存到文件
        if session_dir:
            baike_dir = os.path.join(session_dir, 'baike')
            os.makedirs(baike_dir, exist_ok=True)
            baike_file = os.path.join(baike_dir, f'baike_{paragraph_index}.json')
            with open(baike_file, 'w', encoding='utf-8') as f:
                json.dump(qa_list, f, ensure_ascii=False, indent=2)
            print(f"   📝 已保存到: {baike_file}")
        
        return jsonify({
            'success': True,
            'qa_list': qa_list,
            'paragraph_index': paragraph_index,
            'message': f'百科问答生成成功，共{len(qa_list)}个问题'
        })
        
    except Exception as e:
        print(f"❌ 百科问答生成错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/nlu', methods=['POST'])
def nlu_answer():
    """NLU问答API - 接收问题，返回回答"""
    try:
        data = request.json
        question = data.get('question', '')
        
        if not question:
            return jsonify({'success': False, 'error': '问题不能为空'}), 400
        
        # 调用问答助手
        answer = qa_assistant.call(question)
        
        return jsonify({
            'success': True,
            'answer': answer
        })
    except Exception as e:
        print(f"NLU API error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/generate-exam', methods=['POST'])
def generate_exam():
    """生成测试试卷接口"""
    try:
        data = request.json
        story_text = data.get('story', '')
        baike_qas = data.get('baike_qas', [])
        user_querys = data.get('user_querys', [])
        session_dir = data.get('session_dir', None)
        
        if not baike_qas:
            return jsonify({'error': '请提供百科问答数据'}), 400
        
        # print(f"\n📝 [试卷生成] 开始生成测试题")
        # print(f"   故事长度: {len(story_text)} 字符")
        # print(f"   百科问答数: {len(baike_qas)} 个")
        # print(f"   用户提问数: {len(user_querys)} 个")
        
        params = {
            "story": story_text,
            "baike_qas": baike_qas,
            "user_querys": user_querys
        }
        
        # 调用试卷生成
        result = exam_generator.call(params)
        
        # 解析JSON结果
        try:
            exam_data = json.loads(result)
        except json.JSONDecodeError:
            print(f"   ⚠️ JSON解析失败，尝试修复")
            import re
            match = re.search(r'\[.*\]', result, re.DOTALL)
            if match:
                exam_data = json.loads(match.group())
            else:
                exam_data = []
        
        print(f"   📋 原始生成 {len(exam_data)} 道题目")
        
        # 过滤并验证题目格式
        valid_exam_data = []
        for i, q in enumerate(exam_data):
            # 检查题目格式是否正确（必须是字典且包含必要字段）
            if isinstance(q, dict) and '问题' in q:
                question_text = q.get('问题', '')[:30] if q.get('问题') else '(无问题)'
                print(f"      Q{i+1}: {question_text}...")
                valid_exam_data.append(q)
            else:
                print(f"      Q{i+1}: ⚠️ 格式错误，已跳过: {str(q)[:50]}...")
        
        exam_data = valid_exam_data
        print(f"   ✅ 有效题目: {len(exam_data)} 道")
        
        # 保存到文件
        if session_dir:
            exam_dir = os.path.join(TARGET_DIR, session_dir, 'exam')
            os.makedirs(exam_dir, exist_ok=True)
            exam_file = os.path.join(exam_dir, 'exam.json')
            with open(exam_file, 'w', encoding='utf-8') as f:
                json.dump(exam_data, f, ensure_ascii=False, indent=2)
            print(f"   📝 已保存到: {exam_file}")
        
        return jsonify({
            'success': True,
            'exam': exam_data,
            'message': f'试卷生成成功，共{len(exam_data)}道题'
        })
        
    except Exception as e:
        print(f"❌ 试卷生成错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/save-exam-result', methods=['POST'])
def save_exam_result():
    """保存用户答案"""
    try:
        data = request.json
        session_dir = data.get('session_dir', '')
        user_answers = data.get('user_answers', {})
        exam_completed = data.get('exam_completed', False)
        
        if not session_dir:
            return jsonify({'success': False, 'error': '缺少session_dir'}), 400
        
        exam_dir = os.path.join(session_dir, 'exam')
        os.makedirs(exam_dir, exist_ok=True)
        
        exam_result_file = os.path.join(TARGET_DIR, exam_dir, 'exam_result.json')
        exam_result = {
            'user_answers': user_answers,
            'exam_completed': exam_completed,
            'timestamp': time.time()
        }
        
        with open(exam_result_file, 'w', encoding='utf-8') as f:
            json.dump(exam_result, f, ensure_ascii=False, indent=2)
        
        print(f"   ✅ 保存用户答案到: {exam_result_file}")
        return jsonify({'success': True, 'message': '答案已保存'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def generate_video_task(prompt, image_path, session_dir, paragraph_index):
    """后台任务：生成视频"""
    try:
        print(f"\n🎬 [视频生成] 开始生成段落 {paragraph_index} 的视频")
        print(f"   提示词: {prompt[:50]}...")
        print(f"   图片: {image_path}")
        print(f"   接收到的session_dir: {session_dir}")
        
        # 确保session_dir是绝对路径
        if not os.path.isabs(session_dir):
            session_dir = os.path.join(TARGET_DIR, session_dir)
            print(f"   转换为绝对路径: {session_dir}")
        
        # 上传图片到OSS获取URL
        image_url = upload_image_to_oss(image_path)
        if not image_url:
            print(f"   ⚠️ 图片上传失败，跳过视频生成")
            return None
        
        # 创建视频目录
        video_dir = os.path.join(session_dir, 'videos')
        os.makedirs(video_dir, exist_ok=True)
        
        # 生成视频文件路径
        video_path = os.path.join(video_dir, f'video_{paragraph_index}.mp4')
        
        # 生成视频（默认10秒，720p，16:9）
        result = video_generator.generate(
            prompt=prompt,
            image_url=image_url,
            output_path=video_path,
            duration=10,
            resolution='720p',
            aspect_ratio='16:9'
        )
        
        if result:
            print(f"   ✅ 视频生成成功: {video_path}")
            return video_path
        else:
            print(f"   ❌ 视频生成失败")
            return None
    except Exception as e:
        print(f"   ❌ 视频生成异常: {e}")
        import traceback
        traceback.print_exc()
        return None


@app.route('/api/file-exists', methods=['POST'])
def check_file_exists():
    """检查文件是否存在"""
    try:
        data = request.json
        file_path = data.get('file_path', '')
        
        if not file_path:
            return jsonify({'exists': False, 'error': '缺少file_path参数'}), 400
        
        # 将相对路径转换为绝对路径
        absolute_path = os.path.join(TARGET_DIR, file_path)
        exists = os.path.exists(absolute_path)
        return jsonify({'exists': exists, 'file_path': file_path, 'absolute_path': absolute_path})
    except Exception as e:
        return jsonify({'exists': False, 'error': str(e)}), 500


@app.route('/api/generate-video', methods=['POST'])
def generate_video_api():
    """生成视频接口（异步）"""
    try:
        data = request.json
        prompt = data.get('prompt', '')
        image_path = data.get('image_path', '')
        session_dir = data.get('session_dir', '')
        paragraph_index = data.get('paragraph_index', 0)
        
        # 添加调试输出
        print(f"📝 视频生成API接收到的session_dir: {session_dir}")
        print(f"📝 视频生成API接收到的image_path: {image_path}")
        
        if not prompt or not image_path or not session_dir:
            return jsonify({'success': False, 'error': '缺少必要参数'}), 400
        
        if not os.path.exists(image_path):
            return jsonify({'success': False, 'error': '图片文件不存在'}), 400
        
        # 在后台线程中生成视频（使用全局线程池）
        print(f"🎬 [视频生成] 开始生成段落 {paragraph_index} 的视频, 图片: {image_path}, 提示词: {prompt[:50]}...")
        future = video_executor.submit(generate_video_task, prompt, image_path, session_dir, paragraph_index)
        
        # 立即返回，不等待完成
        return jsonify({
            'success': True,
            'message': '视频生成任务已提交',
            'paragraph_index': paragraph_index
        })
        
    except Exception as e:
        print(f"❌ 视频生成接口错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ========== 图片分析 API（识别图片中的主体和位置）==========
@app.route('/api/analyze-image', methods=['POST'])
def analyze_image():
    """分析图片中的主体和位置"""
    try:
        data = request.json
        image_path = data.get('image_path', '')
        
        if not image_path:
            return jsonify({'success': False, 'error': '缺少 image_path 参数'}), 400
        
        # 检查文件是否存在
        if not os.path.exists(image_path):
            return jsonify({'success': False, 'error': f'图片文件不存在: {image_path}'}), 404
        
        print(f"🔍 开始分析图片: {image_path}")
        
        
        
        # cfg = {
        #     "url": "http://127.0.0.1:9988/v1/chat/completions",
        #     "model": "vlm_query2func_model"
        # }
        
        print(f"   🔍 使用 gemini_analyze.py 分析图片")
        print(f"   📄 图片路径: {image_path}")
        
        
        result = analyzer.call({
            "image_path": image_path,
            "max_objects": 5,
            "debug": False
        })
        
        print(f"✅ 图片分析完成: {len(result) if isinstance(result, dict) else 0} 个主体")
        print(f"   分析结果: {json.dumps(result, ensure_ascii=False)}")
        
        return jsonify({
            'success': True,
            'objects': result
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ========== 视频帧分析 API（截取视频帧并识别主体）==========
@app.route('/api/analyze-video-frame', methods=['POST'])
def analyze_video_frame():
    """截取视频指定时间点的帧并分析其中的主体"""
    try:
        data = request.json
        video_path = data.get('video_path', '')
        time_seconds = data.get('time_seconds', 0)
        
        if not video_path:
            return jsonify({'success': False, 'error': '缺少 video_path 参数'}), 400
        
        print(f"📥 收到视频帧分析请求:")
        print(f"   视频路径: {video_path}")
        print(f"   时间点: {time_seconds}秒")
        print(f"   当前工作目录: {os.getcwd()}")
        
        # 检查视频文件是否存在
        if not os.path.exists(video_path):
            print(f"   ❌ 文件不存在!")
            # 尝试使用绝对路径
            abs_path = os.path.join(os.getcwd(), video_path)
            print(f"   尝试绝对路径: {abs_path}")
            if os.path.exists(abs_path):
                video_path = abs_path
                print(f"   ✅ 使用绝对路径成功")
            else:
                return jsonify({'success': False, 'error': f'视频文件不存在: {video_path}'}), 404
        
        print(f"🎬 开始截取视频帧: {video_path}, 时间点: {time_seconds}秒")
        
        
        
        # 创建临时目录存放截取的帧
        temp_dir = os.path.join(os.path.dirname(video_path), 'temp_frames')
        os.makedirs(temp_dir, exist_ok=True)
        
        # 生成唯一的输出文件名
        timestamp = int(time.time() * 1000)
        output_path = os.path.join(temp_dir, f'frame_{timestamp}.png')
        
        # 截取帧
        with ImageCapture(video_path) as capturer:
            success = capturer.capture_frame_at_time(time_seconds, output_path)
        
        if not success:
            return jsonify({'success': False, 'error': '截取视频帧失败'}), 500
        
        print(f"✅ 视频帧截取成功: {output_path}")
        
        # 直接使用 gemini_analyze.py 分析图片（使用本地服务）
        print(f"🔍 开始调用 Gemini API 分析图片...", flush=True)
        import time as time_module
        start_time = time_module.time()
        
        print(f"   📄 图片路径: {output_path}", flush=True)
        print(f"   📐 图片存在: {os.path.exists(output_path)}", flush=True)
        
        try:
            
            print(f"   🔍 使用 gemini_analyze.py 分析图片", flush=True)
            
            
            result = analyzer.call({
                "image_path": output_path,
                "max_objects": 5,
                "debug": False
            })
            
            print(f"   ✅ API 调用成功", flush=True)
            
        except Exception as api_error:
            print(f"   ❌ API 调用异常: {api_error}", flush=True)
            import traceback
            traceback.print_exc()
            raise
        
        elapsed = time_module.time() - start_time
        print(f"   ⏱️ Gemini API 响应时间: {elapsed:.2f}秒", flush=True)
        
        # 获取截取帧的实际尺寸
        
        with PILImage.open(output_path) as img:
            frame_width, frame_height = img.size
        
        print(f"✅ 视频帧分析完成: {len(result) if isinstance(result, dict) else 0} 个主体")
        print(f"   帧尺寸: {frame_width}x{frame_height}")
        print(f"   分析结果: {json.dumps(result, ensure_ascii=False)}")
        
        # 返回结果，包含截取的帧路径和正确的尺寸
        return jsonify({
            'success': True,
            'frame_path': output_path,
            'frame_size': {'width': frame_width, 'height': frame_height},
            'objects': result
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    print("🎙️ 故事+TTS 服务启动中...")
    print("📍 服务地址: http://localhost:5000")
    print("📍 故事生成: http://localhost:5000/api/generate-story")
    print("📍 TTS接口: http://localhost:5000/api/tts")
    print("📍 图片分析: http://localhost:5000/api/analyze-image")
    print("📍 视频帧分析: http://localhost:5000/api/analyze-video-frame")
    print("📍 注册接口: http://localhost:5000/api/enroll")
    # 使用 threaded=True 支持并发请求，这样图片生成可以并行处理
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

