import os 
import binascii
import sys
import base64
from typing import Optional, List, Dict
import requests
import time
import json
from config import *



def decode_base64_to_image(base64_str: str, output_path: str) -> bool:
    """解码 base64 字符串为图片"""
    try:
        # 补全填充字符
        padding = len(base64_str) % 4
        if padding != 0:
            base64_str += "=" * (4 - padding)
        
        # 解码并保存
        image_data = base64.b64decode(base64_str)
        with open(output_path, "wb") as f:
            f.write(image_data)
        return True
    except binascii.Error as e:
        print(f"Base64 解码失败: {e}")
        return False
    except Exception as e:
        print(f"其他错误: {e}")
        return False
    
class OpenAIImageGenerator:
    """图片生成器 - 使用 gemini-3-pro-image 生成吉卜力风格图片"""
    def __init__(self, base_url, api_key, model):
        self.url = base_url 
        self.headers = {
            "Authorization": f"Bearer {api_key}",  
            "Content-Type": "application/json"
        }
        self.model = model
        
    def call(self, params):
        story_text = params.get("story_text", "这是一片蓝蓝的天空")
        story_title = params.get("story_title", "儿童故事")
        main_role = params.get("main_role", "可爱的小动物")
        scene = params.get("scene", "美丽的大自然")
        prev_image_base64 = params.get("prev_image_base64", None)
        paragraph_index = params.get("paragraph_index", 0)
        session_dir = params.get("session_dir", None)
        
        # 构建吉卜力风格的图片生成 prompt
        prompt = f"""现在告诉你故事背景和当前章节的内容，请你生成一张符合当前章节内容的吉卜力风格的图画。
        
以下是故事情节：
    "story_title": "{story_title}",
    "main_role": "{main_role}",
    "scene": "{scene}",
    "chapter_content": "{story_text}"

注意事项：
1. 生成温馨可爱的吉卜力画风图片
2. 画面要充满童趣，色彩明亮柔和
3. 人物表情要生动可爱
4. 背景要精致细腻，有氛围感
{"5. 注意保持人物面容以及故事风格与上一张图像的一致性" if prev_image_base64 else ""}
"""
        
        # 构建消息内容
        message_content = [{"type": "text", "text": prompt}]
        
        # 如果有上一张图片，添加到消息中以保持一致性
        if prev_image_base64:
            message_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{prev_image_base64}"
                }
            })
        
        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "user", "content": message_content}
            ],
            "modalities": ["text", "image"],
            "extra_body": {
                "generationConfig": {
                    "imageConfig": {
                        "imageSize": "1K"
                    }
                }
            }
        })
        
        try:
            max_retries = 6
            for retry in range(max_retries):
                response = requests.request("POST", self.url, headers=self.headers, data=payload, timeout=3000)
                print(f"图片生成 API 状态码: {response.status_code} (尝试 {retry + 1}/{max_retries})")
                
                response_json = response.json()
                
                # 检查响应中是否有错误
                if 'error' in response_json:
                    error_msg = response_json['error']
                    raise Exception(f"API 错误: {error_msg}")
                
                # 检查是否有 choices 字段
                if 'choices' not in response_json:
                    raise Exception(f"API 响应格式错误，缺少 'choices' 字段")
                
                # 提取图片内容
                message = response_json['choices'][0]['message']
                img_res_list = message.get('images', [])
                
                # 如果没有图片，重试
                if not img_res_list and retry < max_retries - 1:
                    print(f"   未获取到图片，重试中...")
                    continue
                
                image_paths = []
                for idx, img in enumerate(img_res_list):
                    image_url = img.get('image_url', {}).get('url', '')
                    if ',' in image_url:
                        base64_str = image_url.split(",")[1]
                        timestamp = int(time.time())
                        filename = f"image_{paragraph_index}_{idx}.png"
                        
                        # 如果有session_dir，保存到对应目录
                        if session_dir:
                            output_path = os.path.join(session_dir, "images", filename)
                        else:
                            output_path = f"{TARGET_DIR}/output_image_{timestamp}_{paragraph_index}_{idx}.png"
                        
                        success = decode_base64_to_image(base64_str, output_path)
                        if success:
                            print(f"图片已保存: {output_path}")
                            image_paths.append(output_path)
                
                if image_paths:
                    return {
                        'success': True,
                        'image_paths': image_paths,
                        'message': message
                    }
            
            # 所有重试都失败
            return {
                'success': False,
                'image_paths': [],
                'message': None
            }
            
        except Exception as e:
            print(f"图片生成错误: {str(e)}")
            raise
