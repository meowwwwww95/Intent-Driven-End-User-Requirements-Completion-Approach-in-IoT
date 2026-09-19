from typing import Union, List
from config.ai_client import get_default_ai_client, simple_completion, create_system_message, create_user_message

def _convert_one(text: str) -> str:
    """
    使用AI将文本中的具体设备和动作转换为抽象的功能类型和规范化动作。
    """
    if not text:
        return ""
    
    system_prompt = f"""
# 任务
你是一个智能家居意图理解专家。你的任务是将自然语言或特定格式文本中的“具体设备”和“操作”转换为抽象的“功能类型(FunctionType)”和“标准化操作”。

# 转换规则
1. **设备抽象**：识别文本中的设备名称，将其替换为对应的【功能类型设备】。
   - 优先关注文本里实际使用的能力/功能语义，其次再参考设备名称本身。
   - 如果设备名称与能力语义不完全一致，必须优先依据能力/功能判断抽象结果。
   - 如果设备有多个功能类型，请根据上下文推断最合适的一个。例如，空调可以同时为空气温度制冷设备和空气温度制热设备，那么如果使用的是制冷功能，就应该将其替换为空气温度制冷设备。
   - 格式：将具体的设备名替换为功能类型设备名。

2. **动作标准化**：
   - "打开"类（如 turn_on, start, play, open） -> 统一转换为 "打开()"
   - "关闭"类（如 turn_off, stop, close） -> 统一转换为 "关闭()"
   - "读取/状态"类（如 reading, get_status） -> 统一转换为 "获取读数()"
   - 其他能力 -> 保持语义，统一添加 "()" 后缀（如果还没有）。

3. **格式保持**：
   - 保持原文本的句法结构，仅替换名词（设备）和动词（操作）。
   - 处理 "设备.能力" 格式 -> "功能类型.标准化能力()"
   - 处理 "能力 设备" 格式 -> "标准化能力() 功能类型"

4. **冲突处理**：
   - 如果设备名称暗示的类别与能力名称暗示的功能不一致，优先采用能力名称表达的功能。
   - 例如某个设备名看起来像温度传感器，但当前使用的是 `get_humidity()` 这类能力时，应优先按“湿度相关功能”理解。

# 示例
输入: "IF temperature_sensor.reading() == 16 THEN turn_on() Light"
输出: "IF 温度感知设备.获取读数() == 16 THEN 打开() 光照加亮设备"

# 限制
1. 请转换用户提供的文本。直接输出转换结果，不要包含解释。
"""

    messages = [
        create_system_message(system_prompt),
        create_user_message(f"请转换以下文本：\n{text}")
    ]

    try:
        # 使用较低的temperature以保证确定性
        response = simple_completion(messages, temperature=0.1)
        return response.strip()
    except Exception as e:
        # 如果AI调用失败，记录错误并返回原文本
        print(f"AI转换失败: {e}")
        return text

def convert_specific_devices_to_function_types(text: Union[str, List[str]]) -> Union[str, List[str]]:
    with get_default_ai_client().usage_scope("SYS2IRP.convert_specific_devices_to_function_types", scope_type="tool"):
        if isinstance(text, str):
            return _convert_one(text)
        if isinstance(text, list):
            # 可以考虑并行处理或者批量处理，这里简单地逐个处理
            return [_convert_one(t) for t in text]
        return text
