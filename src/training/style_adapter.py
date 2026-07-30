"""
风格适配模块
用于将分析得到的风格特征应用到AI模型生成中
"""

import json
import random
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class StyleProfile:
    """风格画像数据类"""
    message_length_distribution: Dict[str, Any] = field(default_factory=dict)
    emotion_tendency: Dict[str, int] = field(default_factory=dict)
    question_frequency: float = 0.0
    frequent_phrases: List[tuple] = field(default_factory=list)
    modal_particles_usage: Dict[str, int] = field(default_factory=dict)
    emoji_usage: Dict[str, Any] = field(default_factory=dict)
    punctuation_patterns: Dict[str, Any] = field(default_factory=dict)
    total_messages: int = 0


class StyleAdapter:
    """风格适配器"""
    
    def __init__(self, style_profile: StyleProfile):
        """
        初始化风格适配器
        
        Args:
            style_profile: 风格画像
        """
        self.style_profile = style_profile
        self._prepare_style_parameters()
    
    def _prepare_style_parameters(self):
        """准备风格参数"""
        # 计算各种风格特征的概率
        self._calculate_emotion_probabilities()
        self._calculate_phrase_probabilities()
        self._calculate_particle_probabilities()
        self._calculate_emoji_probabilities()
        self._calculate_punctuation_probabilities()
    
    def _calculate_emotion_probabilities(self):
        """计算情绪概率"""
        emotion_counts = self.style_profile.emotion_tendency
        total_emotions = sum(emotion_counts.values())
        
        if total_emotions > 0:
            self.emotion_probabilities = {
                emotion: count / total_emotions 
                for emotion, count in emotion_counts.items()
            }
        else:
            self.emotion_probabilities = {}
    
    def _calculate_phrase_probabilities(self):
        """计算短语使用概率"""
        frequent_phrases = self.style_profile.frequent_phrases
        total_occurrences = sum(count for _, count in frequent_phrases)
        
        if total_occurrences > 0:
            self.phrase_probabilities = {
                phrase: count / total_occurrences 
                for phrase, count in frequent_phrases
            }
        else:
            self.phrase_probabilities = {}
    
    def _calculate_particle_probabilities(self):
        """计算语气词使用概率"""
        particle_counts = self.style_profile.modal_particles_usage
        total_particles = sum(particle_counts.values())
        
        if total_particles > 0:
            self.particle_probabilities = {
                particle: count / total_particles 
                for particle, count in particle_counts.items()
            }
        else:
            self.particle_probabilities = {}
    
    def _calculate_emoji_probabilities(self):
        """计算表情符号使用概率"""
        emoji_data = self.style_profile.emoji_usage.get('most_common_emojis', [])
        total_emojis = sum(count for _, count in emoji_data)
        
        if total_emojis > 0:
            self.emoji_probabilities = {
                emoji: count / total_emojis 
                for emoji, count in emoji_data
            }
        else:
            self.emoji_probabilities = {}
    
    def _calculate_punctuation_probabilities(self):
        """计算标点符号使用概率"""
        punctuation_data = self.style_profile.punctuation_patterns.get('most_common_punctuations', {})
        total_punctuations = sum(punctuation_data.values())
        
        if total_punctuations > 0:
            self.punctuation_probabilities = {
                punct: count / total_punctuations 
                for punct, count in punctuation_data.items()
            }
        else:
            self.punctuation_probabilities = {}
    
    def adjust_message_length(self, message: str) -> str:
        """
        调整消息长度以匹配目标风格
        
        Args:
            message: 原始消息
            
        Returns:
            调整后的消息
        """
        length_dist = self.style_profile.message_length_distribution
        if not length_dist:
            return message
            
        mean_length = length_dist.get('mean_length', len(message))
        
        # 如果消息太长，截断
        if len(message) > mean_length * 1.5:
            # 保留前mean_length个字符
            message = message[:int(mean_length)]
        # 如果消息太短，考虑添加内容
        elif len(message) < mean_length * 0.5:
            # 添加语气词或表情符号
            message = self._enhance_short_message(message)
            
        return message
    
    def _enhance_short_message(self, message: str) -> str:
        """
        增强短消息，使其更符合风格
        
        Args:
            message: 短消息
            
        Returns:
            增强后的消息
        """
        # 随机添加语气词
        if self.particle_probabilities and random.random() < 0.3:
            particle = random.choices(
                list(self.particle_probabilities.keys()),
                list(self.particle_probabilities.values())
            )[0]
            message += particle
            
        # 随机添加表情符号
        if self.emoji_probabilities and random.random() < 0.2:
            emoji = random.choices(
                list(self.emoji_probabilities.keys()),
                list(self.emoji_probabilities.values())
            )[0]
            message += emoji
            
        return message
    
    def adjust_emotion_tendency(self, message: str) -> str:
        """
        调整情绪倾向以匹配目标风格
        
        Args:
            message: 原始消息
            
        Returns:
            调整后的情绪化消息
        """
        if not self.emotion_probabilities:
            return message
            
        # 根据情绪倾向调整消息
        emotion = random.choices(
            list(self.emotion_probabilities.keys()),
            list(self.emotion_probabilities.values())
        )[0]
        
        # 根据情绪类型调整消息
        if emotion == 'positive' and random.random() < 0.2:
            positive_expressions = ['😊', '😄', '👍', '太好了', '很棒']
            message += random.choice(positive_expressions)
        elif emotion == 'negative' and random.random() < 0.2:
            negative_expressions = ['😔', '😞', '👎', '不太好', '有点难过']
            message += random.choice(negative_expressions)
            
        return message
    
    def insert_frequent_phrases(self, message: str) -> str:
        """
        插入高频短语以匹配目标风格
        
        Args:
            message: 原始消息
            
        Returns:
            插入高频短语后的消息
        """
        if not self.phrase_probabilities:
            return message
            
        # 随机决定是否插入短语
        if random.random() < 0.3:
            phrase = random.choices(
                list(self.phrase_probabilities.keys()),
                list(self.phrase_probabilities.values())
            )[0]
            
            # 随机选择插入位置
            insert_pos = random.randint(0, len(message))
            message = message[:insert_pos] + phrase + message[insert_pos:]
            
        return message
    
    def adjust_punctuation(self, message: str) -> str:
        """
        调整标点符号使用以匹配目标风格
        
        Args:
            message: 原始消息
            
        Returns:
            调整标点后的消息
        """
        if not self.punctuation_probabilities:
            return message
            
        # 随机替换或添加标点符号
        if random.random() < 0.4:
            punctuation = random.choices(
                list(self.punctuation_probabilities.keys()),
                list(self.punctuation_probabilities.values())
            )[0]
            
            # 随机选择位置添加标点
            insert_pos = random.randint(0, len(message))
            message = message[:insert_pos] + punctuation + message[insert_pos:]
            
        return message
    
    def add_emojis(self, message: str) -> str:
        """
        添加表情符号以匹配目标风格
        
        Args:
            message: 原始消息
            
        Returns:
            添加表情符号后的消息
        """
        if not self.emoji_probabilities:
            return message
            
        # 根据使用频率决定是否添加表情符号
        emoji_usage_rate = self.style_profile.emoji_usage.get('emoji_usage_rate', 0)
        
        if random.random() < emoji_usage_rate:
            emoji = random.choices(
                list(self.emoji_probabilities.keys()),
                list(self.emoji_probabilities.values())
            )[0]
            
            # 随机选择添加位置（开头、结尾或中间）
            position = random.choice(['start', 'end', 'middle'])
            if position == 'start':
                message = emoji + message
            elif position == 'end':
                message = message + emoji
            else:
                insert_pos = random.randint(0, len(message))
                message = message[:insert_pos] + emoji + message[insert_pos:]
                
        return message
    
    def apply_style(self, message: str) -> str:
        """
        应用完整风格调整
        
        Args:
            message: 原始消息
            
        Returns:
            风格化后的消息
        """
        # 依次应用各种风格调整
        message = self.adjust_message_length(message)
        message = self.insert_frequent_phrases(message)
        message = self.adjust_emotion_tendency(message)
        message = self.add_emojis(message)
        message = self.adjust_punctuation(message)
        
        return message
    
    def get_style_prompt(self) -> str:
        """
        生成风格提示词，用于指导模型生成
        
        Returns:
            风格提示词
        """
        prompt_parts = []
        
        # 添加情绪倾向提示
        if self.emotion_probabilities:
            dominant_emotion = max(self.emotion_probabilities.items(), key=lambda x: x[1])[0]
            emotion_prompts = {
                'positive': '积极乐观的',
                'negative': '略带忧郁的',
                'neutral': '平和理性的'
            }
            if dominant_emotion in emotion_prompts:
                prompt_parts.append(emotion_prompts[dominant_emotion])
        
        # 添加长度偏好提示
        length_dist = self.style_profile.message_length_distribution
        if length_dist:
            mean_length = length_dist.get('mean_length', 0)
            if mean_length < 20:
                prompt_parts.append('简洁明了的')
            elif mean_length > 50:
                prompt_parts.append('详细丰富的')
            else:
                prompt_parts.append('适度长度的')
        
        # 添加语气词使用提示
        if self.particle_probabilities:
            prompt_parts.append('带有语气词的')
            
        # 添加表情符号使用提示
        emoji_usage_rate = self.style_profile.emoji_usage.get('emoji_usage_rate', 0)
        if emoji_usage_rate > 0.5:
            prompt_parts.append('经常使用表情符号的')
        elif emoji_usage_rate > 0.2:
            prompt_parts.append('偶尔使用表情符号的')
            
        # 添加疑问句使用提示
        question_freq = self.style_profile.question_frequency
        if question_freq > 0.3:
            prompt_parts.append('喜欢提问的')
        elif question_freq < 0.1:
            prompt_parts.append('较少提问的')
            
        if prompt_parts:
            return "请以" + "、".join(prompt_parts) + "方式回复"
        else:
            return ""


