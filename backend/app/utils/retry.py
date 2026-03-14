"""
APICallRetry mechanism
用于ProcessLLM等外部APICall的Retry逻辑
"""

import time
import random
import functools
from typing import Callable, Any, Optional, Type, Tuple
from ..utils.logger import get_logger

logger = get_logger('mirofish.retry')


def retry_with_backoff(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable[[Exception, int], None]] = None
):
    """
    带指数退避的Retry decorator
    
    Args:
        max_retries: 最大Retry次数
        initial_delay: 初始延迟（秒）
        max_delay: 最大延迟（秒）
        backoff_factor: 退避因子
        jitter: Whether添加随机抖动
        exceptions: 需要Retry的异常Type
        on_retry: Retry时的回调Function (exception, retry_count)
    
    Usage:
        @retry_with_backoff(max_retries=3)
        def call_llm_api():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            delay = initial_delay
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                    
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == max_retries:
                        logger.error(f"Function {func.__name__} 在 {max_retries} 次Retry后仍Failed: {str(e)}")
                        raise
                    
                    # 计算延迟
                    current_delay = min(delay, max_delay)
                    if jitter:
                        current_delay = current_delay * (0.5 + random.random())
                    
                    logger.warning(
                        f"Function {func.__name__} 第 {attempt + 1} 次尝试Failed: {str(e)}, "
                        f"{current_delay:.1f}秒后Retry..."
                    )
                    
                    if on_retry:
                        on_retry(e, attempt + 1)
                    
                    time.sleep(current_delay)
                    delay *= backoff_factor
            
            raise last_exception
        
        return wrapper
    return decorator


def retry_with_backoff_async(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable[[Exception, int], None]] = None
):
    """
    异步Version的Retry decorator
    """
    import asyncio
    
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            delay = initial_delay
            
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                    
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == max_retries:
                        logger.error(f"异步Function {func.__name__} 在 {max_retries} 次Retry后仍Failed: {str(e)}")
                        raise
                    
                    current_delay = min(delay, max_delay)
                    if jitter:
                        current_delay = current_delay * (0.5 + random.random())
                    
                    logger.warning(
                        f"异步Function {func.__name__} 第 {attempt + 1} 次尝试Failed: {str(e)}, "
                        f"{current_delay:.1f}秒后Retry..."
                    )
                    
                    if on_retry:
                        on_retry(e, attempt + 1)
                    
                    await asyncio.sleep(current_delay)
                    delay *= backoff_factor
            
            raise last_exception
        
        return wrapper
    return decorator


class RetryableAPIClient:
    """
    可Retry的API客户端封装
    """
    
    def __init__(
        self,
        max_retries: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 30.0,
        backoff_factor: float = 2.0
    ):
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor
    
    def call_with_retry(
        self,
        func: Callable,
        *args,
        exceptions: Tuple[Type[Exception], ...] = (Exception,),
        **kwargs
    ) -> Any:
        """
        ExecuteFunctionCall并在Failed时Retry
        
        Args:
            func: 要Call的Function
            *args: FunctionParameters
            exceptions: 需要Retry的异常Type
            **kwargs: Function关键字Parameters
            
        Returns:
            FunctionReturn value
        """
        last_exception = None
        delay = self.initial_delay
        
        for attempt in range(self.max_retries + 1):
            try:
                return func(*args, **kwargs)
                
            except exceptions as e:
                last_exception = e
                
                if attempt == self.max_retries:
                    logger.error(f"APICall在 {self.max_retries} 次Retry后仍Failed: {str(e)}")
                    raise
                
                current_delay = min(delay, self.max_delay)
                current_delay = current_delay * (0.5 + random.random())
                
                logger.warning(
                    f"APICall第 {attempt + 1} 次尝试Failed: {str(e)}, "
                    f"{current_delay:.1f}秒后Retry..."
                )
                
                time.sleep(current_delay)
                delay *= self.backoff_factor
        
        raise last_exception
    
    def call_batch_with_retry(
        self,
        items: list,
        process_func: Callable,
        exceptions: Tuple[Type[Exception], ...] = (Exception,),
        continue_on_failure: bool = True
    ) -> Tuple[list, list]:
        """
        批量Call并对每个Failed项单独Retry
        
        Args:
            items: 要Process的ProjectList
            process_func: ProcessFunction，Receive单个item作为Parameters
            exceptions: 需要Retry的异常Type
            continue_on_failure: 单项Failed后Whether继续Process其他项
            
        Returns:
            (SuccessResultList, Failed项List)
        """
        results = []
        failures = []
        
        for idx, item in enumerate(items):
            try:
                result = self.call_with_retry(
                    process_func,
                    item,
                    exceptions=exceptions
                )
                results.append(result)
                
            except Exception as e:
                logger.error(f"Process第 {idx + 1} 项Failed: {str(e)}")
                failures.append({
                    "index": idx,
                    "item": item,
                    "error": str(e)
                })
                
                if not continue_on_failure:
                    raise
        
        return results, failures

