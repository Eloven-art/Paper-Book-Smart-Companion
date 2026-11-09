
import json
from typing import Callable
import requests


class Gemini3ProAgent:
    def __init__(self,
                 base_url: str,
                 api_key: str,
                 model: str,
                 system_prompt: str = None,
                 track_history: bool = False):
        self.url = base_url
        self.headers = {
            "Authorization": f"Bearer {api_key}",  
            "Content-Type": "application/json"
        }
        self.model = model
        self.system_prompt = system_prompt
        track_history = False
        if self.system_prompt is None:
            self.history = []
        else:
            self.history = [
                {"role": "system", "content": self.system_prompt}
            ]
        self.track_history = track_history
    
    def basic_success_check(self, response):
        if not response or 'choices' not in response:
            print(f"输出不正确：{response}")
            return False
        else:
            return True

    def call(self,
             prompt: str,
             top_p: float = 0.95,
             max_new_token: int = 512,
             temperature: float = 1.0,
             max_try: int = 10,
             success_check_fn: Callable = None
             ):
        
        self.history[0]['content'] = self.history[0]['content']
        self.history.append({
            "role": "user",
            "content": prompt
        })
        success = False
        try_times = 0

        while try_times < max_try:
            payload = json.dumps({
            "model": self.model,
            "temperature": temperature,
            "max_new_tokens": max_new_token,
            "top_p": top_p,
            "stop": ["<|endoftext|>","<|im_end|>"],
            "messages": self.history
            })
            try:

                response = requests.request("POST", self.url, headers=self.headers, data=payload, timeout=90)
                response = response.json()
                
                if success_check_fn is None:
                    success_check_fn = lambda x: True
                if self.basic_success_check(response):
                    response = response['choices'][0]['message']
                    response = json.loads(json.dumps(response,
                                        ensure_ascii=False))
                    # print(f"response: {response}")
                    response = response['content']
                    if success_check_fn(response):
                        self.history.append({
                            "role": "assistant",
                            "content": response
                        })
                        success = True
                        break
                else:
                    try_times += 1
            except Exception as e:
                print(f"发生异常:{e}")
                print(f"response：{response}")
                try_times+=1
        
        if not self.track_history:
            if self.system_prompt is not None:
                self.history = self.history[:1]
            else:
                self.history = []
        
        return response, success
