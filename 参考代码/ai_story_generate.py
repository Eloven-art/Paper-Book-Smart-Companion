import json
from typing import Dict
import random
from typing import Dict, Callable
import requests
import time

from tqdm import trange, tqdm

from prompt_zh import instruction, question_asker_system, expert_system, dlg_based_writer_system, dlg_based_writer_prompt, chapter_writer_system, role_extract_system, role_review_system
import re
from text_agent_base import Gemini3ProAgent

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
    for idx, content in enumerate(chapter_list):
        response[str(idx+1)] = split_sfx_text(content)
    return response

def json_parse_outline(outline):
    outline = outline.strip("```json").strip("```")
    try:
        outline = json.loads(outline)
        if not isinstance(outline, dict):
            return False
        if outline.keys() != {"story_title", "story_outline"}:
            return False
        for chapter in outline["story_outline"]:
            if chapter.keys() != {"chapter_title", "chapter_summary"}:
                return False
    except json.decoder.JSONDecodeError:
        return False
    return True

def parse_list(output):
    try:
        pages = eval(output)
        return isinstance(pages, list)
    except Exception:
        return False

class GeminiAIStroyWriter:
    def __init__(self,base_url, api_key,model,temperature=1.0,
                 max_conv_turns=3, num_outline=3, max_new_token=300):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_conv_turns = max_conv_turns
        self.num_outline = num_outline
        self.max_new_token = max_new_token
    
    def generate_outline(self, params):
        # `params`: story setting like 
        # {
        #     "story_title": "xxx",
        #     "main_role": "xxx",
        #     ......
        # }message: 
        asker = Gemini3ProAgent(
                base_url=self.base_url,
                api_key=self.api_key,
                model=self.model,
                system_prompt=question_asker_system,
                track_history=False)
        
        expert = Gemini3ProAgent(
                base_url=self.base_url,
                api_key=self.api_key,
                model=self.model,
                system_prompt=expert_system,
                track_history=False)

        dialogue = []
        for turn in trange(self.max_conv_turns):
            dialogue_history = "\n".join(dialogue)
            
            question, success = asker.call(
                f"Story setting: {params}\nDialogue history: \n{dialogue_history}\n",
                temperature=self.temperature
            )
            question = question.strip()
            if question == "Thank you for your help!":
                break
            dialogue.append(f"You: {question}")
            answer, success = expert.call(
                f"Story setting: {params}\nQuestion: \n{question}\nAnswer: ",
                temperature=self.temperature
            )
            answer = answer.strip()
            dialogue.append(f"Expert: {answer}")

        # print("\n".join(dialogue))
        writer = Gemini3ProAgent(
                base_url=self.base_url,
                api_key=self.api_key,
                model=self.model,
                system_prompt=dlg_based_writer_system,
                track_history=False)
        
        writer_prompt = dlg_based_writer_prompt.format(
            story_setting=params,
            dialogue_history="\n".join(dialogue),
            num_outline=self.num_outline,
            max_new_token = self.max_new_token,
            language=params['language']
        )

        outline, success = writer.call(writer_prompt, success_check_fn=json_parse_outline)
        print(outline)
        outline = json.loads(outline.strip("```json").strip("```"))
        # print(outline).strip("```json").strip("```")
        return outline

    def generate_story_from_outline(self, outline, params):
        chapter_writer = Gemini3ProAgent(
                base_url=self.base_url,
                api_key=self.api_key,
                model=self.model,
                system_prompt=chapter_writer_system.replace(
                    "{language}", params['language']
                ),
                track_history=False)
        all_pages = []
        for idx, chapter in enumerate(tqdm(outline["story_outline"])):

            chapter_detail, success = chapter_writer.call(
                json.dumps(
                    {
                        "language": params["language"],
                        "knowledge_points": params["knowledge_points"],
                        "completed_story": all_pages,
                        "current_chapter": chapter
                    },
                    ensure_ascii=False
                ),
                success_check_fn=parse_list,
                temperature=self.temperature
            )
            while success is False:
                chapter_detail, success = chapter_writer.call(
                    json.dumps(
                        {
                            "completed_story": all_pages,
                            "current_chapter": chapter
                        },
                        ensure_ascii=False
                    ),
                    seed=random.randint(0, 100000),
                    temperature=self.temperature,
                    success_check_fn=parse_list
                )
            pages = [page.strip() for page in eval(chapter_detail)]
            print(pages)
            all_pages.extend(["".join(pages)])
        # print(all_pages)
        return all_pages

    def call(self, params):
        outline = self.generate_outline(params)
        pages = self.generate_story_from_outline(outline, params)
        return pages
