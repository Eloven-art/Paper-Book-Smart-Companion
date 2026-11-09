# -*- coding: utf-8 -*-

from prompt_zh import knowledge_system, baike_prompt
from text_agent_base import Gemini3ProAgent
import re



class GeminiAIBaikeGene:
    def __init__(self,base_url, api_key, model="gemini-3-pro", temperature=1.0, max_conv_turns=3, num_outline=4, max_new_token=512):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_conv_turns = max_conv_turns
        self.num_outline = num_outline
        self.max_new_token = max_new_token
    
    def check_language(self,text):
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
    
    def call(self, params, chapter):
        # `params`: story setting like 
        # {
        #     "story_title": "xxx",
        #     "main_role": "xxx",
        #     ......
        # }message: 

        params['language'] = self.check_language(chapter)
       
        baike_kw_generator = Gemini3ProAgent(
                base_url=self.base_url,
                api_key=self.api_key,
                model=self.model,
                system_prompt=knowledge_system,
                track_history=False)
        
        baikeKW_prompt = baike_prompt.format(
            story_title=params["story_title"],
            main_role=params["main_role"],
            scene=params["scene"],
            language=params["language"],
            chapter=chapter
        )
       
        # 调用API生成百科知识问答

        outline, success = baike_kw_generator.call(baikeKW_prompt)
        response = outline.strip("```json").strip("```")
        print(f"************* 百科问答 *************: {response}")
        
        return response
