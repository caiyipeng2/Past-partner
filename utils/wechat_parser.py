#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
微信数据库解析工具
用于解析微信导出的数据库文件
"""

import sqlite3
import json
import os
from typing import List, Dict, Any
from datetime import datetime
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class WeChatDBParser:
    """微信数据库解析器"""
    
    def __init__(self):
        pass
    
    def parse_wechat_db(self, db_path: str) -> List[Dict[str, Any]]:
        """
        解析微信数据库文件
        
        Args:
            db_path: 微信数据库文件路径
            
        Returns:
            解析后的聊天记录列表
        """
        try:
            # 检查文件是否存在
            if not os.path.exists(db_path):
                raise FileNotFoundError(f"数据库文件不存在: {db_path}")
            
            # 连接数据库
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # 尝试不同的表结构（不同版本的微信可能有所不同）
            chat_records = []
            
            # 尝试常见的表名
            table_names = ['message', 'ChatInfo', 'chat', 'messages']
            
            for table_name in table_names:
                try:
                    # 查询表结构
                    cursor.execute(f"PRAGMA table_info({table_name})")
                    columns = cursor.fetchall()
                    
                    if columns:
                        # 如果找到了表，尝试查询数据
                        chat_records = self._extract_messages(cursor, table_name, columns)
                        if chat_records:
                            break
                except sqlite3.Error:
                    continue
            
            conn.close()
            
            logger.info(f"从微信数据库中提取到 {len(chat_records)} 条消息")
            return chat_records
            
        except Exception as e:
            logger.error(f"解析微信数据库失败: {e}")
            return []
    
    def _extract_messages(self, cursor, table_name: str, columns: List[tuple]) -> List[Dict[str, Any]]:
        """
        从指定表中提取消息
        
        Args:
            cursor: 数据库游标
            table_name: 表名
            columns: 列信息
            
        Returns:
            消息列表
        """
        # 常见的列名映射
        column_mapping = {
            'talker': ['talker', 'username', 'contact', 'sender'],
            'content': ['content', 'message', 'msg', 'text'],
            'createTime': ['createTime', 'timestamp', 'time', 'date'],
            'type': ['type', 'msgType', 'messageType']
        }
        
        # 查找实际的列名
        actual_columns = {}
        column_names = [col[1] for col in columns]
        
        for logical_name, possible_names in column_mapping.items():
            for possible_name in possible_names:
                if possible_name in column_names:
                    actual_columns[logical_name] = possible_name
                    break
        
        # 如果没有找到必要的列，返回空列表
        if 'content' not in actual_columns:
            return []
        
        # 构建查询语句
        select_columns = []
        for logical_name, actual_name in actual_columns.items():
            select_columns.append(f"{actual_name} AS {logical_name}")
        
        query = f"SELECT {', '.join(select_columns)} FROM {table_name}"
        
        try:
            cursor.execute(query)
            rows = cursor.fetchall()
            
            # 转换为标准格式
            messages = []
            for row in rows:
                message = {}
                for i, (logical_name, _) in enumerate(actual_columns.items()):
                    value = row[i]
                    if logical_name == 'createTime' and isinstance(value, int):
                        # 转换时间戳
                        try:
                            dt = datetime.fromtimestamp(value/1000 if value > 10000000000 else value)
                            message['timestamp'] = dt.isoformat()
                        except:
                            message['timestamp'] = str(value)
                    else:
                        message[logical_name] = value
                
                # 添加到消息列表
                messages.append(message)
            
            return messages
            
        except sqlite3.Error as e:
            logger.error(f"查询数据库失败: {e}")
            return []
    
    def parse_wechat_folder(self, folder_path: str) -> List[Dict[str, Any]]:
        """
        解析微信导出文件夹中的所有数据库文件
        
        Args:
            folder_path: 微信导出文件夹路径
            
        Returns:
            所有解析后的聊天记录列表
        """
        all_messages = []
        
        try:
            # 遍历文件夹中的所有文件
            for root, dirs, files in os.walk(folder_path):
                for file in files:
                    if file.endswith('.db') or file.endswith('.sqlite'):
                        db_path = os.path.join(root, file)
                        logger.info(f"正在解析数据库文件: {db_path}")
                        
                        messages = self.parse_wechat_db(db_path)
                        all_messages.extend(messages)
            
            logger.info(f"从文件夹中总共提取到 {len(all_messages)} 条消息")
            return all_messages
            
        except Exception as e:
            logger.error(f"解析微信文件夹失败: {e}")
            return all_messages
    
    def save_parsed_data(self, messages: List[Dict[str, Any]], output_path: str) -> bool:
        """
        保存解析后的数据到文件
        
        Args:
            messages: 解析后的消息列表
            output_path: 输出文件路径
            
        Returns:
            是否保存成功
        """
        try:
            # 确保输出目录存在
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            # 保存为JSON格式
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(messages, f, ensure_ascii=False, indent=2)
            
            logger.info(f"解析后的数据已保存到: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"保存解析数据失败: {e}")
            return False

def main():
    """主函数 - 使用示例"""
    parser = WeChatDBParser()
    
    # 创建示例数据库文件（仅用于演示）
    sample_db_path = "data/sample_wechat.db"
    os.makedirs(os.path.dirname(sample_db_path), exist_ok=True)
    
    # 注意：在实际使用中，这里会解析真实的微信数据库文件
    # 由于无法创建真实的微信数据库，我们只是演示解析器的结构
    
    print("微信数据库解析工具")
    print("支持解析微信导出的数据库文件和文件夹")
    print("使用方法:")
    print("  parser = WeChatDBParser()")
    print("  messages = parser.parse_wechat_db('path/to/wechat.db')")
    print("  messages = parser.parse_wechat_folder('path/to/wechat/folder')")

if __name__ == "__main__":
    main()