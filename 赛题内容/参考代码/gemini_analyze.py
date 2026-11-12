import json
import requests
import base64
from typing import Dict, Generator, Union, List, Tuple
import os
from PIL import Image, ImageDraw, ImageFont
import random


class ImageObjectAnalyzer:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.url = base_url 
        self.headers = {
            "Authorization": f"Bearer {api_key}",  
            "Content-Type": "application/json"
        }
        self.model = model
        
        
    
    def _encode_image(self, image_path: str) -> str:
        """
        将图片文件编码为base64字符串
        
        Args:
            image_path: 图片文件路径
            
        Returns:
            str: base64编码的图片字符串
        """
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"图片文件不存在: {image_path}")
        
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    
    def _get_image_mime_type(self, image_path: str) -> str:
        """
        根据文件扩展名获取MIME类型
        
        Args:
            image_path: 图片文件路径
            
        Returns:
            str: MIME类型字符串
        """
        ext = os.path.splitext(image_path)[1].lower()
        mime_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
            '.bmp': 'image/bmp'
        }
        return mime_types.get(ext, 'image/jpeg')
    
    def call(self, params):
        """
        调用API进行图像分析
        
        Args:
            params: 参数字典，包含：
                - image_path: 图片文件路径（必需）
                - prompt: 自定义提示词（可选）
                - max_objects: 最大物体数量（可选，默认10）
                - debug: 是否输出调试信息（可选，默认False）
            
        Returns:
            返回包含物体及坐标的字典
        """
        return self._call_non_stream(params)
    
    def _call_non_stream(self, params) -> Dict:
        """非流式输出模式，返回解析后的字典"""
        image_path = params.get("image_path", "")
        if not image_path:
            raise ValueError("必须提供image_path参数")
        
        custom_prompt = params.get("prompt", "")
        max_objects = params.get("max_objects", 5)
        debug = params.get("debug", False)
        
        # 编码图片
        if debug:
            print(f"[DEBUG] 开始编码图片: {image_path}", flush=True)
        base64_image = self._encode_image(image_path)
        if debug:
            print(f"[DEBUG] 图片编码完成, base64长度: {len(base64_image)}", flush=True)
        mime_type = self._get_image_mime_type(image_path)
        if debug:
            print(f"[DEBUG] MIME类型: {mime_type}", flush=True)
        
        # 构建系统提示词
        system_prompt = """核心身份：你是精通多类别图像目标检测的专业助手，具备高精度物体识别与像素级边界框定位能力，可适配写实、动漫、插画、实景等各类图像风格。
            核心任务：基于用户上传的图片，完成全图目标检测，输出所有可识别物体的类别名称与对应的精准边界框绝对坐标，同时提供图像基础尺寸信息。
            检测执行规则
            目标全覆盖：识别图像中所有可见物体，区分核心主体（视觉占比最大、最突出）与次要目标（背景元素、小物件），按 “核心→次要” 优先级排序输出。
            类别命名规范
            已知物体标注标准类别名（如：猫、手机、红色轿车、木质餐桌），同时还需要输出该物体的英文描述；
            特殊 / 原创物体标注特征化名称（如：蓝白渐变卡通机器人、复古花纹陶瓷花瓶）；
            同类多个物体无需区分编号（如：人物；椅子）。
            边界框定位精度要求
            坐标格式：[x1, y1, x2, y2]（x1/y1 = 目标左上角像素坐标；x2/y2 = 目标右下角像素坐标）；
            """
        
        # system_prompt = """你是一个专业的图像目标检测助手，能够准确识别图片中的主体并精确定位它们的边界框。你的标注准确度非常高，边界框能够紧密贴合物体轮廓。"""
        
        if custom_prompt:
            prompt = custom_prompt
        else:
            prompt = f"""请仔细分析这张图片，识别出图片中最显著的{max_objects}个物体，并为每个物体标注精确的边界框的绝对位置坐标。请先观察图片，确定每个物体的区域坐标，以json格式返回。
                            JSON格式要求：
                {{
                    "中文物体类别名称1<English Object1>": [左上角x, 左上角y, 右下角x, 右下角y],
                    "中文物体类别名称2<English Object2>": [左上角x, 左上角y, 右下角x, 右下角y]
                }}

                输出要求：
                1. 坐标原点(0,0)在图片左上角，x向右增大，y向下增大
                2. 坐标的数值是像素点的数量，所有坐标必须是整数
                3. 请严格按照json格式返回，不要包含任何多余的换行符和转义字符
                """
            #         """


        
        payload = json.dumps({
            "model": self.model,
            "temperature": 0.1,  # 使用很低的温度以获得更精确、稳定的坐标
            "stream": False,
            "stop": ["<|endoftext|>", "<|im_end|>", "<|im_start|>"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{base64_image}"
                        }
                    }
                ]}
            ]
        })
        
        # 非流式输出
        if debug:
            print(f"[DEBUG] 准备发送 POST 请求到: {self.url}", flush=True)
            print(f"[DEBUG] Payload 长度: {len(payload)} 字节", flush=True)
        response = requests.post(self.url, headers=self.headers, data=payload, timeout=300)
        if debug:
            print(f"[DEBUG] 收到响应, 状态码: {response.status_code}", flush=True)
        response_json = response.json()
        
        if debug:
            print("="*60)
            print("API 完整响应：")
            print(json.dumps(response_json, ensure_ascii=False, indent=2))
            print("="*60)
        
        # 检查响应格式
        if 'choices' not in response_json:
            error_msg = f"API响应格式错误，缺少'choices'字段。完整响应: {json.dumps(response_json, ensure_ascii=False, indent=2)}"
            print(f"❌ {error_msg}")
            return {"error": "API响应格式错误", "raw_response": response_json}
        
        response_message = response_json['choices'][0]['message']
        content = response_message['content']
        
        if debug:
            print("="*60)
            print("API 返回内容：")
            print(content)
            print("="*60)
        
        # 尝试解析JSON
        try:
            # 如果返回的内容包含```json标记，需要提取
            cleaned_content = content
            if '```json' in content:
                # 提取JSON部分
                start = content.find('```json') + 7
                end = content.find('```', start)
                cleaned_content = content[start:end].strip()
            elif '```' in content:
                # 提取代码块
                start = content.find('```') + 3
                end = content.find('```', start)
                cleaned_content = content[start:end].strip()
            
            if debug and cleaned_content != content:
                print("清理后的JSON内容：")
                print(cleaned_content)
                print("="*60)
            
            result = json.loads(cleaned_content)
            return result
        except json.JSONDecodeError as e:
            print(f"❌ JSON解析失败: {e}")
            print(f"原始内容:\n{content}")
            # 返回原始内容
            return {"error": "JSON解析失败", "raw_content": content}


