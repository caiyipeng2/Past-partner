"""
数据预处理模块
用于清洗和预处理聊天记录数据，为模型训练做准备
"""

import re
import logging
from typing import List, Dict, Any, Tuple, Optional
from collections import Counter
import numpy as np

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ChatPreprocessor:
    """聊天数据预处理器"""
    
    def __init__(self):
        # 表情符号模式
        self.emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"  # emoticons
            "\U0001F300-\U0001F5FF"  # symbols & pictographs
            "\U0001F680-\U0001F6FF"  # transport & map symbols
            "\U0001F1E0-\U0001F1FF"  # flags (iOS)
            "\u2600-\u26FF\u2700-\u27BF"  # Miscellaneous Symbols and Dingbats
            "]+", 
            flags=re.UNICODE
        )
        
        # 中文标点符号
        self.chinese_punctuation = '，。！？；：""''（）【】《》'
        
        # 英文标点符号
        self.english_punctuation = ',.!?;:"\'\"()\[\]<>'
        
    def clean_message(self, message: str) -> str:
        """
        清洗单条消息
        
        Args:
            message: 原始消息
            
        Returns:
            清洗后的消息
        """
        if not isinstance(message, str):
            return ""
        
        # 去除首尾空白字符
        message = message.strip()
        
        # 去除多余的空白字符
        message = re.sub(r'\s+', ' ', message)
        
        return message
    
    def extract_emojis(self, message: str) -> List[str]:
        """
        提取消息中的表情符号
        
        Args:
            message: 消息内容
            
        Returns:
            表情符号列表
        """
        return self.emoji_pattern.findall(message)
    
    def extract_punctuation_patterns(self, message: str) -> Dict[str, int]:
        """
        提取消息中的标点符号使用模式
        
        Args:
            message: 消息内容
            
        Returns:
            标点符号统计字典
        """
        punctuation_counts = {}
        
        # 统计中文标点
        for char in self.chinese_punctuation:
            count = message.count(char)
            if count > 0:
                punctuation_counts[char] = count
                
        # 统计英文标点
        for char in self.english_punctuation:
            count = message.count(char)
            if count > 0:
                punctuation_counts[char] = count
                
        return punctuation_counts
    
    def segment_chinese_text(self, text: str) -> List[str]:
        """
        对中文文本进行分词（简单实现，实际使用时可替换为jieba等专业分词工具）
        
        Args:
            text: 中文文本
            
        Returns:
            分词结果列表（这里简化为字符级别）
        """
        # 简单按字符分割，实际应用中应使用jieba等专业分词工具
        return list(text)
    
    def extract_features(self, message: str) -> Dict[str, Any]:
        """
        提取单条消息的特征
        
        Args:
            message: 消息内容
            
        Returns:
            特征字典
        """
        features = {}
        
        # 基础特征
        features['length'] = len(message)
        features['char_count'] = len(message)
        features['word_count'] = len(message.split())
        
        # 简化的分词（字符级别）
        chinese_words = self.segment_chinese_text(message)
        features['chinese_word_count'] = len(chinese_words)
        
        # 表情符号
        emojis = self.extract_emojis(message)
        features['emoji_count'] = len(emojis)
        features['emojis'] = emojis
        
        # 标点符号
        punctuations = self.extract_punctuation_patterns(message)
        features['punctuation_count'] = sum(punctuations.values())
        features['punctuations'] = punctuations
        
        # 是否以特定标点结尾
        if message:
            last_char = message[-1]
            features['ends_with_question'] = 1 if last_char in '？?' else 0
            features['ends_with_exclamation'] = 1 if last_char in '！!' else 0
            features['ends_with_period'] = 1 if last_char in '。.' else 0
            
        return features
    
    def build_vocabulary(self, messages: List[str], min_freq: int = 2) -> Dict[str, int]:
        """
        构建词汇表
        
        Args:
            messages: 消息列表
            min_freq: 最小词频
            
        Returns:
            词汇表字典
        """
        word_counter = Counter()
        
        for message in messages:
            # 字符级别的"分词"
            words = self.segment_chinese_text(message)
            word_counter.update(words)
            
            # 英文分词
            english_words = re.findall(r'[a-zA-Z]+', message)
            word_counter.update(english_words)
        
        # 过滤低频词
        vocabulary = {
            word: idx for idx, (word, freq) in enumerate(word_counter.items()) 
            if freq >= min_freq
        }
        
        return vocabulary
    
    def preprocess_conversation(self, chat_records: List[Dict[str, Any]], 
                              target_sender: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        预处理整个对话记录
        
        Args:
            chat_records: 聊天记录列表
            target_sender: 目标发送者（如果只关注特定人的消息）
            
        Returns:
            预处理后的数据
        """
        processed_data = []
        
        for record in chat_records:
            # 如果指定了目标发送者，只处理该发送者的消息
            if target_sender and record.get('sender') != target_sender:
                continue
                
            message = record.get('message', '')
            if not message:
                continue
                
            # 清洗消息
            cleaned_message = self.clean_message(message)
            if not cleaned_message:
                continue
                
            # 提取特征
            features = self.extract_features(cleaned_message)
            
            # 构建处理后的记录
            processed_record = {
                'original': record,
                'cleaned_message': cleaned_message,
                'features': features
            }
            
            processed_data.append(processed_record)
            
        return processed_data
    
    def create_training_pairs(self, chat_records: List[Dict[str, Any]], 
                            context_window: int = 5) -> List[Tuple[List[str], str]]:
        """
        创建训练对（上下文，回复）
        
        Args:
            chat_records: 聊天记录列表
            context_window: 上下文窗口大小
            
        Returns:
            训练对列表
        """
        training_pairs = []
        
        # 只提取消息内容
        messages = [record.get('message', '') for record in chat_records 
                   if record.get('message')]
        
        # 创建训练对
        for i in range(len(messages)):
            # 上下文（前面的几条消息）
            start_idx = max(0, i - context_window)
            context = messages[start_idx:i]
            
            # 回复（当前消息）
            response = messages[i]
            
            # 只有当上下文非空时才添加到训练对中
            if context:
                training_pairs.append((context, response))
                
        return training_pairs


if __name__ == "__main__":
    # 测试代码
    preprocessor = ChatPreprocessor()
    
    # 示例消息
    test_message = "你好呀！今天天气真不错😊，我们去公园散步怎么样？"
    
    # 清洗消息
    cleaned = preprocessor.clean_message(test_message)
    print(f"原始消息: {test_message}")
    print(f"清洗后: {cleaned}")
    
    # 提取特征
    features = preprocessor.extract_features(test_message)
    print(f"特征: {features}")
    
    # 测试训练对创建
    sample_records = [
        {'message': '在干嘛呢？'},
        {'message': '在看书'},
        {'message': '看什么书？'},
        {'message': '一本关于AI的小说'},
        {'message': '听起来很有趣！'},
        {'message': '是的，你要不要也看看？'}
    ]
    
    pairs = preprocessor.create_training_pairs(sample_records, context_window=3)
    print(f"\n训练对数量: {len(pairs)}")
    for i, (context, response) in enumerate(pairs[:3]):  # 只显示前3个
        print(f"训练对 {i+1}:")
        print(f"  上下文: {context}")
        print(f"  回复: {response}")