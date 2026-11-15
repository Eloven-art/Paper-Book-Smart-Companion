"""Local launcher. Provider key is entered without echo and never written to disk."""
import getpass
import os
import shutil
import threading
import webbrowser
import uvicorn

if __name__ == '__main__':
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        print('请先安装 FFmpeg（包含 ffprobe）并加入 PATH，然后重新启动。')
        raise SystemExit(1)
    if not os.getenv('ELEVENLABS_API_KEY'):
        value = getpass.getpass('ElevenLabs API Key（隐藏输入；留空只检查界面）: ').strip()
        if value: os.environ['ELEVENLABS_API_KEY'] = value
    print('打开 http://localhost:8000；按 Ctrl+C 停止。密钥仅保存在当前进程。')
    threading.Timer(1.5, lambda: webbrowser.open('http://localhost:8000')).start()
    uvicorn.run('backend.server:create_app', factory=True, host='127.0.0.1', port=8000)