def denormalize_coordinates(normalized_coords: List[float], img_width: int, img_height: int) -> List[int]:
    """
    反归一化坐标：将0-1范围的相对坐标转换为像素坐标
    
    Args:
        normalized_coords: 归一化坐标 [x1, y1, x2, y2]，值在0-1之间
        img_width: 图片宽度（像素）
        img_height: 图片高度（像素）
        
    Returns:
        绝对坐标 [x1, y1, x2, y2]，像素值
        
    示例:
        # 输入: 归一化坐标 [0.271, 0.425, 0.492, 0.842]
        # 图片: 1920 x 1080
        denormalize_coordinates([0.271, 0.425, 0.492, 0.842], 1920, 1080)
        # 输出: [520, 459, 945, 909]
        
    转换公式:
        x_pixel = x_normalized * 图片宽度
        y_pixel = y_normalized * 图片高度
    """
    x1, y1, x2, y2 = normalized_coords
    
    # 反归一化：相对坐标 * 图片尺寸 = 绝对坐标
    x1_abs = int(x1 * img_width/1000)
    y1_abs = int(y1 * img_height/1000)
    x2_abs = int(x2 * img_width/1000)
    y2_abs = int(y2 * img_height/1000)
    
    return [x1_abs, y1_abs, x2_abs, y2_abs]


