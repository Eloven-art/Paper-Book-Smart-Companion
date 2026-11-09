import requests
import json
import time
import sys
from config import *

class MusicGenerator:
    """音乐生成器"""
    def __init__(self, base_url=None, 
                 api_key='xxxx',
                 model='imagination-bgm',
                 priority='5',
                 verbose=False):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.priority = priority
        self.verbose = verbose
        self.poll_interval = 0.5
        self.max_attempts = 60
        
    def submit_task(self, prompt, duration=30, seed=1):
        """提交音乐生成任务"""
        url = f'{self.base_url}/v1/async/chat'
        
        headers = {
            'api-key': self.api_key,
            'x-apx-task-priority': self.priority,
            'X-APX-Model': self.model,
            'Content-Type': 'application/json'
        }
        
        data = {
            "model": self.model,
            "prompt": prompt,
            "duration": duration,
            "seed": seed
        }
        
        if self.verbose:
            print(f"🎵 提交音乐生成任务")
            print(f"   URL: {url}")
            print(f"   Model: {self.model}")
            print(f"   Prompt: {prompt[:50]}...")
            print(f"   Duration: {duration}s")
        
        try:
            response = requests.post(url, headers=headers, json=data, allow_redirects=True, timeout=30)
            
            if self.verbose:
                print(f"   响应状态码: {response.status_code}")
            
            if response.status_code in [200, 201]:
                result = response.json()
                
                if self.verbose:
                    print(f"   响应内容: {result}")
                
                task_id = result.get('task_id') or result.get('id') or result.get('request_id')
                if task_id:
                    return task_id
                else:
                    print(f"   ❌ 响应中未找到任务ID")
                    print(f"   完整响应: {result}")
                    return None
            else:
                print(f"   ❌ 任务提交失败，状态码: {response.status_code}")
                print(f"   响应内容: {response.text[:500]}")
                return None
                
        except Exception as e:
            print(f"   ❌ 请求异常: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def get_result(self, task_id, verbose=None):
        """获取任务结果"""
        if verbose is None:
            verbose = self.verbose
            
        url = f'{self.base_url}/v1/async/results/{task_id}'
        
        headers = {
            'api-key': self.api_key,
            'X-APX-Model': self.model,
            'Content-Type': 'application/json; charset=utf-8'
        }
        
        try:
            response = requests.get(url, headers=headers)
            
            if response.status_code in [200, 201]:
                result = response.json()
                status = result.get('status')
                
                if verbose:
                    print(f"   📊 任务状态: {status} (3=完成, 4=失败)")
                
                if status == 3:
                    return result
                elif status == 4:
                    # 任务失败
                    error_msg = result.get('error', result.get('message', '未知错误'))
                    print(f"   ❌ 任务失败: {error_msg}")
                    print(f"   完整响应: {result}")
                    return None
                else:
                    # 任务进行中
                    return None
            else:
                if verbose:
                    print(f"   ❌ 获取结果失败，状态码: {response.status_code}")
                return None
                
        except Exception as e:
            if verbose:
                print(f"   ❌ 请求失败: {e}")
            return None
    
    def download_audio(self, url, output_file):
        """下载音频文件"""
        try:
            response = requests.get(url, stream=True)
            
            if response.status_code in [200, 201]:
                with open(output_file, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                return True
            else:
                return False
                
        except Exception as e:
            print(f"下载失败: {e}")
            return False
    
    def poll_result(self, task_id):
        """轮询获取任务结果"""
        attempt = 0
        start_time = time.time()
        
        while attempt < self.max_attempts:
            attempt += 1
            
            result = self.get_result(task_id, verbose=False)
            
            if result:
                response_data = result.get('response', {})
                audio_url = response_data.get('audio_url')
                
                if audio_url:
                    elapsed_time = int(time.time() - start_time)
                    print(f"   ⏱️  音乐生成完成! (耗时: {elapsed_time}秒, 尝试次数: {attempt})")
                    return result
                else:
                    # 任务完成但没有音频URL
                    status = result.get('status')
                    print(f"   ⚠️  任务状态: {status}, 但未找到音频URL")
            
            # 每10次尝试打印一次进度
            if attempt % 10 == 0:
                elapsed = int(time.time() - start_time)
                print(f"   ⏳ 等待中... (已等待 {elapsed}秒, 尝试 {attempt}/{self.max_attempts})")
            
            if attempt < self.max_attempts:
                time.sleep(self.poll_interval)
        
        elapsed_time = int(time.time() - start_time)
        print(f"   ❌ 音乐生成超时 (等待了 {elapsed_time}秒, {self.max_attempts}次尝试)")
        return None
    
    def generate(self, story_content, duration=30, seed=1, output_file=None):
        """生成音乐的完整流程"""
        # 优化提示词：更详细的描述
        prompt = f"生成一段轻快愉悦的儿童背景音乐，适合讲故事时播放。故事内容：{story_content[:80]}"
        
        print(f"   📝 音乐提示词: {prompt}")
        print(f"   ⏱️  时长: {duration}秒")
        
        # 提交任务
        task_id = self.submit_task(prompt, duration, seed)
        
        if not task_id:
            print("   ❌ 音乐任务提交失败")
            return None
        
        print(f"   🆔 音乐任务ID: {task_id}")
        print(f"   ⏳ 等待音乐生成...")
        
        # 轮询获取结果
        result = self.poll_result(task_id)
        
        if not result:
            print("   ❌ 音乐生成超时或失败")
            return None
        
        # 下载音频
        response_data = result.get('response', {})
        audio_url = response_data.get('audio_url')
        
        if not audio_url:
            print(f"   ❌ 未找到音频URL，响应数据: {response_data}")
            return None
        
        if output_file is None:
            output_file = f"{TARGET_DIR}/music_{int(time.time())}.wav"
        
        print(f"   📥 下载音乐: {audio_url}")
        
        if self.download_audio(audio_url, output_file):
            print(f"   ✅ 音乐已保存: {output_file}")
            return output_file
        else:
            print(f"   ❌ 音乐下载失败")
            return None
