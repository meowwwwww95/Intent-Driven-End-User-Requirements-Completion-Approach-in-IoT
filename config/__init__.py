#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置模块

该模块包含了意图转换器的所有配置文件
"""

# 版本信息
__version__ = "1.0.0"
__author__ = "SmartHome Team"

from .deepseek_config import (
    get_default_client,
    get_chat_model,
    get_reasoning_model,
    get_provider_name,
    get_default_config,
    create_custom_client,
    validate_api_key,
    update_default_config,
    is_model_supported,
    supports_stream_options,
    get_json_response_format_mode,
)

# AI客户端模块
from .ai_client import (
    AIClient,
    get_default_ai_client,
    stream_completion,
    simple_completion,
    completion_with_retry,
    create_message,
    create_user_message,
    create_assistant_message,
    create_system_message
)


from .quality_template_config import (
    get_quality_template_spec,
    print_quality_template
)
# 配置模块列表
__all__ = [
    # DeepSeek配置
    "get_default_client",
    "get_chat_model",
    "get_reasoning_model", 
    "get_provider_name",
    "get_default_config",
    "create_custom_client",
    "validate_api_key",
    "update_default_config",
    "is_model_supported",
    "supports_stream_options",
    "get_json_response_format_mode",
    
    # AI客户端
    "AIClient",
    "get_default_ai_client",
    "stream_completion",
    "simple_completion",
    "completion_with_retry",
    "create_message",
    "create_user_message",
    "create_assistant_message",
    "create_system_message",
    
]
