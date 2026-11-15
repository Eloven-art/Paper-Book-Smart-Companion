"""Local single-user voice-clone backend. No production mock/fallback speech."""
from __future__ import annotations
import asyncio
import hmac
import io
import zipfile
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
MAX_UPLOAD = 20 * 1024 * 1024

class ProviderError(Exception):
    def __init__(self, code, message, status=502):
        self.code, self.message, self.status = code, message, status

class ElevenLabsProvider:
    """Verified against official /voices/add and /text-to-speech/convert docs."""
    def __init__(self, key: str):
        self.key = key
        self.model = os.getenv('ELEVENLABS_MODEL_ID', 'eleven_multilingual_v2')

    async def _call(self, method, path, **kwargs):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15)) as client:
                result = await client.request(method, 'https://api.elevenlabs.io/v1' + path,
                                              headers={'xi-api-key': self.key}, **kwargs)
        except httpx.TimeoutException:
            raise ProviderError('PROVIDER_TIMEOUT', '服务响应超时。请先检查服务端音色列表，注册请求不要盲目重复提交。', 504)
        except httpx.RequestError:
            raise ProviderError('PROVIDER_UNREACHABLE', '无法连接音色服务，请检查服务器网络。')
        if result.is_error:
            if result.status_code in (401, 403):
                raise ProviderError('PROVIDER_AUTH', '服务密钥、账户权限或套餐不支持当前操作，请由服务端检查。')
            if result.status_code == 429:
                raise ProviderError('PROVIDER_LIMIT', '音色服务额度或频率受限，请稍后重试。', 429)
            raise ProviderError('PROVIDER_REJECTED', f'音色服务拒绝请求（HTTP {result.status_code}）；请检查样本和账户状态。')
        return result

    async def enroll(self, name, wav):
        response = await self._call('POST', '/voices/add', data={'name': name, 'description': 'Created by Story Cottage'},
                                    files={'files': ('reference.wav', wav, 'audio/wav')})
        try:
            data = response.json()
            voice_id = data['voice_id']
            if not isinstance(voice_id, str) or not voice_id: raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise ProviderError('PROVIDER_FORMAT', '音色服务返回格式不完整，未标记为注册成功。')
        return voice_id, 'verification_required' if data.get('requires_verification') else 'ready'

    async def synthesize(self, voice_id, text):
        response = await self._call('POST', '/text-to-speech/' + quote(voice_id, safe=''),
                                    params={'output_format': 'mp3_44100_128'},
                                    json={'text': text, 'model_id': self.model})
        if not response.content or not response.headers.get('content-type', '').startswith('audio/'):
            raise ProviderError('PROVIDER_FORMAT', '音色服务没有返回可播放的音频。')
        return response.content

class SpeechInput(BaseModel):
    text: str = Field(min_length=1, max_length=3000)

class SelectionInput(BaseModel):
    voice_id: str = Field(min_length=1, max_length=100)

async def normalize_audio(raw: bytes) -> tuple[bytes, float]:
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise ProviderError('AUDIO_TOOL_MISSING', '服务器需要安装 FFmpeg 与 ffprobe。', 503)
    # Decode real bytes instead of trusting file extension or MIME. Reference files remain temporary.
    with tempfile.TemporaryDirectory(prefix='cottage-voice-') as folder:
        source, output = Path(folder) / 'input', Path(folder) / 'reference.wav'
        source.write_bytes(raw)
        async def run(*args):
            proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try: out, _ = await asyncio.wait_for(proc.communicate(), timeout=25)
            except asyncio.TimeoutError:
                proc.kill(); await proc.communicate()
                raise ProviderError('AUDIO_TIMEOUT', '音频处理超时，请换一段更短的录音。', 422)
            if proc.returncode:
                raise ProviderError('INVALID_AUDIO', '文件无法解码，请上传有效的人声录音。', 422)
            return out
        probe = json.loads(await run('ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_entries',
                                    'format=duration:stream=codec_type', '-of', 'json', str(source)))
        try: duration = float(probe['format']['duration'])
        except (KeyError, TypeError, ValueError):
            raise ProviderError('INVALID_AUDIO', '无法读取音频时长。', 422)
        # Application limits, not a promise of provider cloning quality.
        if not 10 <= duration <= 120:
            raise ProviderError('AUDIO_DURATION', '本版本接受 10—120 秒的参考音频，建议 30—60 秒清晰单人录音。', 422)
        streams = probe.get('streams', [])
        if not streams or any(s.get('codec_type') != 'audio' for s in streams):
            raise ProviderError('INVALID_AUDIO', '请上传纯音频文件，不接受视频或播放列表。', 422)
        await run('ffmpeg', '-v', 'error', '-nostdin', '-protocol_whitelist', 'file,pipe', '-i', str(source),
                  '-vn', '-ac', '1', '-ar', '44100', '-c:a', 'pcm_s16le', '-t', '120', str(output))
        return output.read_bytes(), duration

