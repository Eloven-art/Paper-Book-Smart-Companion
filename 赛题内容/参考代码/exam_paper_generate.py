# -*- coding: utf-8 -*-
import json
from typing import Dict
import random
from typing import Dict, Callable
import requests
import time

from tqdm import trange, tqdm

from prompt_zh import paper_system, paper_prompt
from text_agent_base import Gemini3ProAgent
import re



class GeminiAIPaperGene:
    def __init__(self, base_url: str,
                 api_key: str,
                 model: str,
                 temperature: float = 1.0,
                 max_new_token: int = 512):
        
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_new_token = max_new_token
    def check_language(self, text):
        # 去除空格和换行
        text = text.strip()
        if not text:
            return "中文"

        # 匹配中文字符的正则
        chinese_chars = re.findall(r'[\u4e00-\u9fa5]', text)
        # 匹配英文字母的正则
        english_chars = re.findall(r'[a-zA-Z]', text)

        if len(chinese_chars) > len(english_chars):
            return "中文"  # 中文
        elif len(english_chars) > len(chinese_chars):
            return "英文"  # 英文
        else:
            return "中文" # 可能是纯数字或标点
    
    def call(self, params):
        # `params`: story setting like 
        # {
        #     "story_title": "xxx",
        #     "main_role": "xxx",
        #     ......
        # }message: 
       
        baike_paper_generator = Gemini3ProAgent(
                base_url=self.base_url,
                api_key=self.api_key,
                model=self.model,
                system_prompt=paper_system,
                track_history=False)
        
        # print(f"paper_prompt: {paper_prompt}")

        # 确保baike_qas和user_querys是字符串格式（用于格式化prompt）
        baike_qas_str = json.dumps(params["baike_qas"], ensure_ascii=False) if isinstance(params["baike_qas"], (list, dict)) else str(params["baike_qas"])
        user_querys_str = json.dumps(params["user_querys"], ensure_ascii=False) if isinstance(params["user_querys"], (list, dict)) else str(params["user_querys"])
        
        final_prompt = paper_prompt.format(
            language=self.check_language(params["story"]),
            sotry=params["story"],
            baike_qas=baike_qas_str,
            user_querys=user_querys_str
        )
       
        # 调用API生成百科知识问答

        outline, success = baike_paper_generator.call(final_prompt)
        
        # 确保outline是字符串类型
        if not isinstance(outline, str):
            # 如果outline不是字符串，尝试转换为字符串
            if isinstance(outline, dict):
                # 如果是字典，直接返回JSON字符串
                response = json.dumps(outline, ensure_ascii=False)
            else:
                # 其他类型，转换为字符串
                response = str(outline)
        else:
            # 如果是字符串，去除可能的markdown代码块标记
            response = outline.strip("```json").strip("```").strip()
        
        return response
