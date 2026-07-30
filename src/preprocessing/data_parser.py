"""
数据解析模块
用于解析不同格式的历史聊天记录
"""

import json
import re
from typing import List, Dict, Any
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ChatDataParser:
    """聊天数据解析器"""
    
    def __init__(self):
        pass
    
    def parse_text_chat(self, file_path: str) -> List[Dict[str, Any]]:
        """
        解析纯文本格式的聊天记录
        假设格式为: [时间] 发送者: 消息内容
        
        Args:
            file_path: 文件路径
            
        Returns:
            解析后的聊天记录列表
        """
        chat_records = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    
                    # 匹配 [时间] 发送者: 消息内容 格式
                    pattern = r'\[(.*?)\]\s*(.*?):\s*(.*)'
                    match = re.match(pattern, line)
                    
                    if match:
                        timestamp, sender, message = match.groups()
                        chat_records.append({
                            'timestamp': timestamp,
                            'sender': sender,
                            'message': message,
                            'line_num': line_num
                        })
                    else:
                        # 如果不匹配标准格式，作为未知格式处理
                        chat_records.append({
                            'timestamp': None,
                            'sender': 'unknown',
                            'message': line,
                            'line_num': line_num
                        })
                        
        except Exception as e:
            logger.error(f"解析文本聊天记录时出错: {e}")
            raise
            
        return chat_records
    
    def parse_json_chat(self, file_path: str) -> List[Dict[str, Any]]:
        """
        解析JSON格式的聊天记录
        
        Args:
            file_path: 文件路径
            
        Returns:
            解析后的聊天记录列表
        """
        chat_records = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            # 支持两种JSON格式
            if isinstance(data, list):
                # 格式1: 直接是消息数组
                chat_records = data
            elif isinstance(data, dict) and 'messages' in data:
                # 格式2: 包含messages字段的对象
                chat_records = data['messages']
            else:
                raise ValueError("不支持的JSON格式")
                
        except Exception as e:
            logger.error(f"解析JSON聊天记录时出错: {e}")
            raise
            
        return chat_records
    
    def parse_chat_data(self, file_path: str, format_type: str = 'auto') -> List[Dict[str, Any]]:
        """
        自动解析聊天记录
        
        Args:
            file_path: 文件路径
            format_type: 格式类型 ('text', 'json', 'auto')
            
        Returns:
            解析后的聊天记录列表
        """
        if format_type == 'auto':
            # 根据文件扩展名自动判断格式
            if file_path.endswith('.json'):
                format_type = 'json'
            else:
                format_type = 'text'
        
        if format_type == 'text':
            return self.parse_text_chat(file_path)
        elif format_type == 'json':
            return self.parse_json_chat(file_path)
        else:
            raise ValueError(f"不支持的格式类型: {format_type}")


if __name__ == "__main__":
    # 测试代码
    parser = ChatDataParser()
    
    # 示例使用
    # records = parser.parse_chat_data("data/raw/sample.txt", "text")
    # records = parser.parse_chat_data("data/raw/sample.json", "json")
    pass