class StyleController:
    """风格控制器，用于管理风格适配过程"""
    
    def __init__(self):
        self.current_style_adapter: Optional[StyleAdapter] = None
    
    def load_style_profile(self, profile_path: str) -> StyleProfile:
        """
        从文件加载风格画像
        
        Args:
            profile_path: 风格画像文件路径
            
        Returns:
            风格画像对象
        """
        try:
            with open(profile_path, 'r', encoding='utf-8') as f:
                profile_data = json.load(f)
                
            # 转换为StyleProfile对象
            style_profile = StyleProfile(
                message_length_distribution=profile_data.get('message_length_distribution', {}),
                emotion_tendency=profile_data.get('emotion_tendency', {}),
                question_frequency=profile_data.get('question_frequency', 0.0),
                frequent_phrases=[tuple(item) for item in profile_data.get('frequent_phrases', [])],
                modal_particles_usage=profile_data.get('modal_particles_usage', {}),
                emoji_usage=profile_data.get('emoji_usage', {}),
                punctuation_patterns=profile_data.get('punctuation_patterns', {}),
                total_messages=profile_data.get('total_messages', 0)
            )
            
            return style_profile
            
        except Exception as e:
            logger.error(f"加载风格画像时出错: {e}")
            raise
    
    def set_style_profile(self, style_profile: StyleProfile):
        """
        设置当前风格画像
        
        Args:
            style_profile: 风格画像对象
        """
        self.current_style_adapter = StyleAdapter(style_profile)
    
    def process_response(self, response: str) -> str:
        """
        处理模型响应，应用风格调整
        
        Args:
            response: 模型原始响应
            
        Returns:
            风格化后的响应
        """
        if self.current_style_adapter:
            return self.current_style_adapter.apply_style(response)
        else:
            return response
    
    def get_style_prompt(self) -> str:
        """
        获取当前风格提示词
        
        Returns:
            风格提示词
        """
        if self.current_style_adapter:
            return self.current_style_adapter.get_style_prompt()
        else:
            return ""


