#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频生成调用模块
"""

import requests
import json
import time
import sys
import os
import base64
from requests.adapters import HTTPAdapter
from PIL import Image
from io import BytesIO
import http.server
import socketserver
import threading
from pathlib import Path
from datetime import datetime

# 导入OSS相关库
try:
    import oss2
    from oss2 import Auth, Bucket
    OSS_AVAILABLE = True
except ImportError:
    OSS_AVAILABLE = False
    print("⚠️  警告：oss2库未安装，无法使用OSS上传功能")
    print("   安装方法：pip install oss2")


# OSS配置（通过环境变量配置，未设置时功能不可用）

access_key_id = os.environ.get('OSS_ACCESS_KEY_ID', '')
access_key_secret = os.environ.get('OSS_ACCESS_KEY_SECRET', '')
bucket_name = os.environ.get('OSS_BUCKET', '')
endpoint = os.environ.get('OSS_ENDPOINT', '')

def generate_video_filename(prefix='generated_video', duration=None, resolution=None, 
                           timestamp=True, extension='mp4'):
    """
    生成带时间戳的视频文件名
    
    参数:
        prefix: 文件名前缀（默认: 'generated_video'）
        duration: 视频时长（秒），如果提供则添加到文件名中
        resolution: 视频分辨率，如果提供则添加到文件名中
        timestamp: 是否添加时间戳（默认: True）
        extension: 文件扩展名（默认: 'mp4'）
        
    返回:
        带时间戳的文件名
        
    示例:
        generate_video_filename('my_video', duration=10, resolution='720p')
        # 返回: my_video_20240101_123456_10s_720p.mp4
    """
    parts = [prefix]
    
    # 添加时间戳
    if timestamp:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        parts.append(ts)
    
    # 添加时长
    if duration:
        parts.append(f'{duration}s')
    
    # 添加分辨率
    if resolution:
        parts.append(resolution)
    
    # 组合文件名
    filename = '_'.join(parts) + f'.{extension}'
    return filename


def get_oss_url_from_path(img_path, bucket_name=bucket_name, endpoint=endpoint):
    """
    根据本地路径直接生成OSS URL（不检查是否存在）
    
    参数:
        img_path: 本地图片路径
        bucket_name: OSS存储桶名称
        endpoint: OSS端点
        
    返回:
        OSS URL地址
    """
    img_name = os.path.basename(img_path)
    object_name = f'NLP/zouting1/huck-comp/{img_name}'
    oss_url = f"https://{bucket_name}.{endpoint.replace('http://', '')}/{object_name}"
    return oss_url


def check_oss_file_exists(img_path, access_key_id=access_key_id, access_key_secret=access_key_secret,
                          bucket_name=bucket_name, endpoint=endpoint):
    """
    检查OSS中是否已存在该图片
    
    参数:
        img_path: 本地图片路径
        access_key_id: OSS访问密钥ID
        access_key_secret: OSS访问密钥
        bucket_name: OSS存储桶名称
        endpoint: OSS端点
        
    返回:
        (exists: bool, url: str) - 是否存在和对应的URL
    """
    if not OSS_AVAILABLE:
        return False, None
    
    try:
        img_name = os.path.basename(img_path)
        object_name = f'NLP/zouting1/huck-comp/{img_name}'
        
        auth = Auth(access_key_id, access_key_secret)
        bucket = Bucket(auth, endpoint, bucket_name)
        
        exists = bucket.object_exists(object_name)
        oss_url = f"https://{bucket.bucket_name}.{endpoint.replace('http://', '')}/{object_name}"
        
        return exists, oss_url
    except Exception as e:
        print(f"⚠️  检查OSS文件失败: {e}")
        return False, None


def upload_image_to_oss(img_path, access_key_id=access_key_id, access_key_secret=access_key_secret, 
                        bucket_name=bucket_name, endpoint=endpoint, desktop=True, force_upload=False):
    """
    上传本地图片到阿里云OSS（智能检测，避免重复上传）
    
    参数:
        img_path: 本地图片路径
        access_key_id: OSS访问密钥ID
        access_key_secret: OSS访问密钥
        bucket_name: OSS存储桶名称
        endpoint: OSS端点
        desktop: 是否为桌面环境（保留兼容性）
        force_upload: 是否强制上传（即使文件已存在）
        
    返回:
        图片的OSS URL地址，失败时返回None
    """
    if not OSS_AVAILABLE:
        print("❌ oss2库未安装，请运行: pip install oss2")
        return None
    
    try:
        # 检查文件是否存在
        if not os.path.exists(img_path):
            print(f"❌ 图片文件不存在: {img_path}")
            return None
        
        # 生成OSS对象名称（使用文件名）
        img_name = os.path.basename(img_path)  # 获取文件名
        if not img_name or "." not in img_name:
            img_name = f"image_{hash(img_path)}.jpg"  # 如果没有文件名，生成一个
        
        # 修正拼写错误：object_name
        object_name = f'NLP/zouting1/huck-comp/{img_name}'
        
        # 进行身份验证
        auth = Auth(access_key_id, access_key_secret)
        # 创建 Bucket 对象
        bucket = Bucket(auth, endpoint, bucket_name)
        
        # 生成图片URL
        oss_url = f"https://{bucket.bucket_name}.{endpoint.replace('http://', '')}/{object_name}"
        
        print(f"📤 正在上传到阿里云OSS...")
        print(f"   文件: {img_name}")
        
        # 获取本地文件大小
        local_file_size = os.path.getsize(img_path)
        print(f"   大小: {local_file_size:,} bytes ({local_file_size/1024/1024:.2f} MB)")

        # 读取文件并上传
        with open(img_path, 'rb') as f:
            img_byte_arr = f.read()
        
        res = bucket.put_object(object_name, img_byte_arr)

        print(f"✓ 图片上传成功!")
        print(f"  URL: {oss_url}")
        return oss_url
        
    except Exception as e:
        print(f"❌ 上传图片到OSS失败: {e}")
        print(f"   错误类型: {type(e).__name__}")
        return None


class VideoGenerator:
    """视频生成器类"""
    
    def __init__(self, base_url=None, 
                 api_key='xxxx',
                 model='doubao-seedance-1.0-lite-i2v',
                 priority='5',
                 verbose=True):
        """
        初始化视频生成器
        
        Args:
            base_url: API基础URL
            api_key: API密钥
            model: 模型名称
            priority: 任务优先级
            verbose: 是否显示详细输出
        """
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.priority = priority
        self.verbose = verbose
        self.poll_interval = 5  # 轮询间隔（秒）- 视频生成通常需要更长时间
        self.max_attempts = 120  # 最大轮询次数
    


    
    def start_local_server(self, image_dir, port=8000):
        """
        启动本地HTTP服务器来托管图片
        
        Args:
            image_dir: 图片所在目录
            port: 服务器端口
            
        Returns:
            server_url: 服务器地址
        """
        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=image_dir, **kwargs)
            
            def log_message(self, format, *args):
                pass  # 禁用日志输出
        
        try:
            with socketserver.TCPServer(("", port), Handler) as httpd:
                # 在后台线程中运行服务器
                server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                server_thread.start()
                
                if self.verbose:
                    print(f"✓ 本地服务器已启动: http://localhost:{port}")
                
                return f"http://localhost:{port}"
        except OSError as e:
            if self.verbose:
                print(f"⚠️  端口 {port} 已被占用，尝试其他端口...")
            return self.start_local_server(image_dir, port + 1)
    
    def upload_to_imgbb(self, image_path, api_key=None):
        """
        上传图片到ImgBB图床（免费，需要API key）
        注册地址: https://api.imgbb.com/
        
        特点：
        - 支持最大32MB图片
        - 全球CDN加速
        - 永久保存
        - 每月5000次上传限制（免费版）
        
        Args:
            image_path: 图片路径
            api_key: ImgBB API密钥
            
        Returns:
            image_url: 图片URL
        """
        if not api_key:
            if self.verbose:
                print("⚠️  需要ImgBB API密钥")
                print("   访问 https://api.imgbb.com/ 注册")
            return None
        
        url = "https://api.imgbb.com/1/upload"
        
        try:
            with open(image_path, 'rb') as f:
                image_data = base64.b64encode(f.read()).decode('utf-8')
            
            payload = {
                'key': api_key,
                'image': image_data,
            }
            
            if self.verbose:
                print("📤 正在上传到ImgBB图床...")
                print("   (首次上传可能需要几秒钟)")
            
            response = requests.post(url, data=payload, timeout=60)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    image_url = result['data']['url']
                    display_url = result['data'].get('display_url', image_url)
                    delete_url = result['data'].get('delete_url', 'N/A')
                    
                    if self.verbose:
                        print(f"✓ 上传成功!")
                        print(f"  URL: {image_url}")
                        print(f"  展示URL: {display_url}")
                        print(f"  删除URL: {delete_url}")
                    return image_url
                else:
                    error_msg = result.get('error', {}).get('message', '未知错误')
                    if self.verbose:
                        print(f"❌ ImgBB上传失败: {error_msg}")
                        if 'auth' in error_msg.lower() or 'key' in error_msg.lower():
                            print("   原因：API key无效或已过期")
                        elif 'size' in error_msg.lower():
                            print("   原因：图片过大（限制32MB）")
                    return None
            else:
                if self.verbose:
                    print(f"❌ ImgBB服务器响应错误 (HTTP {response.status_code})")
                    try:
                        error_data = response.json()
                        print(f"   错误: {error_data}")
                    except:
                        print(f"   响应: {response.text[:200]}")
                return None
                
        except requests.exceptions.Timeout:
            if self.verbose:
                print(f"❌ 上传超时，请检查网络连接")
            return None
        except Exception as e:
            if self.verbose:
                print(f"❌ 上传异常: {e}")
                print(f"   类型: {type(e).__name__}")
            return None
    
    def upload_to_smms(self, image_path):
        """
        上传图片到SM.MS图床（免费，无需注册）
        官网：https://sm.ms/
        
        Args:
            image_path: 图片路径
            
        Returns:
            image_url: 图片URL
        """
        url = "https://sm.ms/api/v2/upload"
        
        try:
            with open(image_path, 'rb') as f:
                files = {'smfile': f}
                
                if self.verbose:
                    print("📤 正在上传到SM.MS图床...")
                    print("   (首次上传可能需要几秒钟)")
                
                response = requests.post(url, files=files, timeout=60)
                
                if response.status_code == 200:
                    result = response.json()
                    if result.get('success'):
                        image_url = result['data']['url']
                        if self.verbose:
                            print(f"✓ 上传成功!")
                            print(f"  URL: {image_url}")
                            print(f"  删除URL: {result['data'].get('delete', 'N/A')}")
                        return image_url
                    else:
                        # 处理各种错误情况
                        error_msg = result.get('message', '')
                        error_code = result.get('code', '')
                        
                        if 'Image upload repeated limit' in error_msg or error_code == 'image_repeated':
                            # 图片已存在，尝试从错误信息中提取URL
                            if 'images' in result:
                                image_url = result['images']
                            elif 'data' in result and result['data']:
                                image_url = result['data']
                            else:
                                image_url = None
                                
                            if image_url:
                                if self.verbose:
                                    print(f"ℹ️  图片已存在（重复上传）")
                                    print(f"  URL: {image_url}")
                                return image_url
                            else:
                                if self.verbose:
                                    print(f"⚠️  图片重复但无法获取URL，尝试其他图床")
                                return None
                        else:
                            if self.verbose:
                                print(f"❌ SM.MS上传失败: {error_msg}")
                                if 'flood' in error_msg.lower():
                                    print("   原因：请求过于频繁，请稍后再试")
                                elif 'size' in error_msg.lower():
                                    print("   原因：图片过大（限制5MB）")
                            return None
                else:
                    if self.verbose:
                        print(f"❌ SM.MS服务器响应错误 (HTTP {response.status_code})")
                        print(f"   响应: {response.text[:200]}")
                    return None
                    
        except requests.exceptions.Timeout:
            if self.verbose:
                print(f"❌ 上传超时，请检查网络连接")
            return None
        except Exception as e:
            if self.verbose:
                print(f"❌ 上传异常: {e}")
            return None
    
    def local_image_to_url(self, image_path, method='local', auto_fallback=False, **kwargs):
        """
        将本地图片转换为可访问的URL
        
        Args:
            image_path: 本地图片路径
            method: 转换方法
                - 'local': 启动本地HTTP服务器（仅适用于本地API）
                - 'smms': 上传到SM.MS（免费，无需注册）⭐推荐
                - 'imgbb': 上传到ImgBB（需要api_key，稳定）
                - 'auto': 自动选择最佳方法（先smms，失败则imgbb）
            auto_fallback: 是否在失败时自动尝试备用方案
            **kwargs: 其他参数
                - port: 本地服务器端口（method='local'时）
                - api_key: ImgBB API密钥（method='imgbb'时）
        
        Returns:
            image_url: 图片URL
        """
        if not os.path.exists(image_path):
            if self.verbose:
                print(f"❌ 图片文件不存在: {image_path}")
            return None
        
        # 自动模式：优先SM.MS，失败则尝试ImgBB
        if method == 'auto':
            if self.verbose:
                print("🤖 自动模式：尝试最佳上传方案")
            
            # 先尝试SM.MS
            result = self.upload_to_smms(image_path)
            if result:
                return result
            
            # SM.MS失败，尝试ImgBB
            api_key = kwargs.get('api_key')
            if api_key:
                if self.verbose:
                    print("\n🔄 SM.MS失败，尝试ImgBB...")
                result = self.upload_to_imgbb(image_path, api_key)
                if result:
                    return result
            else:
                if self.verbose:
                    print("\n⚠️  SM.MS失败且未提供ImgBB API key")
            
            return None
        
        # 单一方法模式
        if method == 'local':
            # 使用本地HTTP服务器
            image_dir = os.path.dirname(os.path.abspath(image_path))
            image_name = os.path.basename(image_path)
            port = kwargs.get('port', 8000)
            
            server_url = self.start_local_server(image_dir, port)
            if server_url:
                return f"{server_url}/{image_name}"
            return None
            
        elif method == 'imgbb':
            # 上传到ImgBB
            api_key = kwargs.get('api_key')
            result = self.upload_to_imgbb(image_path, api_key)
            
            # 自动备用：如果失败且开启auto_fallback，尝试SM.MS
            if not result and auto_fallback:
                if self.verbose:
                    print("\n🔄 ImgBB失败，尝试SM.MS备用...")
                result = self.upload_to_smms(image_path)
            
            return result
            
        elif method == 'smms':
            # 上传到SM.MS
            result = self.upload_to_smms(image_path)
            
            # 自动备用：如果失败且开启auto_fallback且有API key，尝试ImgBB
            if not result and auto_fallback:
                api_key = kwargs.get('api_key')
                if api_key:
                    if self.verbose:
                        print("\n🔄 SM.MS失败，尝试ImgBB备用...")
                    result = self.upload_to_imgbb(image_path, api_key)
            
            return result
            
        else:
            if self.verbose:
                print(f"❌ 不支持的方法: {method}")
                print("   可选：'local', 'smms', 'imgbb', 'auto'")
            return None
    
    def compress_image_base64(self, image_base64, max_size_mb=5, quality=85):
        """
        压缩base64编码的图片
        
        Args:
            image_base64: 原始base64编码
            max_size_mb: 最大大小(MB)
            quality: JPEG质量(1-100)
            
        Returns:
            compressed_base64: 压缩后的base64编码
        """
        try:
            # 解码base64
            image_data = base64.b64decode(image_base64)
            original_size_mb = len(image_data) / 1024 / 1024
            
            if self.verbose:
                print(f"原始图片大小: {original_size_mb:.2f} MB")
            
            # 如果小于限制，直接返回
            if len(image_base64) / 1024 / 1024 < max_size_mb:
                return image_base64
            
            # 打开图片
            img = Image.open(BytesIO(image_data))
            
            # 转换为RGB（如果是RGBA）
            if img.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = background
            
            # 逐步降低质量直到满足大小要求
            current_quality = quality
            while current_quality > 20:
                output = BytesIO()
                img.save(output, format='JPEG', quality=current_quality, optimize=True)
                compressed_data = output.getvalue()
                compressed_base64 = base64.b64encode(compressed_data).decode('utf-8')
                
                compressed_size_mb = len(compressed_base64) / 1024 / 1024
                
                if compressed_size_mb < max_size_mb:
                    if self.verbose:
                        print(f"✓ 图片已压缩: {original_size_mb:.2f}MB -> {compressed_size_mb:.2f}MB (质量: {current_quality})")
                    return compressed_base64
                
                current_quality -= 10
            
            # 如果还是太大，尝试缩小尺寸
            if self.verbose:
                print("⚠️  降低质量不够，尝试缩小尺寸...")
            
            scale = 0.8
            while scale > 0.3:
                new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
                resized_img = img.resize(new_size, Image.Resampling.LANCZOS)
                
                output = BytesIO()
                resized_img.save(output, format='JPEG', quality=60, optimize=True)
                compressed_data = output.getvalue()
                compressed_base64 = base64.b64encode(compressed_data).decode('utf-8')
                
                compressed_size_mb = len(compressed_base64) / 1024 / 1024
                
                if compressed_size_mb < max_size_mb:
                    if self.verbose:
                        print(f"✓ 图片已缩放并压缩: {original_size_mb:.2f}MB -> {compressed_size_mb:.2f}MB (缩放: {scale*100:.0f}%)")
                    return compressed_base64
                
                scale -= 0.1
            
            # 实在压不下去，返回最小的版本
            if self.verbose:
                print(f"⚠️  警告：无法压缩到{max_size_mb}MB以下，返回最小版本")
            return compressed_base64
            
        except Exception as e:
            if self.verbose:
                print(f"图片压缩失败: {e}，使用原始图片")
            return image_base64
    
    def upload_image(self, image_base64):
        """
        上传base64图片并获取URL
        
        Args:
            image_base64: 图片的base64编码
            
        Returns:
            image_url: 上传后的图片URL，失败返回None
        """
        # TODO: 这里需要根据你的API文档实现图片上传接口
        # 这只是一个示例，你需要根据实际API修改
        url = f'{self.base_url}/v1/upload/image'
        
        headers = {
            'api-key': self.api_key,
            'Content-Type': 'application/json'
        }
        
        data = {
            'image': image_base64
        }
        
        try:
            response = requests.post(url, headers=headers, json=data, timeout=(30, 60))
            if response.status_code == 200:
                result = response.json()
                return result.get('url') or result.get('image_url')
            else:
                if self.verbose:
                    print(f"图片上传失败: {response.text}")
                return None
        except Exception as e:
            if self.verbose:
                print(f"图片上传异常: {e}")
            return None
    


    def submit_task(self, prompt, image_base64=None, image_url=None, duration=10, resolution="720p", aspect_ratio="16:9", extra_body=None):
        """
        提交视频生成任务
        
        Args:
            prompt: 视频描述提示词
            image_base64: 输入图像的base64编码字符串（可选）
            image_url: 输入图像的URL（可选，优先使用）
            duration: 视频时长（秒）
            resolution: 视频分辨率，如"720p", "1080p"
            aspect_ratio: 视频宽高比，如"16:9", "9:16"
            extra_body: 额外参数字典
            
        Returns:
            task_id: 任务ID，失败返回None
        """
        url = f'{self.base_url}/v1/async/chat'
        
        headers = {
            'api-key': self.api_key,
            'x-apx-task-priority': self.priority,
            'X-APX-Model': self.model,
            'Content-Type': 'application/json'
        }
        

        # 构建正确的消息格式（按照API文档）
        # 优先使用URL，其次使用base64
        if image_url:
            # 方式1: 使用URL（推荐）
            print(f"✓ 使用URL方式提交")
            print(f"  - 图片URL: {image_url}")
            
            data = {
                "model": self.model,
                "prompt": prompt,
                "duration": duration,
                "resolution": resolution,
                "aspect_ratio": aspect_ratio,
                "img_url": image_url,
                "extra_body": extra_body
            }
            
        elif image_base64:
            # 方式2: 使用base64
            base64_len = len(image_base64)
            base64_size_mb = base64_len / 1024 / 1024
            print(f"✓ 使用base64方式提交")
            print(f"  - Base64长度: {base64_len:,} 字符 ({base64_size_mb:.2f} MB)")
            
            # 如果base64过大，自动压缩（建议不超过5MB）
            if base64_size_mb > 5:
                print(f"⚠️  base64数据过大 ({base64_size_mb:.2f} MB)，开始自动压缩...")
                image_base64 = self.compress_image_base64(image_base64, max_size_mb=5)
                new_size_mb = len(image_base64) / 1024 / 1024
                print(f"✓ 压缩完成: {base64_size_mb:.2f}MB -> {new_size_mb:.2f}MB")
            
            # 按照API文档的格式构建请求
            data = {
                "model": self.model,
                "content": [
                    {
                        "type": "text",
                        "text": prompt
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_base64}"
                        }
                    }
                ]
            }
            
        else:
            # 没有图片，使用默认占位图片（可按需替换为自己的图床地址）
            print(f"⚠️  未提供图片，使用默认占位图片")
            data = {
            "model": self.model,
            "prompt": prompt,
                "img_url": "https://example.com/default-image.png"
            }
        
        if self.verbose:
            print("🎬 提交视频生成任务...")
            print(f"提示词: {prompt}")
            print(f"时长: {duration}秒")
            print(f"分辨率: {resolution}")
            print(f"宽高比: {aspect_ratio}")
        
        # 添加重试机制
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            retry_count += 1
            
            try:
                # 针对大数据传输的优化配置
                # 1. 不使用session，直接发送请求（避免连接复用问题）
                # 2. 明确设置headers避免chunked encoding
                
                # 序列化JSON数据并计算大小
                import json as json_module
                json_data = json_module.dumps(data, ensure_ascii=False)
                json_bytes = json_data.encode('utf-8')
                data_size_mb = len(json_bytes) / 1024 / 1024
                
                if self.verbose:
                    print(f"请求体大小: {data_size_mb:.2f} MB")
                
                # 明确设置Content-Length，避免chunked encoding
                headers['Content-Length'] = str(len(json_bytes))
                headers['Content-Type'] = 'application/json; charset=utf-8'
                # 不要设置Connection: close或keep-alive，让requests自动处理
                
                # 根据数据大小调整超时时间
                if data_size_mb > 5:
                    timeout_settings = (60, 180)  # 大文件：60秒连接，180秒读取
                    if self.verbose:
                        print(f"⏱️  大文件传输，超时设置: {timeout_settings}")
                elif image_base64:
                    timeout_settings = (30, 120)  # 中等文件：30秒连接，120秒读取
                else:
                    timeout_settings = (10, 30)   # 小文件：10秒连接，30秒读取
                
                # 直接使用requests.post，不使用session
                # 传递已序列化的JSON数据，避免chunked encoding
                response = requests.post(
                    url, 
                    headers=headers, 
                    data=json_bytes,  # 使用data而不是json参数，传递字节数据
                    timeout=timeout_settings
                )
                
                if response.status_code in [200, 201]:
                    result = response.json()
                    
                    if self.verbose:
                        print("任务提交成功!")
                        print(json.dumps(result, indent=2, ensure_ascii=False))
                    
                    # 尝试从响应中提取task_id
                    task_id = result.get('task_id') or result.get('id') or result.get('request_id')
                    return task_id
                else:
                    if self.verbose:
                        print(f"任务提交失败: {response.text}")
                    return None
                    
            except requests.exceptions.ConnectionError as e:
                if self.verbose:
                    print(f"连接错误 (第{retry_count}次尝试): {e}")
                    print(f"错误类型: {type(e).__name__}")
                # 重试前等待一段时间
                if retry_count < max_retries:
                    time.sleep(2)  # 等待2秒后重试
                    continue
                return None
            except requests.exceptions.ChunkedEncodingError as e:
                # 专门处理ChunkedEncodingError错误
                if self.verbose:
                    print(f"Chunked编码错误 (第{retry_count}次尝试): {e}")
                    print(f"错误类型: {type(e).__name__}")
                    print(f"错误详情: {str(e)}")
                # 立即重试，不使用缓存的连接
                if retry_count < max_retries:
                    time.sleep(1)  # 快速重试
                    continue
                return None
            except requests.exceptions.RequestException as e:
                if self.verbose:
                    print(f"请求失败 (第{retry_count}次尝试): {e}")
                    print(f"错误类型: {type(e).__name__}")
                    print(f"错误详情: {str(e)}")
                # 重试前等待一段时间
                if retry_count < max_retries:
                    time.sleep(2)  # 等待2秒后重试
                    continue
                return None
        
        # 所有重试都失败
        if self.verbose:
            print(f"已尝试{max_retries}次连接，均失败，请检查网络连接或API服务状态")
        return None
    
    def get_result(self, task_id, verbose=None):
        """
        获取任务结果
        
        Args:
            task_id: 任务ID
            verbose: 是否显示详细信息，默认使用类的verbose设置
            
        Returns:
            result: 任务结果字典，失败或未完成返回None
        """
        if verbose is None:
            verbose = self.verbose
            
        url = f'{self.base_url}/v1/async/results/{task_id}'
        
        headers = {
            'api-key': self.api_key,
            'X-APX-Model': self.model,
            'Content-Type': 'application/json; charset=utf-8'
        }
        
        if verbose:
            print(f"查询视频任务结果 (ID: {task_id})")
        
        # 添加重试机制
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            retry_count += 1
            
            try:
                # 设置会话和连接池
                session = requests.Session()
                # 优化连接池配置，防止连接复用问题
                adapter = requests.adapters.HTTPAdapter(
                    pool_connections=10, 
                    pool_maxsize=10, 
                    max_retries=3,
                    pool_block=False
                )
                session.mount('http://', adapter)
                session.mount('https://', adapter)  # 同时支持https
                
                # 禁用长连接，避免ChunkedEncodingError
                session.keep_alive = False
                
                # 添加超时设置
                response = session.get(
                    url, 
                    headers=headers,
                    timeout=(5, 15)  # (连接超时5秒，读取超时15秒)
                )
                
                if response.status_code in [200, 201]:
                    result = response.json()
                    # 只有当status为3（完成）时才返回结果
                    if result.get('status') == 3:
                        if verbose:
                            print("任务已完成")
                        return result
                    elif result.get('status') == 2:
                        if verbose:
                            print(f"任务进行中，进度: {result.get('progress', '未知')}%")
                        return None
                    elif result.get('status') == 4:
                        if verbose:
                            print(f"任务失败: {result.get('error', '未知错误')}")
                        return None
                    else:
                        if verbose:
                            print(f"任务状态: {result.get('status', '未知')}")
                        return None
                else:
                    if verbose:
                        print(f"获取结果失败: {response.text}")
                    return None
                    
            except requests.exceptions.ConnectionError as e:
                if verbose:
                    print(f"连接错误 (第{retry_count}次尝试): {e}")
                    print(f"错误类型: {type(e).__name__}")
                # 重试前等待一段时间
                if retry_count < max_retries:
                    time.sleep(1)  # 等待1秒后重试
                    continue
                return None
            except requests.exceptions.ChunkedEncodingError as e:
                # 专门处理ChunkedEncodingError错误
                if verbose:
                    print(f"Chunked编码错误 (第{retry_count}次尝试): {e}")
                    print(f"错误类型: {type(e).__name__}")
                    print(f"错误详情: {str(e)}")
                # 立即重试
                if retry_count < max_retries:
                    time.sleep(1)  # 快速重试
                    continue
                return None
            except requests.exceptions.RequestException as e:
                if verbose:
                    print(f"请求失败 (第{retry_count}次尝试): {e}")
                    print(f"错误类型: {type(e).__name__}")
                    print(f"错误详情: {str(e)}")
                # 重试前等待一段时间
                if retry_count < max_retries:
                    time.sleep(1)  # 等待1秒后重试
                    continue
                return None
        
        # 所有重试都失败
        if verbose:
            print(f"已尝试{max_retries}次连接，均失败")
        return None
    
    def download_video(self, video_url, output_path, verbose=None):
        """
        下载生成的视频文件
        
        Args:
            video_url: 视频文件的URL
            output_path: 保存视频的路径
            verbose: 是否显示详细信息，默认使用类的verbose设置
            
        Returns:
            bool: 下载成功返回True，失败返回False
        """
        if verbose is None:
            verbose = self.verbose
            
        if not video_url:
            if verbose:
                print("视频URL为空")
            return False
            
        if verbose:
            print(f"下载视频文件: {video_url}")
            print(f"保存路径: {output_path}")
        
        # 确保输出目录存在
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        
        # 添加重试机制
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            retry_count += 1
            
            try:
                # 设置会话和连接池
                session = requests.Session()
                # 优化连接池配置
                adapter = requests.adapters.HTTPAdapter(
                    pool_connections=10, 
                    pool_maxsize=10, 
                    max_retries=3,
                    pool_block=False
                )
                session.mount('http://', adapter)
                session.mount('https://', adapter)  # 同时支持https
                
                # 禁用长连接，避免ChunkedEncodingError
                session.keep_alive = False
                
                # 添加超时设置，设置较长的读取超时用于大文件下载
                with session.get(
                    video_url, 
                    headers={'api-key': self.api_key},
                    stream=True,  # 使用流式下载，适合大文件
                    timeout=(5, 60)  # (连接超时5秒，读取超时60秒)
                ) as response:
                    response.raise_for_status()  # 如果状态码不是200，抛出异常
                    
                    # 获取文件大小
                    total_size = int(response.headers.get('content-length', 0))
                    downloaded_size = 0
                    chunk_size = 8192  # 每次读取的块大小
                    
                    with open(output_path, 'wb') as file:
                        for chunk in response.iter_content(chunk_size=chunk_size):
                            if chunk:  # 过滤掉保持连接的空块
                                file.write(chunk)
                                downloaded_size += len(chunk)
                                
                                # 显示下载进度
                                if verbose and total_size > 0:
                                    percent = (downloaded_size / total_size) * 100
                                    # print(f"下载进度: {percent:.1f}% ({downloaded_size}/{total_size} bytes)", end='\r')
                    
                    if verbose:
                        print()  # 换行
                        print("✅ 视频下载完成")
                        
                        # 显示文件信息
                        if os.path.exists(output_path):
                            file_size = os.path.getsize(output_path)
                            file_size_mb = file_size / 1024 / 1024
                            filename = os.path.basename(output_path)
                            
                            # print(f"📁 文件名: {filename}")
                            # print(f"📦 文件大小: {file_size:,} bytes ({file_size_mb:.2f} MB)")
                            # print(f"📂 保存位置: {os.path.abspath(output_path)}")
                    
                    return True
                    
            except requests.exceptions.ConnectionError as e:
                if verbose:
                    print(f"下载连接错误 (第{retry_count}次尝试): {e}")
                    print(f"错误类型: {type(e).__name__}")
                # 重试前等待一段时间
                if retry_count < max_retries:
                    time.sleep(3)  # 等待3秒后重试
                    continue
                return False
            except requests.exceptions.ChunkedEncodingError as e:
                # 专门处理ChunkedEncodingError错误
                if verbose:
                    print(f"下载Chunked编码错误 (第{retry_count}次尝试): {e}")
                    print(f"错误类型: {type(e).__name__}")
                    print(f"错误详情: {str(e)}")
                # 删除可能损坏的文件
                if os.path.exists(output_path):
                    try:
                        os.remove(output_path)
                    except:
                        pass
                # 立即重试
                if retry_count < max_retries:
                    time.sleep(2)  # 快速重试
                    continue
                return False
            except requests.exceptions.RequestException as e:
                if verbose:
                    print(f"下载请求失败 (第{retry_count}次尝试): {e}")
                    print(f"错误类型: {type(e).__name__}")
                    print(f"错误详情: {str(e)}")
                # 删除可能损坏的文件
                if os.path.exists(output_path):
                    try:
                        os.remove(output_path)
                    except:
                        pass
                # 重试前等待一段时间
                if retry_count < max_retries:
                    time.sleep(3)  # 等待3秒后重试
                    continue
                return False
            except IOError as e:
                if verbose:
                    print(f"文件写入错误: {e}")
                    print(f"错误类型: {type(e).__name__}")
                return False
            except Exception as e:
                if verbose:
                    print(f"下载过程中发生未知错误: {e}")
                    print(f"错误类型: {type(e).__name__}")
                return False
        
        if verbose:
            print(f"已尝试{max_retries}次下载，均失败")
        return False
    
    def poll_result(self, task_id):
        """
        轮询任务结果
        
        Args:
            task_id: 任务ID
            
        Returns:
            result: 任务完成的结果字典，超时或失败返回None
        """
        if self.verbose:
            print(f"开始轮询视频生成结果 (ID: {task_id})")
        
        # 最大等待时间为300秒
        max_wait_time = 300
        wait_interval = 5  # 初始等待间隔5秒
        elapsed_time = 0
        
        while elapsed_time < max_wait_time:
            # 使用get_result方法查询状态
            result = self.get_result(task_id)
            
            # 如果结果已完成，返回结果
            if result is not None:
                if self.verbose:
                    print("视频生成完成！")
                return result
            
            # 增加等待时间
            elapsed_time += wait_interval
            
            # 动态调整等待间隔，避免频繁请求
            if elapsed_time > 60:  # 1分钟后
                wait_interval = 10
            if elapsed_time > 120:  # 2分钟后
                wait_interval = 15
            
            # 显示等待信息
            if self.verbose:
                print(f"等待中，已过去 {elapsed_time}秒，预计还需等待 {max_wait_time - elapsed_time}秒...")
            
            # 等待下一轮查询
            time.sleep(wait_interval)
        
        # 超时
        if self.verbose:
            print(f"视频生成超时! 已等待 {max_wait_time}秒")
        return None
    
    def generate(self, prompt, image_base64=None, image_url=None, output_path='output.mp4', duration=10, resolution='720p', aspect_ratio='16:9', extra_body=None):
        """
        生成视频文件
        
        Args:
            prompt: 视频描述提示词
            image_base64: 输入图像的base64编码字符串（可选）
            image_url: 输入图像的URL（可选，优先使用）
            output_path: 输出视频路径
            duration: 视频时长（秒）
            resolution: 视频分辨率，如"720p", "1080p"
            aspect_ratio: 视频宽高比，如"16:9", "9:16"
            extra_body: 额外参数字典
            
        Returns:
            str: 成功返回视频文件路径，失败返回None
        """
        # 1. 提交任务
        task_id = self.submit_task(prompt, image_base64, image_url, duration, resolution, aspect_ratio, extra_body)
        if not task_id:
            print("视频生成任务提交失败，程序退出")
            return None
        
        # 2. 轮询任务结果
        result = self.poll_result(task_id)
        if not result:
            print("未获取到视频结果，程序退出")
            return None
        
        # 3. 从结果中提取视频URL
        video_url = None
        # 根据不同API响应格式获取视频URL
        if isinstance(result, dict):
            # 检查不同的键名
            if 'response' in result and isinstance(result['response'], dict):
                video_url = result['response']['video_url']
                # print(f"✅ 从API响应中提取到视频URL: {video_url}")
                

        
        if not video_url:
            print(f"无法从API响应中提取视频URL，响应内容: {result}")
            return None
        
        # 4. 下载视频文件
        # 注意参数顺序变更: 从download_video(video_url, output_path)改为download_video(video_url, output_path)
        success = self.download_video(video_url, output_path)
        if success:
            print(f"✅ 视频生成成功，保存路径: {output_path}")
            return output_path
        else:
            print(f"❌ 视频下载失败")
            return None
