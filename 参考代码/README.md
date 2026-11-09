# AI 故事生成 + 语音合成系统 V2

这是一个整合了故事生成和语音合成的完整系统，用户只需输入故事参数，系统会自动生成故事并转换为语音，还支持配图、音效、背景音乐、视频、问答、百科、试卷等扩展功能。

## ✨ 功能

- ✨ **自动故事生成**：基于多智能体流水线自动生成儿童故事（支持中英文分章写作）
- 🎙️ **一键转语音**：生成的故事自动转换为语音，支持多种音色及声音克隆注册
- 🖼️ **多模态扩展**：配图生成、音效生成、背景音乐、图生视频、图片内容分析
- 🧠 **互动学习**：故事问答（NLU）、百科生成、自动出题考试
- 📖 **书架管理**：生成的故事可保存、加载到书架

## 📁 目录结构

```
├── tts_v2.html            # 主前端界面（故事生成+语音合成）
├── voice_register.html    # 声音注册界面（自定义音色）
├── story_tts_server.py    # Flask 后端主服务（端口 5000）
├── config.py              # 配置入口（环境变量 / 默认值）
├── ai_story_generate.py   # 多智能体故事写作模块
├── text.py                # 简化版故事生成器
├── prompt_zh.py           # 中文 prompt 模板
├── baike_kw_generate.py   # 百科内容生成
├── exam_paper_generate.py # 试卷生成
├── nlu.py                 # 问答助手
├── text_agent_base.py     # LLM Agent 基类
├── generator_tts.py       # TTS 客户端（SSE 流式）
├── generate_audio.py      # 音效生成
├── generate_music.py      # 背景音乐生成
├── generate_image.py      # 图像生成
├── generate_video.py      # 图生视频 + OSS 上传
├── gemini_analyze.py      # 图像内容分析
├── ImageCapture.py        # 视频截帧
├── bookshelf.json         # 书架数据（示例）
├── history_res/           # 前端静态资源
└── requirements.txt       # Python 依赖
```

## 🚀 快速开始

### 1. 安装依赖

要求 Python 3.8+：

```bash
pip install -r requirements.txt
```

### 2. 配置服务端点

所有外部服务地址与密钥通过**环境变量**配置（见 `config.py`），未设置时为占位符 `xxxx`，需填入后才能正常调用：

```bash
export SIMPLE_STORY_WRITER_URL="http://<你的故事生成API，OpenAI兼容接口>"
export SIMPLE_STORY_WRITER_API_KEY="<密钥>"
export TTS_API_URL="http://<你的TTS推理服务>"
export VOICE_CLONE_TTS_API_URL="http://<你的语音克隆TTS流式服务>"
export NLU_URL="http://<你的NLU问答模型API>"
export NLU_API_KEY="<密钥>"
export IMAGE_ANALYSIS_URL="http://<你的图像理解模型API>"
export IMAGE_API_KEY="<密钥>"
export TEXT2IMAGE_URL="http://<你的图像生成模型API>"
export TEXT2IMAGE_API_KEY="<密钥>"
export IMAGE2VIDEO_URL="http://<你的图生视频API>"
export IMAGE2VIDEO_API_KEY="<密钥>"
export TEXT2AUDIO_URL="http://<你的音效生成API>"
export TEXT2AUDIO_API_KEY="<密钥>"
export TEXT2MUSIC_URL="http://<你的音乐生成API>"
export TEXT2MUSIC_API_KEY="<密钥>"
export ASR_WS_SERVER_URL="ws://<你的ASR WebSocket服务>"
export ENROLL_SERVER_URL="http://<你的声音注册服务>"
# 可选：OSS 上传（用于视频生成模块）
export OSS_ACCESS_KEY_ID="<id>" OSS_ACCESS_KEY_SECRET="<secret>" OSS_BUCKET="<bucket>" OSS_ENDPOINT="<endpoint>"
# 可选：输出数据根目录（默认为项目根目录，书架为 bookshelf.json，生成结果写入 output_dir/ 等子目录）
export TARGET_DIR="/path/to/data"
```

也可以直接修改 `config.py` 中的默认值。

### 3. 启动服务

```bash
python story_tts_server.py
```

服务将在 `http://localhost:5000` 启动。

### 4. 打开前端界面

浏览器访问 `http://localhost:5000/tts_v2.html`（服务会直接托管前端页面），或单独打开 `tts_v2.html` 文件。

## 📝 使用流程

### 步骤1：输入故事参数

填写三个必填字段：
- **故事主题** (story_topic)：例如"勇敢的小兔子"
- **主要角色** (main_role)：例如"小兔子"
- **场景设定** (scene)：例如"森林"

