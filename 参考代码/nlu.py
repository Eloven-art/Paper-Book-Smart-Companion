from typing import Optional, List, Dict
import requests
import time
import json
import re

class QAAssistant:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.url = base_url 
        self.headers = {
            "Authorization": f"Bearer {api_key}",  
            "Content-Type": "application/json"
        }
        self.model = model

    
    def check_language(self, text):
        # 去除空格和换行
        text = text.strip()
        if not text:
            return "中文"

        # 匹配中文字符的正则
        chinese_chars = re.findall(r'[\u4e00-\u9fa5]', text)
        # 匹配英文字母的正则
        english_chars = re.findall(r'[a-zA-Z]', text)

        chinese_count = len(chinese_chars)
        english_count = len(english_chars)

        # 逻辑判断
        if chinese_count > english_count:
            return "中文"  # 中文
        elif english_count > 0:
            return "英文"  # 英文
        else:
            return "中文" # 可能是纯数字或标点
    
    def call(self, question, max_length=30):
        system_prompt = """你是一个友好的故事小助手，专门为小朋友服务。你需要：
                    1. 用简单易懂、活泼有趣的语言回答问题
                    2. 回答要简短，适合语音播放
                    3. 如果小朋友想继续听故事，告诉他们说"继续播放"
                    4. 保持温暖、鼓励的语气
                    5. 回答问题时，要注意用户问题的语言类型，中文问题用中文回答，英文问题用英文回答"""
        
        prompt = f"{question}\n\n请用简洁、温暖的{self.check_language(question)}语言回答，控制在{max_length}字以内。"
        
        payload = json.dumps({
            "model": self.model,
            "temperature": 0.8,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]
        })
        
        response = requests.post(self.url, headers=self.headers, data=payload, timeout=30)
        result = response.json()
        content = result['choices'][0]['message']['content']
        return content
