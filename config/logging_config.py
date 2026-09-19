#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日志配置模块

提供统一的日志配置和管理功能，支持文件输出和控制台输出。
"""

import logging
import logging.handlers
import os
import sys
from datetime import datetime
from typing import Optional, Tuple


CASE_RUN_LOGGER_NAME = "intent2sys.case.run"
CASE_DEBUG_LOGGER_NAME = "intent2sys.case.debug"

class StreamToLogger:
    """
    将流重定向到Logger，同时保留原流的输出
    """
    def __init__(self, stream, logger, level=logging.INFO):
        self.stream = stream
        self.logger = logger
        self.level = level
        self.linebuf = ''

    def write(self, buf):
        if buf is None:
            return 0

        if not isinstance(buf, str):
            try:
                buf = str(buf)
            except Exception:
                return 0

        try:
            self.stream.write(buf)
        except Exception:
            try:
                encoding = getattr(self.stream, "encoding", None) or "utf-8"
                self.stream.buffer.write(buf.encode(encoding, errors="replace"))
            except Exception:
                pass

        self.linebuf += buf
        while "\n" in self.linebuf:
            line, self.linebuf = self.linebuf.split("\n", 1)
            if line.endswith("\r"):
                line = line[:-1]
            self.logger.log(self.level, line)
        return len(buf)

    def flush(self):
        if self.linebuf:
            line = self.linebuf
            self.linebuf = ""
            if line.endswith("\r"):
                line = line[:-1]
            self.logger.log(self.level, line)
            for handler in list(getattr(self.logger, "handlers", []) or []):
                try:
                    handler.flush()
                except Exception:
                    pass
        try:
            self.stream.flush()
        except Exception:
            pass

    def isatty(self):
        try:
            return bool(getattr(self.stream, "isatty", lambda: False)())
        except Exception:
            return False

    def __getattr__(self, name):
        return getattr(self.stream, name)

def redirect_stdout_to_logger(logger):
    """
    重定向 stdout 和 stderr 到指定的 logger
    """
    sys.stdout = StreamToLogger(sys.stdout, logger, logging.INFO)
    sys.stderr = StreamToLogger(sys.stderr, logger, logging.ERROR)


def _reset_logger(logger: logging.Logger) -> logging.Logger:
    """移除已有处理器，避免重复写入。"""
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    return logger


def setup_case_logging(out_dir: str, overwrite: bool = True) -> Tuple[logging.Logger, logging.Logger]:
    """在指定 case 输出目录下创建 run.log 与 debug.log。"""
    os.makedirs(out_dir, exist_ok=True)
    run_log_path = os.path.join(out_dir, "run.log")
    debug_log_path = os.path.join(out_dir, "debug.log")
    file_mode = "w" if overwrite else "a"

    run_logger = _reset_logger(logging.getLogger(CASE_RUN_LOGGER_NAME))
    run_logger.setLevel(logging.INFO)
    run_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    run_file_handler = logging.FileHandler(run_log_path, mode=file_mode, encoding="utf-8")
    run_file_handler.setLevel(logging.INFO)
    run_file_handler.setFormatter(run_formatter)
    run_logger.addHandler(run_file_handler)

    console_handler = logging.StreamHandler(sys.__stdout__)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    run_logger.addHandler(console_handler)

    debug_logger = _reset_logger(logging.getLogger(CASE_DEBUG_LOGGER_NAME))
    debug_logger.setLevel(logging.DEBUG)
    debug_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(threadName)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    debug_file_handler = logging.FileHandler(debug_log_path, mode=file_mode, encoding="utf-8")
    debug_file_handler.setLevel(logging.DEBUG)
    debug_file_handler.setFormatter(debug_formatter)
    debug_logger.addHandler(debug_file_handler)

    return run_logger, debug_logger


def get_run_logger() -> logging.Logger:
    """获取 case 摘要日志记录器。"""
    return logging.getLogger(CASE_RUN_LOGGER_NAME)


def get_case_debug_logger() -> logging.Logger:
    """获取 case 详细日志记录器。"""
    return logging.getLogger(CASE_DEBUG_LOGGER_NAME)

class LoggerManager:
    """日志管理器"""
    
    _loggers = {}
    _log_dir = None
    
    @classmethod
    def setup_log_directory(cls, log_dir: Optional[str] = None):
        """设置日志目录"""
        if log_dir is None:
            # 默认在项目根目录下创建logs文件夹
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            log_dir = os.path.join(current_dir, 'logs')
        
        cls._log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
    
    @classmethod
    def get_logger(cls, name: str, level: int = logging.DEBUG, console_output: bool = True) -> logging.Logger:
        """获取或创建日志记录器"""
        if name in cls._loggers:
            return cls._loggers[name]
        
        # 确保日志目录已设置
        if cls._log_dir is None:
            cls.setup_log_directory()
        
        logger = logging.getLogger(name)
        logger.setLevel(level)
        
        # 避免重复添加处理器
        if logger.handlers:
            cls._loggers[name] = logger
            return logger
        
        # 创建格式化器
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # 创建文件处理器 - 按日期分割
        today = datetime.now().strftime('%Y%m%d')
        log_file = os.path.join(cls._log_dir, f'{name}_{today}.log')
        
        # 确保日志文件所在的目录存在
        log_file_dir = os.path.dirname(log_file)
        if log_file_dir and not os.path.exists(log_file_dir):
            os.makedirs(log_file_dir, exist_ok=True)
            
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        if console_output:
            # 创建控制台处理器
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
        
        cls._loggers[name] = logger
        return logger
    
    @classmethod
    def get_debug_logger(cls, name: str) -> logging.Logger:
        """获取debug级别的日志记录器"""
        return cls.get_logger(f"{name}_debug", logging.DEBUG)
    
    @classmethod
    def get_error_logger(cls, name: str) -> logging.Logger:
        """获取error级别的日志记录器"""
        return cls.get_logger(f"{name}_error", logging.ERROR)


# 便捷函数
def get_logger(name: str, console_output: bool = True) -> logging.Logger:
    """获取日志记录器的便捷函数"""
    return LoggerManager.get_logger(name, console_output=console_output)


def get_debug_logger(name: str) -> logging.Logger:
    """获取debug日志记录器的便捷函数"""
    return LoggerManager.get_debug_logger(name)


def get_error_logger(name: str) -> logging.Logger:
    """获取error日志记录器的便捷函数"""
    return LoggerManager.get_error_logger(name)


# 初始化默认日志目录
LoggerManager.setup_log_directory()
