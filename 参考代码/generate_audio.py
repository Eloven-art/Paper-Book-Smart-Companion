import requests
import json
import time
import sys


class AudioGenerator:
    """音频生成器类"""
    
    def __init__(self, base_url=None, 
                 api_key='xxxx',
                 model='audio1.0',
                 priority='5',
                 verbose=True):
        """
        初始化音频生成器
        
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
        self.poll_interval = 2  # 轮询间隔（秒）
        self.max_attempts = 60  # 最大轮询次数
        
    def submit_task(self, prompt, duration=4, extra_body=None):
        """
        提交音频生成任务
        
        Args:
            prompt: 音频描述提示词
            duration: 音频时长（秒）
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
        
        data = {
            "model": self.model,
            "prompt": prompt,
            "duration": duration,
            "extra_body": extra_body or {}
        }
        
        if self.verbose:
            print("🔊 提交音频生成任务...")
            print(f"提示词: {prompt}")
            print(f"时长: {duration}秒")
        
        try:
            response = requests.post(url, headers=headers, json=data, allow_redirects=True)
            
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
                
        except requests.exceptions.RequestException as e:
            if self.verbose:
                print(f"请求失败: {e}")
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
            print(f"查询任务结果 (ID: {task_id})")
        
        try:
            response = requests.get(url, headers=headers)
            
            if response.status_code in [200, 201]:
                result = response.json()
                # 只有当status为3（完成）时才返回结果
                if result.get('status') == 3:
                    if verbose:
                        print("任务已完成")
                    return result
                else:
                    return None
            else:
                if verbose:
                    print(f"获取结果失败: {response.text}")
                return None
                
        except requests.exceptions.RequestException as e:
            if verbose:
                print(f"请求失败: {e}")
            return None
    
    def download_audio(self, url, output_file):
        """
        下载音频文件
        
        Args:
            url: 音频文件URL
            output_file: 输出文件路径
            
        Returns:
            bool: 下载成功返回True，失败返回False
        """
        if self.verbose:
            print(f"正在下载音频...")
        
        try:
            response = requests.get(url, stream=True)
            
            if response.status_code in [200, 201]:
                with open(output_file, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                return True
            else:
                if self.verbose:
                    print(f"下载失败，状态码: {response.status_code}")
                return False
                
        except requests.exceptions.RequestException as e:
            if self.verbose:
                print(f"下载失败: {e}")
            return False
        except IOError as e:
            if self.verbose:
                print(f"文件写入失败: {e}")
            return False
    
    def poll_result(self, task_id):
        """
        轮询获取任务结果
        
        Args:
            task_id: 任务ID
            
        Returns:
            result: 任务结果字典，超时或失败返回None
        """
        if self.verbose:
            print(f"等待任务完成（每{self.poll_interval}秒查询一次）...")
        
        attempt = 0
        start_time = time.time()
        
        while attempt < self.max_attempts:
            attempt += 1
            
            if self.verbose:
                print(f"\r查询中 ({attempt}/{self.max_attempts})...", end='', flush=True)
            
            # 获取结果（静默模式）
            result = self.get_result(task_id, verbose=False)
            
            if result:
                # 获取音频URL（从response对象中提取）
                response_data = result.get('response', {})
                audio_url = response_data.get('audio_url')
                
                if audio_url:
                    elapsed_time = int(time.time() - start_time)
                    if self.verbose:
                        print()  # 换行
                        print(f"任务完成! (耗时: {elapsed_time}秒)")
                    return result
            
            # 等待下一次查询
            if attempt < self.max_attempts:
                time.sleep(self.poll_interval)
        
        # 超时
        elapsed_time = int(time.time() - start_time)
        if self.verbose:
            print()  # 换行
            print(f"超时: 已查询 {attempt} 次（{elapsed_time}秒），任务未完成")
        return None
    
    def generate(self, prompt, duration=4, extra_body=None, output_file=None):
        """
        生成音频的完整流程：提交任务 -> 轮询结果 -> 下载音频
        
        Args:
            prompt: 音频描述提示词
            duration: 音频时长（秒）
            extra_body: 额外参数字典
            output_file: 输出文件路径，默认为 task_id.wav
            
        Returns:
            str: 成功返回输出文件路径，失败返回None
        """
        # 步骤1: 提交任务
        task_id = self.submit_task(prompt, duration, extra_body)
        
        if not task_id:
            if self.verbose:
                print("任务提交失败")
            return None
        
        if self.verbose:
            print(f"任务ID: {task_id}")
        
        # 步骤2: 轮询获取结果
        result = self.poll_result(task_id)
        
        if not result:
            if self.verbose:
                print(f"可以稍后使用 task_id={task_id} 查询结果")
            return None
        
        # 步骤3: 下载音频
        response_data = result.get('response', {})
        audio_url = response_data.get('audio_url')
        
        if not audio_url:
            if self.verbose:
                print("未找到音频URL")
            return None
        
        if self.verbose:
            print(f"音频URL: {audio_url}")
        
        # 确定输出文件名
        if output_file is None:
            output_file = f"{task_id}.wav"
        
        # 下载音频文件
        if self.download_audio(audio_url, output_file):
            print(f"已保存: {output_file}")
            return output_file
        else:
            if self.verbose:
                print("下载失败")
            return None


def main():
    """主函数"""
    # 创建音频生成器（verbose=True 显示详细信息，verbose=False 简洁输出）
    generator = AudioGenerator(priority='5', verbose=False)
    
    # 配置任务参数
    prompt = "小兔子乖乖，把门开开，快点开开，我要进来。"
    duration = 3
    extra_body = {}
    output_file = 'output_2.wav'
    
    # 生成音频
    result = generator.generate(prompt, duration, extra_body, output_file=output_file)
    
    if not result:
        sys.exit(1)
