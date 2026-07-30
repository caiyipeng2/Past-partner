"""
聊天引擎模块
整合上下文管理、风格适配和模型推理功能
"""

import logging
from typing import Optional, Dict, Any
from src.inference.context_manager import ContextManager, ContextualChatbot
from src.training.style_adapter import StyleController

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ChatEngine:
    """聊天引擎"""
    
    def __init__(self, max_history_turns: int = 5):
        """
        初始化聊天引擎
        
        Args:
            max_history_turns: 最大历史轮次数量
        """
        # 初始化上下文管理器
        self.context_manager = ContextManager(max_history_turns=max_history_turns)
        
        # 初始化风格控制器
        self.style_controller = StyleController()
        
        # 初始化上下文聊天机器人
        self.chatbot = ContextualChatbot(self.context_manager)
        
        # 当前对话ID
        self.current_conversation_id: Optional[str] = None
        
        # 模型推理器（概念性实现）
        self.model_inference = None
    
    def set_style_profile(self, style_profile):
        """
        设置风格画像
        
        Args:
            style_profile: 风格画像对象
        """
        self.style_controller.set_style_profile(style_profile)
        logger.info("已设置风格画像")
    
    def load_style_profile_from_file(self, profile_path: str):
        """
        从文件加载风格画像
        
        Args:
            profile_path: 风格画像文件路径
        """
        style_profile = self.style_controller.load_style_profile(profile_path)
        self.set_style_profile(style_profile)
        logger.info(f"已从 {profile_path} 加载风格画像")
    
    def start_new_conversation(self) -> str:
        """
        开始新对话
        
        Returns:
            对话ID
        """
        self.current_conversation_id = self.chatbot.start_new_conversation()
        logger.info(f"开始新对话: {self.current_conversation_id}")
        return self.current_conversation_id
    
    def set_active_conversation(self, conversation_id: str) -> bool:
        """
        设置活动对话
        
        Args:
            conversation_id: 对话ID
            
        Returns:
            是否成功设置
        """
        success = self.context_manager.set_active_conversation(conversation_id)
        if success:
            self.current_conversation_id = conversation_id
            logger.info(f"已设置活动对话: {conversation_id}")
        else:
            logger.warning(f"设置活动对话失败: {conversation_id}")
        return success
    
    def chat(self, user_input: str, conversation_id: Optional[str] = None) -> str:
        """
        进行对话
        
        Args:
            user_input: 用户输入
            conversation_id: 对话ID，如果为None则使用当前活动对话
            
        Returns:
            助手回复
        """
        if conversation_id is None:
            conversation_id = self.current_conversation_id
            
        if conversation_id is None:
            # 如果没有活动对话，创建一个新的
            conversation_id = self.start_new_conversation()
        
        # 使用上下文聊天机器人处理对话
        assistant_reply = self.chatbot.chat(user_input, conversation_id)
        
        # 应用风格调整
        styled_reply = self.style_controller.process_response(assistant_reply)
        
        return styled_reply
    
    def get_context_as_text(self, conversation_id: Optional[str] = None, 
                          max_turns: Optional[int] = None) -> str:
        """
        获取对话上下文文本
        
        Args:
            conversation_id: 对话ID，如果为None则使用当前活动对话
            max_turns: 最大轮次数量
            
        Returns:
            格式化的对话上下文文本
        """
        return self.context_manager.get_context_as_text(conversation_id, max_turns)
    
    def clear_conversation(self, conversation_id: Optional[str] = None):
        """
        清除对话历史
        
        Args:
            conversation_id: 对话ID，如果为None则清除当前活动对话
        """
        self.context_manager.clear_conversation(conversation_id)
        logger.info("已清除对话历史")
    
    def list_conversations(self) -> list:
        """
        列出所有对话
        
        Returns:
            对话列表信息
        """
        return self.context_manager.list_conversations()
    
    def save_conversation(self, conversation_id: str, file_path: str):
        """
        保存对话到文件
        
        Args:
            conversation_id: 对话ID
            file_path: 文件路径
        """
        self.context_manager.save_conversation(conversation_id, file_path)
        logger.info(f"对话已保存到: {file_path}")
    
    def load_conversation(self, file_path: str) -> str:
        """
        从文件加载对话
        
        Args:
            file_path: 文件路径
            
        Returns:
            对话ID
        """
        conversation_id = self.context_manager.load_conversation(file_path)
        logger.info(f"对话已从 {file_path} 加载")
        return conversation_id
    
    def get_style_prompt(self) -> str:
        """
        获取风格提示词
        
        Returns:
            风格提示词
        """
        return self.style_controller.get_style_prompt()
    
    def integrate_with_model(self, model_inference):
        """
        集成模型推理器
        
        Args:
            model_inference: 模型推理器对象
        """
        self.model_inference = model_inference
        logger.info("已集成模型推理器")
    
    def generate_response_with_model(self, user_input: str, 
                                   conversation_id: Optional[str] = None) -> str:
        """
        使用集成的模型生成回复
        
        Args:
            user_input: 用户输入
            conversation_id: 对话ID
            
        Returns:
            助手回复
        """
        if self.model_inference is None:
            raise RuntimeError("未集成模型推理器")
        
        if conversation_id is None:
            conversation_id = self.current_conversation_id
            
        if conversation_id is None:
            conversation_id = self.start_new_conversation()
        
        # 添加用户消息到上下文
        self.context_manager.add_user_message(user_input, conversation_id)
        
        # 获取上下文
        context_text = self.get_context_as_text(conversation_id)
        
        # 获取风格提示词
        style_prompt = self.get_style_prompt()
        
        # 构造模型输入
        model_input = self._construct_model_input(user_input, context_text, style_prompt)
        
        # 使用模型生成回复
        raw_response = self.model_inference.generate(model_input)
        
        # 应用风格调整
        styled_response = self.style_controller.process_response(raw_response)
        
        # 添加助手消息到上下文
        self.context_manager.add_assistant_message(styled_response, conversation_id)
        
        return styled_response
    
    def _construct_model_input(self, user_input: str, context_text: str, style_prompt: str) -> str:
        """
        构造模型输入
        
        Args:
            user_input: 用户输入
            context_text: 上下文文本
            style_prompt: 风格提示词
            
        Returns:
            模型输入文本
        """
        # 构造提示词模板
        prompt_template = """你是一个个性化的AI助手，请根据以下要求进行对话：

{style_prompt}

对话历史：
{context}

用户: {user_input}
助手:"""
        
        return prompt_template.format(
            style_prompt=style_prompt if style_prompt else "请以自然友好的方式进行对话",
            context=context_text if context_text else "无历史对话",
            user_input=user_input
        )