def normalize_coordinates(absolute_coords: List[int], img_width: int, img_height: int) -> List[float]:
    """
    归一化坐标：将像素坐标转换为0-1范围的相对坐标
    
    Args:
        absolute_coords: 绝对坐标 [x1, y1, x2, y2]，像素值
        img_width: 图片宽度（像素）
        img_height: 图片高度（像素）
        
    Returns:
        归一化坐标 [x1, y1, x2, y2]，值在0-1之间
        
    示例:
        # 输入: 绝对坐标 [520, 459, 945, 909]
        # 图片: 1920 x 1080
        normalize_coordinates([520, 459, 945, 909], 1920, 1080)
        # 输出: [0.271, 0.425, 0.492, 0.842]
        
    转换公式:
        x_normalized = x_pixel / 图片宽度
        y_normalized = y_pixel / 图片高度
    """
    x1, y1, x2, y2 = absolute_coords
    
    # 归一化：绝对坐标 / 图片尺寸 = 相对坐标
    x1_norm = x1 / img_width
    y1_norm = y1 / img_height
    x2_norm = x2 / img_width
    y2_norm = y2 / img_height
    
    return [x1_norm, y1_norm, x2_norm, y2_norm]


def get_predefined_colors(num_colors: int = 20) -> List[Tuple[int, int, int]]:
    """
    获取预定义的明亮颜色列表
    
    Args:
        num_colors: 需要的颜色数量
        
    Returns:
        颜色列表
    """
    # 预定义一些明亮易区分的颜色
    predefined = [
        (255, 0, 0),      # 红色
        (0, 255, 0),      # 绿色
        (0, 0, 255),      # 蓝色
        (255, 255, 0),    # 黄色
        (255, 0, 255),    # 品红
        (0, 255, 255),    # 青色
        (255, 128, 0),    # 橙色
        (128, 0, 255),    # 紫色
        (255, 0, 128),    # 粉红
        (0, 255, 128),    # 春绿
        (128, 255, 0),    # 黄绿
        (0, 128, 255),    # 天蓝
        (255, 128, 128),  # 浅红
        (128, 255, 128),  # 浅绿
        (128, 128, 255),  # 浅蓝
        (255, 255, 128),  # 浅黄
        (255, 128, 255),  # 浅品红
        (128, 255, 255),  # 浅青
        (192, 0, 0),      # 深红
        (0, 192, 0),      # 深绿
    ]
    
    # 如果需要更多颜色，循环使用
    colors = []
    for i in range(num_colors):
        colors.append(predefined[i % len(predefined)])
    
    return colors