💡 提示：可以点击快速示例按钮快速填充。

### 步骤2：选择朗读音色

- 👧 **小美** - 甜美女声
- 👦 **小刚** - 清晰男声
- 👩 **小云** - 知性女声
- 👨 **小明** - 磁性男声
- 👧🏻 **小溪** - 活泼女声
- 👴 **老张** - 沧桑男声

也支持通过 `voice_register.html` 注册自定义克隆音色。

### 步骤3：生成故事+语音

点击"生成故事+语音"按钮，系统会：
1. 调用 AI 生成故事（约5-10秒）
2. 将故事转换为语音（约3-5秒）
3. 在页面显示故事内容
4. 自动播放生成的语音

## 🔧 技术架构

```
用户输入参数 → 故事生成API → 获取故事内容 → TTS API → 生成音频 → 播放
                     ↓（可选）
        配图 / 音效 / 音乐 / 视频 / 问答 / 百科 / 试卷
```

## 📡 主要 API 接口

### 1. POST /api/generate-story-stream（推荐，流式）

```json
{
  "story_topic": "勇敢的小兔子",
  "main_role": "小兔子",
  "scene": "森林"
}
```

以 SSE 流式返回逐段生成的故事内容。

### 2. POST /api/generate-story（仅生成故事）

请求同上，响应：

```json
{
  "success": true,
  "content": "完整的故事内容...",
  "pages": ["第一页", "第二页"],
  "message": "故事生成成功"
}
```

### 3. POST /api/tts（仅语音合成）

```json
{
  "text": "要合成的文本",
  "voice": "xiaomei"
}
```

### 其他接口

- `/api/generate-image` / `/api/generate-music` / `/api/generate-audio` / `/api/generate-video` — 多模态生成
- `/api/generate-paragraph-audio` — 按段落生成语音（含 SFX 音效混音）
- `/api/nlu` / `/api/generate-baike` / `/api/generate-exam` — 问答 / 百科 / 试卷
- `/api/asr` / `/api/enroll` — 语音识别 / 声音注册
- `/api/analyze-image` / `/api/analyze-video-frame` — 图像/视频帧分析
- `/api/bookshelf*` — 书架增删查载
- `/health` — 健康检查

## 🎭 音色映射

前端音色ID → 后端TTS音色：

```python
'xiaomei'   → 'tianmeixiaoyuan'  # 小美
'xiaogang'  → 'niudun'            # 小刚
'xiaoyun'   → 'oula'              # 小云
'xiaoming'  → 'M001'              # 小明
'xiaoxi'    → 'F001'              # 小溪
'laozhang'  → 'xiaosi'            # 老张
```

## ⚠️ 注意事项

1. **外部服务依赖**：故事生成、TTS、图像/视频/音效/音乐等均依赖外部 API，需先完成环境变量配置并确保网络可达。
2. **生成时间**：故事生成约5-10秒，语音合成约3-5秒，总计约8-15秒；视频生成耗时更长。
3. **文本长度**：生成的故事通常为200-500字；TTS 建议单次不超过1000字。
4. **生产部署**：默认以 `debug=False` 启动；请勿在生产环境开启 debug 模式，并注意 `0.0.0.0` 监听范围。

## 🐛 故障排查

### 问题1：点击生成后没有反应
1. 检查后端服务是否已启动（端口5000）
2. 打开浏览器控制台查看错误信息
3. 确认填写了所有必填参数

### 问题2：故事生成失败
- API Key 未配置或无效（检查环境变量）
- 网络无法访问对应的 API 服务
- 查看后端日志获取详细错误信息

### 问题3：语音合成失败
- TTS API 地址未配置或不可达
- 故事文本过长（尝试缩短）
- 音色参数错误

## 📊 示例用法

| 示例 | 故事主题 | 主要角色 | 场景设定 | 音色 |
|------|---------|---------|---------|------|
| 小猫学分享 | 小猫咪学会分享 | 小猫咪 | 幼儿园 | 小美 |
| 恐龙冒险 | 勇敢的小恐龙寻找妈妈 | 小恐龙 | 恐龙世界 | 小刚 |
| 魔法森林 | 小女孩的魔法森林奇遇 | 小女孩 | 魔法森林 | 小溪 |

## 📞 支持

如有问题，请检查：
1. Python 版本是否为 3.8+
2. 所有依赖是否正确安装（`pip install -r requirements.txt`）
3. 后端服务是否正常运行（端口5000，访问 `/health` 验证）
4. 环境变量是否配置完整
5. 浏览器控制台是否有错误信息