# 概念性模型推理器
class ConceptualModelInference:
    """概念性模型推理器"""
    
    def __init__(self):
        pass
    
    def generate(self, prompt: str) -> str:
        """
        生成回复（概念性实现）
        
        Args:
            prompt: 提示词
            
        Returns:
            生成的回复
        """
        # 这里应该集成实际的LLM模型
        # 目前只是一个简单的模拟实现
        
        if "天气" in prompt:
            return "今天天气很好呢！阳光明媚，适合出去走走😊"
        elif "书" in prompt:
            return "书籍是人类进步的阶梯，多读书总是好的📚"
        elif "电影" in prompt:
            return "最近有什么好看的电影吗？我听说《AI爱情故事》很不错🎬"
        else:
            return "谢谢你和我聊天！有什么我可以帮助你的吗？"


if __name__ == "__main__":
    # 测试代码
    # 创建聊天引擎
    engine = ChatEngine(max_history_turns=3)
    
    # 集成概念性模型推理器
    model_inference = ConceptualModelInference()
    engine.integrate_with_model(model_inference)
    
    # 开始新对话
    conv_id = engine.start_new_conversation()
    print(f"开始对话: {conv_id}")
    
    # 进行几轮对话
    responses = []
    
    user_inputs = [
        "你好，今天天气怎么样？",
        "我最近在看一本书，讲的是人工智能的发展史。",
        "你觉得AI未来会发展到什么程度？",
        "那你认为AI会有感情吗？"
    ]
    
    for user_input in user_inputs:
        response = engine.chat(user_input)
        responses.append(response)
        print(f"用户: {user_input}")
        print(f"助手: {response}\n")
    
    # 查看对话历史
    context_text = engine.get_context_as_text()
    print("完整对话历史:")
    print(context_text)
    
    # 测试使用模型生成回复
    print("\n使用模型生成回复测试:")
    model_response = engine.generate_response_with_model("我们聊聊电影吧")
    print(f"模型回复: {model_response}")