if __name__ == "__main__":
    # 测试代码
    # 创建示例风格画像
    sample_profile = StyleProfile(
        message_length_distribution={
            'mean_length': 25.5,
            'min_length': 1,
            'max_length': 100
        },
        emotion_tendency={
            'positive': 60,
            'neutral': 30,
            'negative': 10
        },
        question_frequency=0.25,
        frequent_phrases=[('你好', 15), ('哈哈', 12), ('真的吗', 8)],
        modal_particles_usage={
            '呀': 20,
            '呢': 15,
            '啊': 10
        },
        emoji_usage={
            'emoji_usage_rate': 0.6,
            'most_common_emojis': [('😊', 25), ('😂', 18), ('😍', 12)]
        },
        punctuation_patterns={
            'most_common_punctuations': {'！': 30, '？': 20, '～': 15}
        },
        total_messages=1000
    )
    
    # 创建风格适配器
    adapter = StyleAdapter(sample_profile)
    
    # 测试风格应用
    test_message = "今天天气不错"
    styled_message = adapter.apply_style(test_message)
    print(f"原始消息: {test_message}")
    print(f"风格化后: {styled_message}")
    
    # 测试风格提示词生成
    style_prompt = adapter.get_style_prompt()
    print(f"风格提示词: {style_prompt}")
    
    # 测试风格控制器
    controller = StyleController()
    controller.set_style_profile(sample_profile)
    
    response = "我觉得我们可以去公园走走"
    processed_response = controller.process_response(response)
    print(f"处理后的响应: {processed_response}")
    
    style_prompt = controller.get_style_prompt()
    print(f"控制器生成的风格提示词: {style_prompt}")