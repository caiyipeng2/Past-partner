"""
风格分析模块
用于分析目标人物的聊天风格特征，包括语气、情绪、用词习惯等
"""

import re
from typing import List, Dict, Any, Tuple
from collections import Counter, defaultdict
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class StyleAnalyzer:
    """聊天风格分析器"""
    
    def __init__(self):
        # 情绪关键词（示例）
        self.emotion_keywords = {
            'positive': ['开心', '高兴', '喜欢', '爱', '棒', '好', '不错', '赞'],
            'negative': ['难过', '生气', '讨厌', '烦', '差', '不好', '糟糕'],
            'neutral': ['知道', '明白', '好的', '嗯', '哦']
        }
        
        # 疑问词
        self.question_words = ['什么', '怎么', '为什么', '哪里', '谁', '何时', '如何']
        
        # 语气词
        self.modal_particles = ['呀', '呢', '啊', '吧', '嘛', '啦']
        
    def analyze_message_length_distribution(self, messages: List[str]) -> Dict[str, Any]:
        """
        分析消息长度分布
        
        Args:
            messages: 消息列表
            
        Returns:
            长度分布统计
        """
        if not messages:
            return {}
            
        lengths = [len(msg) for msg in messages]
        
        return {
            'mean_length': sum(lengths) / len(lengths),
            'min_length': min(lengths),
            'max_length': max(lengths),
            'median_length': sorted(lengths)[len(lengths) // 2],
            'length_counts': dict(Counter(lengths))
        }
    
    def analyze_emotion_tendency(self, messages: List[str]) -> Dict[str, int]:
        """
        分析情绪倾向
        
        Args:
            messages: 消息列表
            
        Returns:
            情绪统计
        """
        emotion_counts = defaultdict(int)
        
        for message in messages:
            # 转换为小写并移除多余空格
            message = message.lower().strip()
            
            # 检查每种情绪的关键词
            for emotion, keywords in self.emotion_keywords.items():
                for keyword in keywords:
                    if keyword in message:
                        emotion_counts[emotion] += 1
                        
            # 如果没有匹配到任何情绪关键词，则归类为中性
            if not any(emotion in emotion_counts for emotion in self.emotion_keywords.keys()):
                emotion_counts['neutral'] += 1
                
        return dict(emotion_counts)
    
    def analyze_question_frequency(self, messages: List[str]) -> float:
        """
        分析疑问句频率
        
        Args:
            messages: 消息列表
            
        Returns:
            疑问句占比
        """
        if not messages:
            return 0.0
            
        question_count = 0
        for message in messages:
            # 检查是否以问号结尾
            if message.strip().endswith(('?', '？')):
                question_count += 1
                continue
                
            # 检查是否包含疑问词
            for question_word in self.question_words:
                if question_word in message:
                    question_count += 1
                    break
                    
        return question_count / len(messages)
    
    def extract_frequent_phrases(self, messages: List[str], min_freq: int = 2, 
                              phrase_length: int = 3) -> List[Tuple[str, int]]:
        """
        提取高频短语
        
        Args:
            messages: 消息列表
            min_freq: 最小频率
            phrase_length: 短语长度（字符数）
            
        Returns:
            高频短语列表
        """
        phrase_counter = Counter()
        
        for message in messages:
            # 提取指定长度的子串
            for i in range(len(message) - phrase_length + 1):
                phrase = message[i:i + phrase_length]
                # 过滤掉纯标点或纯数字的短语
                if re.search(r'[\u4e00-\u9fff]|[a-zA-Z]', phrase):
                    phrase_counter[phrase] += 1
                    
        # 过滤低频短语
        frequent_phrases = [
            (phrase, count) for phrase, count in phrase_counter.items() 
            if count >= min_freq
        ]
        
        # 按频率排序
        frequent_phrases.sort(key=lambda x: x[1], reverse=True)
        
        return frequent_phrases
    
    def analyze_modal_particles_usage(self, messages: List[str]) -> Dict[str, int]:
        """
        分析语气词使用情况
        
        Args:
            messages: 消息列表
            
        Returns:
            语气词使用统计
        """
        particle_counts = defaultdict(int)
        
        for message in messages:
            for particle in self.modal_particles:
                # 计算每个语气词在所有消息中出现的总次数
                particle_counts[particle] += message.count(particle)
                
        return dict(particle_counts)
    
    def analyze_emoji_usage(self, emoji_stats: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        分析表情符号使用习惯
        
        Args:
            emoji_stats: 每条消息的表情符号统计列表
            
        Returns:
            表情符号使用分析
        """
        if not emoji_stats:
            return {}
            
        total_messages = len(emoji_stats)
        messages_with_emojis = sum(1 for stat in emoji_stats if stat.get('emoji_count', 0) > 0)
        
        # 统计所有表情符号
        all_emojis = []
        for stat in emoji_stats:
            all_emojis.extend(stat.get('emojis', []))
            
        emoji_counter = Counter(all_emojis)
        
        return {
            'emoji_usage_rate': messages_with_emojis / total_messages if total_messages > 0 else 0,
            'average_emojis_per_message': len(all_emojis) / total_messages if total_messages > 0 else 0,
            'most_common_emojis': emoji_counter.most_common(10),
            'total_unique_emojis': len(emoji_counter)
        }
    
    def analyze_punctuation_patterns(self, punctuation_stats: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        分析标点符号使用模式
        
        Args:
            punctuation_stats: 每条消息的标点符号统计列表
            
        Returns:
            标点符号使用分析
        """
        if not punctuation_stats:
            return {}
            
        # 合并所有标点符号统计
        total_punctuations = defaultdict(int)
        total_punctuation_count = 0
        messages_with_punctuation = 0
        
        for stat in punctuation_stats:
            punctuations = stat.get('punctuations', {})
            if punctuations:
                messages_with_punctuation += 1
                for punct, count in punctuations.items():
                    total_punctuations[punct] += count
                    total_punctuation_count += count
                    
        return {
            'punctuation_usage_rate': messages_with_punctuation / len(punctuation_stats),
            'average_punctuations_per_message': total_punctuation_count / len(punctuation_stats),
            'most_common_punctuations': dict(sorted(
                total_punctuations.items(), 
                key=lambda x: x[1], 
                reverse=True
            )[:10])
        }
    
    def generate_style_profile(self, processed_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        生成完整的风格画像
        
        Args:
            processed_data: 预处理后的数据
            
        Returns:
            风格画像字典
        """
        if not processed_data:
            return {}
            
        # 提取消息内容
        messages = [item['cleaned_message'] for item in processed_data]
        features_list = [item['features'] for item in processed_data]
        
        # 分析各个维度
        profile = {
            'message_length_distribution': self.analyze_message_length_distribution(messages),
            'emotion_tendency': self.analyze_emotion_tendency(messages),
            'question_frequency': self.analyze_question_frequency(messages),
            'frequent_phrases': self.extract_frequent_phrases(messages),
            'modal_particles_usage': self.analyze_modal_particles_usage(messages),
            'emoji_usage': self.analyze_emoji_usage(features_list),
            'punctuation_patterns': self.analyze_punctuation_patterns(features_list),
            'total_messages': len(messages)
        }
        
        return profile


if __name__ == "__main__":
    # 测试代码
    analyzer = StyleAnalyzer()
    
    # 示例数据
    sample_messages = [
        "你好呀！今天天气真不错😊",
        "在干嘛呢？",
        "在看书📚，一本关于AI的小说",
        "听起来很有趣！我也想看看😊",
        "好呀，我发给你电子版📖",
        "谢谢啦！你真好😄"
    ]
    
    # 生成风格画像
    # 注意：这只是一个简化的示例，在实际使用中需要完整的processed_data
    print("风格分析模块测试")
    print(f"示例消息数量: {len(sample_messages)}")
    
    # 分析消息长度分布
    length_dist = analyzer.analyze_message_length_distribution(sample_messages)
    print(f"消息平均长度: {length_dist.get('mean_length', 0):.2f}")
    
    # 分析情绪倾向
    emotion_tendency = analyzer.analyze_emotion_tendency(sample_messages)
    print(f"情绪倾向: {emotion_tendency}")
    
    # 分析疑问句频率
    question_freq = analyzer.analyze_question_frequency(sample_messages)
    print(f"疑问句频率: {question_freq:.2f}")