def visualize_objects(image_path: str, detection_result: Union[Dict, List], 
                      output_path: str = None, 
                      box_color: Tuple[int, int, int] = None,
                      box_width: int = 3,
                      font_size: int = 20,
                      show_label: bool = True,
                      random_colors: bool = True,
                      fill_alpha: float = 0.0) -> str:
    """
    在图片上可视化检测到的物体边界框（支持相对坐标和绝对坐标）
    
    Args:
        image_path: 原始图片路径
        detection_result: 物体检测结果，支持两种格式：
            格式1 (字典): {"物体名称": [x1, y1, x2, y2], ...}
            格式2 (列表): [{"label": "物体名称", "box_2d": [x1, y1, x2, y2]}, ...]
            坐标支持：
            - 相对坐标 (0-1之间): 表示相对于图片宽高的比例
            - 绝对坐标 (>1): 表示像素坐标
        output_path: 输出图片路径，默认为原图名_annotated.jpg
        box_color: 边界框颜色 (R, G, B)，当random_colors=False时使用
        box_width: 边界框线宽，默认3
        font_size: 标签字体大小，默认20
        show_label: 是否显示标签文字，默认True
        random_colors: 是否为每个物体使用随机颜色，默认True
        fill_alpha: 边界框填充透明度(0-1)，0为不填充，默认0
        
    Returns:
        str: 输出图片的路径
        
    示例:
        # 字典格式（绝对坐标）
        visualize_objects("image.jpg", {"猫": [10, 20, 100, 200]})
        
        # 列表格式（相对坐标）
        result = [
            {"label": "猫", "box_2d": [0.1, 0.2, 0.5, 0.8]},
            {"label": "狗", "box_2d": [0.6, 0.3, 0.9, 0.7]}
        ]
        visualize_objects("image.jpg", result)
    """
    # 检查输入
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"图片文件不存在: {image_path}")
    
    # 处理错误情况
    if isinstance(detection_result, dict) and "error" in detection_result:
        print(f"⚠️  检测结果包含错误，无法可视化")
        return None
    
    # 加载图片
    try:
        image = Image.open(image_path)
        draw = ImageDraw.Draw(image)
        img_width, img_height = image.size
    except Exception as e:
        print(f"❌ 加载图片失败: {e}")
        return None
    
    # 设置输出路径
    if output_path is None:
        base_name = os.path.splitext(image_path)[0]
        output_path = f"{base_name}_annotated.jpg"
    
    # ========== 统一数据格式 ==========
    # 将不同格式转换为统一的字典格式: {"标签": [x1, y1, x2, y2]}
    normalized_result = {}
    
    if isinstance(detection_result, list):
        # 格式1: 列表格式 [{"label": "名称", "box_2d": [x1, y1, x2, y2]}, ...]
        print("🔄 检测到列表格式数据")
        for item in detection_result:
            if isinstance(item, dict) and "label" in item and "box_2d" in item:
                label = item["label"]
                box = item["box_2d"]
                normalized_result[label] = box
            else:
                print(f"⚠️  忽略格式错误的项: {item}")
                
    elif isinstance(detection_result, dict):
        # 格式2: 字典格式 {"名称": [x1, y1, x2, y2], ...}
        print("🔄 检测到字典格式数据")
        normalized_result = detection_result
    else:
        print(f"❌ 不支持的数据格式: {type(detection_result)}")
        return None
    
    if not normalized_result:
        print("⚠️  没有可绘制的物体")
        return None
    
    # 尝试加载中文字体
    try:
        # macOS 中文字体路径
        font_paths = [
            "/System/Library/Fonts/PingFang.ttc",  # macOS PingFang
            "/System/Library/Fonts/STHeiti Light.ttc",  # macOS 黑体
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",  # Linux
            "C:\\Windows\\Fonts\\simhei.ttf",  # Windows 黑体
            "C:\\Windows\\Fonts\\msyh.ttf",  # Windows 微软雅黑
        ]
        
        font = None
        for font_path in font_paths:
            if os.path.exists(font_path):
                font = ImageFont.truetype(font_path, font_size)
                break
        
        if font is None:
            print("⚠️  未找到中文字体，将使用默认字体（可能无法显示中文）")
            font = ImageFont.load_default()
    except Exception as e:
        print(f"⚠️  字体加载失败，使用默认字体: {e}")
        font = ImageFont.load_default()
    
    # 准备颜色
    if random_colors:
        colors = get_predefined_colors(len(normalized_result))
    else:
        colors = [box_color or (255, 0, 0)] * len(normalized_result)
    
    # 统计信息
    total_objects = 0
    valid_boxes = 0
    
    print(f"\n📊 开始绘制边界框...")
    print(f"图片尺寸: {img_width} x {img_height}")
    print(f"检测到的物体数量: {len(normalized_result)}\n")
    
    # 绘制每个物体的边界框
    for idx, (obj_name, coords) in enumerate(normalized_result.items()):
        total_objects += 1
        
        # 验证坐标格式
        if not isinstance(coords, (list, tuple)) or len(coords) != 4:
            print(f"⚠️  '{obj_name}' 坐标格式错误: {coords}")
            continue
        
        try:
            # 解析坐标（可能是float或int）
            x1, y1, x2, y2 = coords
            
            # 判断是相对坐标(归一化)还是绝对坐标(像素)
            # 如果所有坐标都在0-1之间，则认为是归一化的相对坐标
            # is_relative = all(0 <= c <= 1 for c in [x1, y1, x2, y2])
            is_relative = True
            if is_relative:
                # 相对坐标 -> 绝对坐标（反归一化）
                # 公式: 像素坐标 = 归一化坐标 * 图片尺寸
                absolute_coords = denormalize_coordinates(
                    [x1, y1, x2, y2],
                    img_width,
                    img_height
                )
                x1_abs, y1_abs, x2_abs, y2_abs = absolute_coords
                coord_type = "归一化"
            else:
                # 绝对坐标（已经是像素值）
                x1_abs = int(x1)
                y1_abs = int(y1)
                x2_abs = int(x2)
                y2_abs = int(y2)
                coord_type = "绝对"
            
            # 验证坐标有效性
            if x1_abs >= x2_abs or y1_abs >= y2_abs:
                print(f"⚠️  '{obj_name}' 坐标无效 ({coord_type}坐标)")
                print(f"    原始: [{x1}, {y1}, {x2}, {y2}]")
                if is_relative:
                    print(f"    转换: [{x1_abs}, {y1_abs}, {x2_abs}, {y2_abs}]")
                continue
            
            # 确保坐标在图片范围内
            x1_abs = max(0, min(x1_abs, img_width))
            y1_abs = max(0, min(y1_abs, img_height))
            x2_abs = max(0, min(x2_abs, img_width))
            y2_abs = max(0, min(y2_abs, img_height))
            
            # 计算物体大小
            box_width_px = x2_abs - x1_abs
            box_height_px = y2_abs - y1_abs
            
            # 选择颜色
            current_color = colors[idx % len(colors)]
            
            # 绘制边界框（使用绝对坐标）
            draw.rectangle(
                [(x1_abs, y1_abs), (x2_abs, y2_abs)],
                outline=current_color,
                width=box_width
            )
            
            # 绘制标签
            if show_label:
                # 计算标签背景大小
                # 使用 textbbox 来获取文本边界框
                try:
                    bbox = draw.textbbox((0, 0), obj_name, font=font)
                    text_width = bbox[2] - bbox[0]
                    text_height = bbox[3] - bbox[1]
                except:
                    # 如果 textbbox 不可用，使用估算
                    text_width = len(obj_name) * font_size * 0.6
                    text_height = font_size
                
                # 绘制标签背景
                label_y = max(y1_abs - text_height - 8, 0)
                draw.rectangle(
                    [(x1_abs, label_y), (x1_abs + text_width + 10, label_y + text_height + 8)],
                    fill=current_color
                )
                
                # 绘制标签文字
                draw.text(
                    (x1_abs + 5, label_y + 4),
                    obj_name,
                    fill=(255, 255, 255),  # 白色文字
                    font=font
                )
            
            valid_boxes += 1
            
            # 显示详细信息
            if is_relative:
                print(f"✓ {obj_name}:")
                print(f"   归一化坐标: [{x1:.3f}, {y1:.3f}, {x2:.3f}, {y2:.3f}]")
                print(f"   反归一化后: [{x1_abs}, {y1_abs}, {x2_abs}, {y2_abs}]")
                print(f"   物体尺寸: {box_width_px} x {box_height_px} px")
                print(f"   边框颜色: RGB{current_color}\n")
            else:
                print(f"✓ {obj_name}: [{x1_abs}, {y1_abs}, {x2_abs}, {y2_abs}] ({box_width_px}x{box_height_px}px) - RGB{current_color}")
            
        except Exception as e:
            print(f"❌ 绘制 '{obj_name}' 时出错: {e}")
            continue
    
    # 保存结果
    try:
        # 确保输出目录存在
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        
        image.save(output_path, quality=95)
        
        print(f"\n✅ 可视化完成！")
        print(f"📊 统计信息:")
        print(f"   - 总物体数: {total_objects}")
        print(f"   - 成功绘制: {valid_boxes}")
        print(f"   - 失败数量: {total_objects - valid_boxes}")
        print(f"📁 输出文件: {output_path}")
        print(f"📏 文件大小: {os.path.getsize(output_path) / 1024:.2f} KB\n")
        
        return output_path
        
    except Exception as e:
        print(f"❌ 保存图片失败: {e}")
        return None
