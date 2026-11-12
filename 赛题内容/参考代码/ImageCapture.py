"""
ImageCapture - 从视频中捕获指定时间点的帧并保存为图片
"""
import cv2
import os
from typing import Optional


class ImageCapture:
    """从视频文件中捕获指定时间点的帧"""
    
    def __init__(self, video_path: str):
        """
        初始化ImageCapture对象
        
        Args:
            video_path: 视频文件路径
        """
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"视频文件不存在: {video_path}")
        
        self.video_path = video_path
        self.cap = cv2.VideoCapture(video_path)
        
        if not self.cap.isOpened():
            raise ValueError(f"无法打开视频文件: {video_path}")
        
        # 获取视频信息
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.duration = self.total_frames / self.fps if self.fps > 0 else 0
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        print(f"视频信息:")
        print(f"  路径: {video_path}")
        print(f"  分辨率: {self.width}x{self.height}")
        print(f"  帧率: {self.fps:.2f} fps")
        print(f"  总帧数: {self.total_frames}")
        print(f"  时长: {self.duration:.2f} 秒")
    
    def capture_frame_at_time(self, time_seconds: float, output_path: str) -> bool:
        """
        捕获指定时间点的帧并保存为图片
        
        Args:
            time_seconds: 时间点（秒）
            output_path: 输出图片路径
            
        Returns:
            bool: 是否成功捕获并保存
        """
        if time_seconds < 0:
            print(f"错误: 时间不能为负数 ({time_seconds}秒)")
            return False
        
        if time_seconds > self.duration:
            print(f"警告: 指定时间 {time_seconds}秒 超过视频时长 {self.duration:.2f}秒")
            print(f"将捕获最后一帧")
            time_seconds = self.duration
        
        # 设置视频位置到指定时间（以毫秒为单位）
        self.cap.set(cv2.CAP_PROP_POS_MSEC, time_seconds * 1000)
        
        # 读取帧
        ret, frame = self.cap.read()
        
        if not ret:
            print(f"错误: 无法读取时间 {time_seconds}秒 处的帧")
            return False
        
        # 保存图片
        success = cv2.imwrite(output_path, frame)
        
        if success:
            actual_time = self.cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
            frame_number = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))
            print(f"成功捕获帧:")
            print(f"  目标时间: {time_seconds:.2f}秒")
            print(f"  实际时间: {actual_time:.2f}秒")
            print(f"  帧编号: {frame_number}")
            print(f"  保存路径: {output_path}")
        else:
            print(f"错误: 保存图片失败: {output_path}")
        
        return success
    
    def capture_multiple_frames(self, time_list: list, output_dir: str = ".", 
                               filename_prefix: str = "frame") -> list:
        """
        捕获多个时间点的帧
        
        Args:
            time_list: 时间点列表（秒）
            output_dir: 输出目录
            filename_prefix: 文件名前缀
            
        Returns:
            list: 成功保存的文件路径列表
        """
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            print(f"创建输出目录: {output_dir}")
        
        saved_files = []
        
        for i, time_seconds in enumerate(time_list):
            output_path = os.path.join(
                output_dir, 
                f"{filename_prefix}_{i}_{time_seconds:.2f}s.png"
            )
            
            print(f"\n[{i+1}/{len(time_list)}] 捕获 {time_seconds}秒 处的帧...")
            
            if self.capture_frame_at_time(time_seconds, output_path):
                saved_files.append(output_path)
        
        print(f"\n完成! 成功捕获 {len(saved_files)}/{len(time_list)} 帧")
        return saved_files
    
    def get_video_info(self) -> dict:
        """
        获取视频信息
        
        Returns:
            dict: 包含视频信息的字典
        """
        return {
            'path': self.video_path,
            'width': self.width,
            'height': self.height,
            'fps': self.fps,
            'total_frames': self.total_frames,
            'duration': self.duration
        }
    
    def close(self):
        """关闭视频文件"""
        if self.cap is not None:
            self.cap.release()
            print("视频资源已释放")
    
    def __enter__(self):
        """支持上下文管理器"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """支持上下文管理器"""
        self.close()
    
    def __del__(self):
        """析构函数"""
        self.close()


def main():
    """测试函数"""
    video_path = "video_1.mp4"
    
    print("=" * 60)
    print("ImageCapture 测试")
    print("=" * 60)
    
    # 使用上下文管理器
    with ImageCapture(video_path) as capturer:
        # 捕获单个帧
        print("\n" + "=" * 60)
        print("测试: 捕获单个时间点的帧")
        print("=" * 60)
        capturer.capture_frame_at_time(2.0, "captured_frame_2.0s.png")
