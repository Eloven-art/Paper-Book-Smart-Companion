import json
import requests
from typing import Dict
import re

def split_sfx_text(text):
    # 正则表达式匹配 <SFX>...</SFX> 标签对
    pattern = r'(<SFX>)(.*?)(</SFX>)'
    parts = []
    last_end = 0
    
    # 遍历所有匹配的标签
    for match in re.finditer(pattern, text, re.DOTALL):
        start, end = match.span()
        # 提取标签前的普通文本
        if start > last_end:
            normal_text = text[last_end:start].strip()
            if normal_text:
                parts.append((normal_text, 0))
        # 提取标签内的音效文本
        sfx_text = match.group(2).strip()
        if sfx_text:
            parts.append((sfx_text, 1))
        last_end = end
    
    # 提取最后一个标签后的普通文本
    if last_end < len(text):
        normal_text = text[last_end:].strip()
        if normal_text:
            parts.append((normal_text, 0))
    
    return parts

def parse_output(chapter_list):
    response = {}
    for idx,content in enumerate(chapter_list):
        response[str(idx+1)] = split_sfx_text(content)
    return response

class OpenAIStoryWriter:
    """故事生成器"""
    def __init__(self, base_url, api_key, model):
        self.url = base_url 
        self.headers = {
            "Authorization": f"Bearer {api_key}",  
            "Content-Type": "application/json"
        }
        self.model = model
        
    def call(self, params):
        story_topic = params.get("story_topic", "默认主题")
        main_role = params.get("main_role", "某人")
        scene = params.get("scene", "某个地方")
        knowledge_points = params.get("knowledge_points", "")
        language = params.get("language", "中文")
        
        system_prompt = """角色设定：你是一位专业的儿童故事书作家，专注创作 3-11 岁睡前故事。作品需逻辑严谨、情感温暖，且嵌入沉浸式音效描述，适配儿童睡前阅读的舒缓氛围。
                        ## 任务准则：
                            ### 叙述风格：采用纯叙述体，聚焦情节推进与环境描写。禁止出现任何角色对话（如 “XX 说：……” 句式），禁止任何形式的读者互动（如 “你知道吗？”“接下来会发生什么呢？”）。
                            ### 篇章逻辑：故事需具备完整的起承转合，章节间环环相扣、逻辑自洽，所有情节均服务于一个核心主题（如诚实、勇敢、分享、友善等）。
                            ### 篇章与字数限制：篇章数量严格控制在3章以内；单篇章字数限定 50-70 字；篇章之间用 两个换行符（\n\n） 分隔，无需添加篇章标题。
                        ## 核心音效（SFX）强制规则
                            ### 触发条件：文中出现动作、自然现象、动物叫声的描述后，必须紧跟 <SFX></SFX> 标签对，无例外。
                            ### 标签内容要求：标签内必须是前置描述内容的专属音效，格式为 具体音效描述：拟声词，确保音效与前文强关联，不脱离情节。
                            ### 嵌入规范：音效标签需无缝嵌入正文，不可独立成段；示例：小松鼠拖着松果钻进树洞<SFX>爪子扒拉树皮的声音：窸窸窣窣</SFX>。
                        ## 情感与价值导向
                            调性要求：故事基调必须温暖、愉快、充满关怀，避免紧张、惊悚、悲伤情节，符合睡前阅读的放松需求。
                            教育意义：潜移默化传递积极价值观或行为准则，价值观需自然融入情节，不生硬说教。
                            输出要求：文字简洁明快，符合儿童语言理解能力；情节连贯紧凑，无冗余修饰；音效标签精准匹配前文内容，助力沉浸式阅读体验。"""
        if knowledge_points:
            prompt = f"请生成一个以{story_topic}为主题，主要角色是{main_role}，场景设定在{scene}的充满教育意义的故事，需要包含具体的知识点：{knowledge_points}，语言类型是{language}"
        else:
            prompt = f"请生成一个以{story_topic}为主题，主要角色是{main_role}，场景设定在{scene}的充满教育意义的故事。语言类型是{language}"
        
        payload = json.dumps({
            "model": self.model,
            "temperature": 0.8,
            "stop": ["<|endoftext|>","<|im_end|>", "<|im_start|>"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                ]}
            ]
        })
        
        try:
            response = requests.request("POST", self.url, headers=self.headers, data=payload, timeout=300)
            print(f"故事生成 API 状态码: {response.status_code}")
            
            response_json = response.json()
            
            # 检查响应中是否有错误
            if 'error' in response_json:
                error_msg = response_json['error']
                raise Exception(f"API 错误: {error_msg}")
            
            # 检查是否有 choices 字段
            if 'choices' not in response_json:
                raise Exception(f"API 响应格式错误，缺少 'choices' 字段")
            
            # 提取故事内容
            message = response_json['choices'][0]['message']
            story = message['content']
            pages = story.split("\n\n")
            
            print(f"\n📖 故事生成成功:")
            print(f"   总长度: {len(story)} 字符")
            print(f"   分段数: {len(pages)} 段")
            for i, page in enumerate(pages, 1):
                page_preview = page.strip()[:30] if page.strip() else "(空)"
                print(f"   第{i}段: {page_preview}... ({len(page.strip())}字)")
            print()
            
            return {
                'success': True,
                'content': story,
                'pages': pages
            }
        except Exception as e:
            print(f"故事生成错误: {str(e)}")
            raise