def create_app(data_dir=None, provider=None):
    data = Path(data_dir or os.getenv('VOICE_DATA_DIR', str(ROOT / 'work' / 'voice-data')))
    data.mkdir(parents=True, exist_ok=True)
    db_path = data / 'voices.sqlite3'
    with sqlite3.connect(db_path) as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS voices(id TEXT PRIMARY KEY,name TEXT NOT NULL,
          provider_id TEXT NOT NULL,status TEXT NOT NULL,duration REAL NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
          CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);''')
    actual_provider = provider or (ElevenLabsProvider(os.environ['ELEVENLABS_API_KEY']) if os.getenv('ELEVENLABS_API_KEY') else None)
    app = FastAPI(title='纸书智伴 · 音色服务', docs_url=None, redoc_url=None)
    app.state.provider = actual_provider
    app.state.synthesis_gate = asyncio.Semaphore(1)

    def connect():
        db = sqlite3.connect(db_path); db.row_factory = sqlite3.Row; return db
    def find(voice_id):
        with connect() as db: row = db.execute('SELECT * FROM voices WHERE id=?', (voice_id,)).fetchone()
        if not row: raise ProviderError('VOICE_NOT_FOUND', '找不到这个声音，请刷新音色列表。', 404)
        return dict(row)
    def ready_provider():
        if app.state.provider is None:
            raise ProviderError('NOT_CONFIGURED', '尚未连接克隆服务。请在后端设置 ELEVENLABS_API_KEY 后重启。', 503)
        return app.state.provider
    @app.middleware('http')
    async def protect(request: Request, call_next):
        if request.url.path == '/api/voice/status' or request.url.path == '/api/voices' or request.url.path.startswith('/api/voices/'):
            token = os.getenv('VOICE_ACCESS_TOKEN', '')
            client = request.client.host if request.client else ''
            if token:
                supplied = request.headers.get('authorization', '')
                if not hmac.compare_digest(supplied, 'Bearer ' + token):
                    return JSONResponse({'error': {'code':'UNAUTHORIZED','message':'后端访问凭证无效。'}}, status_code=401)
            elif client not in ('127.0.0.1', '::1', 'testclient'):
                return JSONResponse({'error': {'code':'LOCAL_ONLY','message':'默认仅允许本机访问；远程部署请先配置访问认证。'}}, status_code=403)
            origin = request.headers.get('origin')
            if origin and origin != str(request.base_url).rstrip('/'):
                return JSONResponse({'error': {'code':'ORIGIN_DENIED','message':'请从同源网站访问音色服务。'}}, status_code=403)
            try: length = int(request.headers.get('content-length', '0'))
            except ValueError: length = 0
            if length > MAX_UPLOAD + 1024 * 1024:
                return JSONResponse({'error': {'code':'FILE_TOO_LARGE','message':'参考音频不能超过 20 MB。'}}, status_code=413)
        response = await call_next(request)
        if request.url.path == '/api/voice/status' or request.url.path == '/api/voices' or request.url.path.startswith('/api/voices/'):
            response.headers['Cache-Control'] = 'no-store'
        return response
    @app.exception_handler(ProviderError)
    async def provider_error(request, exc):
        return JSONResponse({'error': {'code':exc.code, 'message':exc.message}}, status_code=exc.status)
    from fastapi.exceptions import RequestValidationError
    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({'error': {'code':'VALIDATION','message':'请检查必填字段和文字长度。'}}, status_code=422)

    @app.get('/api/voice/status')
    async def status():
        return {'configured':app.state.provider is not None, 'provider':'elevenlabs',
                'audio_tools':bool(shutil.which('ffmpeg') and shutil.which('ffprobe')),
                'limits':{'min_seconds':10,'max_seconds':120,'max_bytes':MAX_UPLOAD,'max_text':3000}}
    @app.get('/api/voices')
    async def voices():
        with connect() as db:
            rows = [dict(r) for r in db.execute('SELECT id,name,status,duration,created_at FROM voices ORDER BY created_at DESC')]
            selected = db.execute("SELECT value FROM settings WHERE key='selected_voice'").fetchone()
        return {'voices':rows, 'selected_voice_id':selected['value'] if selected else None}
    @app.post('/api/voices')
    async def enroll(audio: UploadFile = File(...), name: str = Form(...), consent: str = Form(...)):
        service = ready_provider()
        name = name.strip()
        if not name or len(name) > 40:
            raise ProviderError('NAME_INVALID', '请填写 1—40 个字的声音名称。', 422)
        if consent != 'yes':
            raise ProviderError('CONSENT_REQUIRED', '请确认这是你有权使用的成人声音。', 422)
        raw = await audio.read(MAX_UPLOAD + 1)
        await audio.close()
        if not raw or len(raw) > MAX_UPLOAD:
            raise ProviderError('FILE_TOO_LARGE', '请选择有效的音频，文件不能超过 20 MB。', 413)
        wav, duration = await normalize_audio(raw)
        provider_id, status = await service.enroll(name, wav)
        voice_id = uuid4().hex
        with connect() as db:
            db.execute('INSERT INTO voices(id,name,provider_id,status,duration) VALUES(?,?,?,?,?)',
                       (voice_id,name,provider_id,status,duration))
        return {'voice':{'id':voice_id,'name':name,'status':status,'duration':duration}}
    @app.post('/api/voices/selection')
    async def select(payload: SelectionInput):
        row = find(payload.voice_id)
        if row['status'] != 'ready':
            raise ProviderError('VOICE_NOT_READY', '音色仍需服务商验证，暂时不能选用。', 409)
        with connect() as db:
            db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('selected_voice',?)", (payload.voice_id,))
        return {'selected_voice_id':payload.voice_id,'name':row['name']}
    @app.post('/api/voices/{voice_id}/speech')
    async def speech(voice_id: str, payload: SpeechInput):
        service = ready_provider(); row = find(voice_id)
        if row['status'] != 'ready':
            raise ProviderError('VOICE_NOT_READY', '音色仍需服务商验证，请在服务商控制台完成验证。', 409)
        text = payload.text.strip()
        if not text: raise ProviderError('TEXT_REQUIRED', '请输入想让这个声音说的话。', 422)
        async with app.state.synthesis_gate:
            audio = await service.synthesize(row['provider_id'], text)
        return Response(audio, media_type='audio/mpeg', headers={'Cache-Control':'no-store'})

    # Explicit asset routes avoid exposing backend, database, original code or secrets.
    @app.get('/')
    async def index(): return FileResponse(ROOT / 'index.html')
    for asset_dir in ('assets','api','ui'):
        app.mount('/' + asset_dir + '/', StaticFiles(directory=ROOT / asset_dir), name='static-' + asset_dir)
    @app.get('/绘本比赛前端网站.zip')
    async def source_archive():
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for filename in ('index.html','app.js','style.css','README.md','音色模块说明.md','requirements.txt','启动网站.bat','启动音色服务.bat','.env.example','接口与验收.md'):
                path = ROOT / filename
                if path.is_file(): archive.write(path, '绘本比赛前端网站/' + filename)
            for folder in ('assets','api','ui','backend','tests','originals'):
                for path in (ROOT / folder).rglob('*'):
                    if path.is_file() and '__pycache__' not in path.parts:
                        archive.write(path, '绘本比赛前端网站/' + str(path.relative_to(ROOT)))
        return Response(buffer.getvalue(), media_type='application/zip')
    @app.get('/{filename}')
    async def frontend_file(filename: str):
        if filename not in ('app.js','style.css'): raise HTTPException(404)
        return FileResponse(ROOT / filename)
    return app
