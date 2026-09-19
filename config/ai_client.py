#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI客户端处理文件

该文件包含了与AI模型交互的核心功能，包括流式请求处理和响应解析。
用于意图转换器中的AI模型调用和响应处理。

主要功能：
- 流式完成请求处理
- AI响应解析
- 错误处理和重试机制
- 请求参数配置
"""

import time
import json
import threading
from contextlib import contextmanager
from typing import Iterator, List, Dict, Any, Optional, Tuple, Union
from openai import OpenAI

from config.deepseek_config import (
    get_default_client,
    get_chat_model,
    get_reasoning_model,
    get_json_response_format_mode,
    get_provider_name,
    supports_stream_options,
)
from config.overhead_recorder import get_overhead_recorder


class AIClient:
    """
    AI客户端类
    
    封装与AI模型的交互逻辑，提供统一的接口。
    """
    
    def __init__(self, client: Optional[OpenAI] = None):
        """
        初始化AI客户端
        
        Args:
            client (OpenAI, optional): OpenAI客户端实例，默认使用默认配置
        """
        self.client = client or get_default_client()
        self.chat_model = get_chat_model()
        self.reasoning_model = get_reasoning_model()
        self.provider = get_provider_name()
        self.json_response_format_mode = get_json_response_format_mode()
        self.supports_stream_options = supports_stream_options()
        self.reset_usage_stats()

    def _build_json_response_format(self) -> Union[Dict[str, str], str]:
        """根据提供方能力构建 JSON 输出格式参数。"""
        if self.json_response_format_mode == "string":
            return "json_object"
        return {"type": "json_object"}

    def _build_completion_request(
        self,
        messages_input: List[Dict[str, str]],
        model: str,
        stream: bool = False,
        use_json: bool = False,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        """按当前提供方能力构建 completion 请求参数。"""
        request_kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages_input,
        }

        if stream:
            request_kwargs["stream"] = True
            if self.supports_stream_options:
                request_kwargs["stream_options"] = {"include_usage": True}

        if use_json:
            request_kwargs["response_format"] = self._build_json_response_format()

        if max_tokens is not None:
            request_kwargs["max_tokens"] = max_tokens

        if temperature is not None:
            request_kwargs["temperature"] = temperature

        return request_kwargs

    @staticmethod
    def _extract_message_content(response: Any) -> str:
        """兼容标准 ChatCompletion 以及少数提供方返回的原始字符串。"""
        if isinstance(response, str):
            return response
        if isinstance(response, dict):
            choices = response.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                content = message.get("content")
                if isinstance(content, str):
                    return content
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise TypeError(f"无法从响应中提取 choices，实际类型: {type(response).__name__}")
        message = getattr(choices[0], "message", None)
        if message is None:
            raise TypeError("响应缺少 message 字段")
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text" and item.get("text"):
                        text_parts.append(str(item["text"]))
                else:
                    item_type = getattr(item, "type", None)
                    item_text = getattr(item, "text", None)
                    if item_type == "text" and item_text:
                        text_parts.append(str(item_text))
            if text_parts:
                return "".join(text_parts)
        raise TypeError(f"无法从响应中提取文本内容，实际类型: {type(content).__name__}")

    @staticmethod
    def _fill_overhead_usage(detail: Dict[str, Any], usage: Any) -> None:
        """把 usage 中的计费 Token 明细回填到开销记录（兼容对象与 dict 形态）。"""
        if usage is None:
            return

        def _get(obj: Any, key: str) -> Any:
            if isinstance(obj, dict):
                return obj.get(key)
            return getattr(obj, key, None)

        prompt_tokens = _get(usage, "prompt_tokens")
        completion_tokens = _get(usage, "completion_tokens")
        if prompt_tokens is not None:
            detail["input_tokens"] = int(prompt_tokens)
        if completion_tokens is not None:
            detail["output_tokens"] = int(completion_tokens)
        prompt_details = _get(usage, "prompt_tokens_details")
        cached_tokens = _get(prompt_details, "cached_tokens") if prompt_details is not None else None
        if cached_tokens is not None:
            detail["cached_input_tokens"] = int(cached_tokens)
        completion_details = _get(usage, "completion_tokens_details")
        reasoning_tokens = _get(completion_details, "reasoning_tokens") if completion_details is not None else None
        if reasoning_tokens is not None:
            detail["reasoning_tokens"] = int(reasoning_tokens)

    def reset_usage_stats(self) -> None:
        """重置累计 token 与按作用域统计结果。"""
        self.total_tokens = 0
        self._thread_local = threading.local()
        self._usage_lock = threading.RLock()
        self._scope_usage: Dict[str, Dict[str, Any]] = {}

    def _get_scope_stack(self) -> List[Dict[str, Any]]:
        stack = getattr(self._thread_local, "scope_stack", None)
        if stack is None:
            stack = []
            self._thread_local.scope_stack = stack
        return stack

    def _set_scope_stack(self, stack: List[Dict[str, Any]]) -> None:
        self._thread_local.scope_stack = stack

    @staticmethod
    def _merge_intervals(intervals: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        normalized: List[Tuple[float, float]] = []
        for start, end in intervals:
            start_value = float(start)
            end_value = float(end)
            if end_value < start_value:
                start_value, end_value = end_value, start_value
            normalized.append((start_value, end_value))
        if not normalized:
            return []
        normalized.sort(key=lambda item: item[0])
        merged: List[Tuple[float, float]] = [normalized[0]]
        for start_value, end_value in normalized[1:]:
            last_start, last_end = merged[-1]
            if start_value <= last_end:
                merged[-1] = (last_start, max(last_end, end_value))
            else:
                merged.append((start_value, end_value))
        return merged

    @classmethod
    def _intervals_duration(cls, intervals: List[Tuple[float, float]]) -> float:
        return sum(end_value - start_value for start_value, end_value in cls._merge_intervals(intervals))

    def _ensure_scope_usage(
        self,
        scope_key: str,
        scope_name: str,
        scope_type: str,
        parent_key: Optional[str],
        depth: int,
    ) -> Dict[str, Any]:
        if scope_key not in self._scope_usage:
            self._scope_usage[scope_key] = {
                "scope_key": scope_key,
                "scope_name": scope_name,
                "scope_type": scope_type,
                "parent_key": parent_key,
                "depth": depth,
                "tokens": 0,
                "api_calls": 0,
                "invocations": 0,
                "wall_clock_seconds": 0.0,
                "exclusive_seconds": 0.0,
                "intervals": [],
            }
        usage = self._scope_usage[scope_key]
        usage["scope_name"] = scope_name
        usage["scope_type"] = scope_type
        usage["parent_key"] = parent_key
        usage["depth"] = depth
        return usage

    def _ensure_scope_usage_from_frame(self, frame: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        scope_key = str(frame.get("key") or "")
        if not scope_key:
            return None
        return self._ensure_scope_usage(
            scope_key=scope_key,
            scope_name=str(frame.get("name") or ""),
            scope_type=str(frame.get("type") or "scope"),
            parent_key=str(frame.get("parent_key")) if frame.get("parent_key") is not None else None,
            depth=int(frame.get("depth", 0) or 0),
        )

    def _pause_exclusive_timing(self, frame: Dict[str, Any], now: float) -> None:
        if "active_start_time" not in frame:
            return
        active_start = frame.get("active_start_time")
        if active_start is None:
            return
        usage = self._ensure_scope_usage_from_frame(frame)
        if usage is None:
            frame["active_start_time"] = None
            return
        usage["exclusive_seconds"] = float(usage.get("exclusive_seconds", 0.0) or 0.0) + max(0.0, now - float(active_start))
        frame["active_start_time"] = None

    @staticmethod
    def _resume_exclusive_timing(frame: Dict[str, Any], now: float) -> None:
        if "active_start_time" not in frame:
            return
        frame["active_start_time"] = now

    @staticmethod
    def _should_pause_parent_exclusive(parent: Optional[Dict[str, Any]], child_scope_type: str) -> bool:
        if parent is None:
            return False
        parent_scope_type = str(parent.get("type") or "scope")
        return parent_scope_type == "agent" and child_scope_type == "agent"

    def _record_usage(self, tokens: int = 0, api_calls: int = 0) -> None:
        scope_stack = self._get_scope_stack()
        if not tokens and not api_calls:
            return
        with self._usage_lock:
            if tokens:
                self.total_tokens += tokens
            for frame in scope_stack:
                scope_key = str(frame.get("key") or "")
                if not scope_key:
                    continue
                usage = self._ensure_scope_usage(
                    scope_key=scope_key,
                    scope_name=str(frame.get("name") or ""),
                    scope_type=str(frame.get("type") or "scope"),
                    parent_key=str(frame.get("parent_key")) if frame.get("parent_key") is not None else None,
                    depth=int(frame.get("depth", 0) or 0),
                )
                if tokens:
                    usage["tokens"] = int(usage.get("tokens", 0) or 0) + int(tokens)
                if api_calls:
                    usage["api_calls"] = int(usage.get("api_calls", 0) or 0) + int(api_calls)

    @contextmanager
    def usage_scope(self, scope_name: str, scope_type: str = "scope") -> Iterator[None]:
        """在指定作用域内累计 token 与调用次数。支持嵌套作用域。"""
        scope = (scope_name or "").strip()
        if not scope:
            yield
            return
        normalized_scope_type = (scope_type or "scope").strip() or "scope"
        scope_stack = self._get_scope_stack()
        parent = scope_stack[-1] if scope_stack else None
        parent_key = str(parent.get("key") or "") if parent else None
        scope_key = f"{parent_key} > {scope}" if parent_key else scope
        depth = len(scope_stack)
        now = time.perf_counter()
        with self._usage_lock:
            usage = self._ensure_scope_usage(
                scope_key=scope_key,
                scope_name=scope,
                scope_type=normalized_scope_type,
                parent_key=parent_key or None,
                depth=depth,
            )
            usage["invocations"] = int(usage.get("invocations", 0) or 0) + 1
            if self._should_pause_parent_exclusive(parent, normalized_scope_type):
                self._pause_exclusive_timing(parent, now)
        frame: Dict[str, Any] = {
            "key": scope_key,
            "name": scope,
            "type": normalized_scope_type,
            "parent_key": parent_key or None,
            "depth": depth,
            "start_time": now,
            "active_start_time": now,
        }
        scope_stack.append(frame)
        try:
            yield
        finally:
            frame_index = None
            current_stack = self._get_scope_stack()
            for i in range(len(current_stack) - 1, -1, -1):
                if current_stack[i].get("key") == scope_key:
                    frame_index = i
                    break
            if frame_index is None:
                return
            frame = current_stack.pop(frame_index)
            end_time = time.perf_counter()
            start_time = float(frame.get("start_time", end_time) or end_time)
            with self._usage_lock:
                self._pause_exclusive_timing(frame, end_time)
                usage = self._ensure_scope_usage(
                    scope_key=scope_key,
                    scope_name=scope,
                    scope_type=normalized_scope_type,
                    parent_key=parent_key or None,
                    depth=depth,
                )
                intervals = list(usage.get("intervals") or [])
                intervals.append((start_time, end_time))
                usage["intervals"] = intervals
                usage["wall_clock_seconds"] = self._intervals_duration(intervals)
                parent_after_pop = current_stack[-1] if current_stack else None
                if self._should_pause_parent_exclusive(parent_after_pop, normalized_scope_type):
                    self._resume_exclusive_timing(current_stack[-1], end_time)

    def get_scope_stack_snapshot(self) -> List[Dict[str, Any]]:
        """获取当前线程的作用域快照，供子线程继承统计上下文。"""
        snapshot: List[Dict[str, Any]] = []
        for frame in self._get_scope_stack():
            snapshot.append(
                {
                    "key": str(frame.get("key") or ""),
                    "name": str(frame.get("name") or ""),
                    "type": str(frame.get("type") or "scope"),
                    "parent_key": str(frame.get("parent_key")) if frame.get("parent_key") is not None else None,
                    "depth": int(frame.get("depth", 0) or 0),
                }
            )
        return snapshot

    @contextmanager
    def inherit_scope_stack(self, scope_snapshot: Optional[List[Dict[str, Any]]] = None) -> Iterator[None]:
        """在当前线程临时继承父线程的作用域栈。"""
        previous_stack = list(self._get_scope_stack())
        inherited_stack: List[Dict[str, Any]] = []
        for frame in scope_snapshot or []:
            inherited_stack.append(
                {
                    "key": str(frame.get("key") or ""),
                    "name": str(frame.get("name") or ""),
                    "type": str(frame.get("type") or "scope"),
                    "parent_key": str(frame.get("parent_key")) if frame.get("parent_key") is not None else None,
                    "depth": int(frame.get("depth", 0) or 0),
                }
            )
        self._set_scope_stack(inherited_stack)
        try:
            yield
        finally:
            self._set_scope_stack(previous_stack)

    def get_scope_usage_summary(self) -> Dict[str, Dict[str, Any]]:
        """返回当前已累计的按作用域统计结果。"""
        summary: Dict[str, Dict[str, Any]] = {}
        with self._usage_lock:
            for scope_key, usage in self._scope_usage.items():
                summary[scope_key] = {
                    "scope_key": scope_key,
                    "scope_name": str(usage.get("scope_name") or ""),
                    "scope_type": str(usage.get("scope_type") or "scope"),
                    "parent_key": str(usage.get("parent_key")) if usage.get("parent_key") is not None else None,
                    "depth": int(usage.get("depth", 0) or 0),
                    "tokens": int(usage.get("tokens", 0) or 0),
                    "api_calls": int(usage.get("api_calls", 0) or 0),
                    "invocations": int(usage.get("invocations", 0) or 0),
                    "wall_clock_seconds": float(usage.get("wall_clock_seconds", 0.0) or 0.0),
                    "exclusive_seconds": float(usage.get("exclusive_seconds", 0.0) or 0.0),
                    "intervals": [
                        [float(start_value), float(end_value)]
                        for start_value, end_value in self._merge_intervals(list(usage.get("intervals") or []))
                    ],
                }
        return summary
    
    def stream_completion(self, messages_input: List[Dict[str, str]], reasoning: bool = False, 
                         use_json: bool = False, debug: bool = False) -> Tuple[str, str]:
        """
        流式完成函数
        
        处理AI模型的流式响应，支持推理模式和JSON格式输出。
        
        Args:
            messages_input: 输入消息列表
            reasoning (bool): 是否使用推理模式
            use_json (bool): 是否使用JSON格式输出
            debug (bool): 是否启用调试模式，打印流式输出
            
        Returns:
            Tuple[str, str]: (reasoning_content, answer_content) 推理内容和答案内容
            
        Raises:
            Exception: 当AI请求失败时抛出异常
        """
        reasoning_content = ""
        answer_content = ""
        is_answering = False
        model_name = self.reasoning_model if reasoning else self.chat_model

        with get_overhead_recorder().llm_call(model=model_name, provider=self.provider) as overhead:
            try:
                # 创建流式请求
                stream = self.client.chat.completions.create(
                    **self._build_completion_request(
                        messages_input=messages_input,
                        model=model_name,
                        stream=True,
                        use_json=use_json,
                    )
                )
                self._record_usage(api_calls=1)

                # 处理流式响应
                for chunk in stream:
                    if getattr(chunk, 'usage', None):
                        self._record_usage(tokens=chunk.usage.total_tokens)
                        self._fill_overhead_usage(overhead, chunk.usage)

                    if "model_name" not in overhead and getattr(chunk, "model", None):
                        overhead["model_name"] = chunk.model

                    if not getattr(chunk, 'choices', None):
                        continue
                    delta = chunk.choices[0].delta

                    if not getattr(delta, 'reasoning_content', None) and not getattr(delta, 'content', None):
                        continue

                    if not getattr(delta, 'reasoning_content', None) and not is_answering:
                        is_answering = True

                    # 处理推理内容
                    if getattr(delta, 'reasoning_content', None):
                        if debug:
                            print(delta.reasoning_content, end='', flush=True)
                        reasoning_content += delta.reasoning_content

                    # 处理答案内容
                    elif getattr(delta, 'content', None):
                        if debug:
                            print(delta.content, end='', flush=True)
                        answer_content += delta.content

            except Exception as e:
                raise Exception(f"AI请求失败: {e}")

        return reasoning_content, answer_content
    
    def simple_completion(self, messages_input: List[Dict[str, str]], 
                         model: Optional[str] = None, 
                         use_json: bool = False,
                         max_tokens: Optional[int] = None,
                         temperature: float = 0.3) -> str:
        """
        简单完成请求（非流式）
        
        Args:
            messages_input: 输入消息列表
            model (str, optional): 使用的模型，默认使用聊天模型
            use_json (bool): 是否使用JSON格式输出
            max_tokens (int, optional): 最大token数
            temperature (float): 温度参数
            
        Returns:
            str: AI响应内容
            
        Raises:
            Exception: 当AI请求失败时抛出异常
        """
        with get_overhead_recorder().llm_call(model=model or self.chat_model, provider=self.provider) as overhead:
            try:
                response = self.client.chat.completions.create(
                    **self._build_completion_request(
                        messages_input=messages_input,
                        model=model or self.chat_model,
                        use_json=use_json,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                )

                if hasattr(response, 'usage') and response.usage:
                    self._record_usage(tokens=response.usage.total_tokens, api_calls=1)
                    self._fill_overhead_usage(overhead, response.usage)
                else:
                    self._record_usage(api_calls=1)

                if getattr(response, "model", None):
                    overhead["model_name"] = response.model

                return self._extract_message_content(response)

            except Exception as e:
                raise Exception(f"AI请求失败: {e}")
    
    def completion_with_retry(self, messages_input: List[Dict[str, str]], 
                             max_retries: int = 3, 
                             retry_delay: float = 1.0,
                             **kwargs) -> Tuple[str, str]:
        """
        带重试机制的流式完成请求
        
        Args:
            messages_input: 输入消息列表
            max_retries (int): 最大重试次数
            retry_delay (float): 重试延迟时间（秒）
            **kwargs: 传递给stream_completion的其他参数
            
        Returns:
            Tuple[str, str]: (reasoning_content, answer_content)
            
        Raises:
            Exception: 当所有重试都失败时抛出异常
        """
        last_exception = None
        
        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    # 重试产生的调用同样计入开销，trigger_type 标记为 retry
                    with get_overhead_recorder().mark_retry():
                        return self.stream_completion(messages_input, **kwargs)
                return self.stream_completion(messages_input, **kwargs)
            except Exception as e:
                last_exception = e
                if attempt < max_retries:
                    if kwargs.get('debug', False):
                        print(f"\n请求失败，{retry_delay}秒后重试 (尝试 {attempt + 1}/{max_retries + 1}): {e}")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # 指数退避
                else:
                    break
        
        raise Exception(f"所有重试都失败了: {last_exception}")
    
    def validate_json_response(self, response: str) -> Dict[str, Any]:
        """
        验证和解析JSON响应
        
        Args:
            response (str): AI响应字符串
            
        Returns:
            Dict[str, Any]: 解析后的JSON对象
            
        Raises:
            json.JSONDecodeError: 当JSON格式无效时抛出异常
        """
        try:
            return json.loads(response)
        except json.JSONDecodeError as e:
            raise json.JSONDecodeError(f"无效的JSON响应: {e}")
    
    def set_models(self, chat_model: Optional[str] = None, reasoning_model: Optional[str] = None):
        """
        设置使用的模型
        
        Args:
            chat_model (str, optional): 聊天模型名称
            reasoning_model (str, optional): 推理模型名称
        """
        if chat_model:
            self.chat_model = chat_model
        if reasoning_model:
            self.reasoning_model = reasoning_model


# ==================== 全局客户端实例 ====================

# 创建默认AI客户端实例
_default_ai_client = AIClient()


def get_default_ai_client() -> AIClient:
    """
    获取默认AI客户端实例
    
    Returns:
        AIClient: 默认AI客户端实例
    """
    return _default_ai_client


# ==================== 便捷函数 ====================

def stream_completion(messages_input: List[Dict[str, str]], reasoning: bool = False, 
                     use_json: bool = False, debug: bool = False) -> Tuple[str, str]:
    """
    便捷的流式完成函数
    
    使用默认AI客户端进行流式完成请求。
    
    Args:
        messages_input: 输入消息列表
        reasoning (bool): 是否使用推理模式
        use_json (bool): 是否使用JSON格式输出
        debug (bool): 是否启用调试模式，打印流式输出
        
    Returns:
        Tuple[str, str]: (reasoning_content, answer_content) 推理内容和答案内容
    """
    return _default_ai_client.stream_completion(messages_input, reasoning, use_json, debug)


def simple_completion(messages_input: List[Dict[str, str]], **kwargs) -> str:
    """
    便捷的简单完成函数
    
    Args:
        messages_input: 输入消息列表
        **kwargs: 其他参数
        
    Returns:
        str: AI响应内容
    """
    return _default_ai_client.simple_completion(messages_input, **kwargs)


def completion_with_retry(messages_input: List[Dict[str, str]], **kwargs) -> Tuple[str, str]:
    """
    便捷的带重试机制的完成函数
    
    Args:
        messages_input: 输入消息列表
        **kwargs: 其他参数
        
    Returns:
        Tuple[str, str]: (reasoning_content, answer_content)
    """
    return _default_ai_client.completion_with_retry(messages_input, **kwargs)


def create_message(role: str, content: str) -> Dict[str, str]:
    """
    创建消息对象
    
    Args:
        role (str): 角色（user, assistant, system）
        content (str): 消息内容
        
    Returns:
        Dict[str, str]: 消息对象
    """
    return {"role": role, "content": content}


def create_user_message(content: str) -> Dict[str, str]:
    """
    创建用户消息
    
    Args:
        content (str): 消息内容
        
    Returns:
        Dict[str, str]: 用户消息对象
    """
    return create_message("user", content)


def create_assistant_message(content: str) -> Dict[str, str]:
    """
    创建助手消息
    
    Args:
        content (str): 消息内容
        
    Returns:
        Dict[str, str]: 助手消息对象
    """
    return create_message("assistant", content)


def create_system_message(content: str) -> Dict[str, str]:
    """
    创建系统消息
    
    Args:
        content (str): 消息内容
        
    Returns:
        Dict[str, str]: 系统消息对象
    """
    return create_message("system", content)
