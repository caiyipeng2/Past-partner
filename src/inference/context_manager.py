"""
上下文管理模块
用于管理多轮对话的上下文记忆和引用机制
"""

import json
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from collections import deque
import hashlib
from datetime import datetime

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class DialogTurn:
    """对话轮次数据类"""
    turn_id: str
    timestamp: str
    role: str  # user 或 assistant
    content: str
    metadata: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class ConversationContext:
    """对话上下文数据类"""
    conversation_id: str
    turns: List[DialogTurn]
    created_at: str
    updated_at: str
    metadata: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class ContextManager:
    """上下文管理器"""
    
    def __init__(self, max_history_turns: int = 5):
        """
        初始化上下文管理器
        
        Args:
            max_history_turns: 最大历史轮次数量
        """
        self.max_history_turns = max_history_turns
        self.conversations: Dict[str, ConversationContext] = {}
        self.active_conversation_id: Optional[str] = None
    
    def create_conversation(self, conversation_id: Optional[str] = None) -> str:
        """
        创建新的对话
        
        Args:
            conversation_id: 对话ID，如果为None则自动生成
            
        Returns:
            对话ID
        """
        if conversation_id is None:
            # 生成唯一的对话ID
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            conversation_id = f"conv_{timestamp}_{hashlib.md5(str(datetime.now().microsecond).encode()).hexdigest()[:8]}"
        
        # 创建新的对话上下文
        context = ConversationContext(
            conversation_id=conversation_id,
            turns=[],
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat()
        )
        
        self.conversations[conversation_id] = context
        self.active_conversation_id = conversation_id
        
        logger.info(f"创建新对话: {conversation_id}")
        return conversation_id
    
    def get_conversation(self, conversation_id: str) -> Optional[ConversationContext]:
        """
        获取对话上下文
        
        Args:
            conversation_id: 对话ID
            
        Returns:
            对话上下文对象或None
        """
        return self.conversations.get(conversation_id)
    
    def set_active_conversation(self, conversation_id: str) -> bool:
        """
        设置活动对话
        
        Args:
            conversation_id: 对话ID
            
        Returns:
            是否成功设置
        """
        if conversation_id in self.conversations:
            self.active_conversation_id = conversation_id
            return True
        return False
    
    def add_user_message(self, content: str, conversation_id: Optional[str] = None) -> str:
        """
        添加用户消息
        
        Args:
            content: 消息内容
            conversation_id: 对话ID，如果为None则使用当前活动对话
            
        Returns:
            轮次ID
        """
        if conversation_id is None:
            conversation_id = self.active_conversation_id
            
        if conversation_id is None:
            raise ValueError("没有指定对话ID且没有活动对话")
            
        context = self.conversations.get(conversation_id)
        if context is None:
            raise ValueError(f"对话 {conversation_id} 不存在")
        
        # 生成轮次ID
        turn_id = f"turn_{len(context.turns) + 1}_{hashlib.md5(content.encode()).hexdigest()[:8]}"
        
        # 创建用户轮次
        turn = DialogTurn(
            turn_id=turn_id,
            timestamp=datetime.now().isoformat(),
            role="user",
            content=content
        )
        
        # 添加到对话中
        context.turns.append(turn)
        context.updated_at = datetime.now().isoformat()
        
        # 控制历史长度
        self._trim_history(context)
        
        logger.debug(f"添加用户消息到对话 {conversation_id}: {content[:50]}...")
        return turn_id
    
    def add_assistant_message(self, content: str, conversation_id: Optional[str] = None) -> str:
        """
        添加助手消息
        
        Args:
            content: 消息内容
            conversation_id: 对话ID，如果为None则使用当前活动对话
            
        Returns:
            轮次ID
        """
        if conversation_id is None:
            conversation_id = self.active_conversation_id
            
        if conversation_id is None:
            raise ValueError("没有指定对话ID且没有活动对话")
            
        context = self.conversations.get(conversation_id)
        if context is None:
            raise ValueError(f"对话 {conversation_id} 不存在")
        
        # 生成轮次ID
        turn_id = f"turn_{len(context.turns) + 1}_{hashlib.md5(content.encode()).hexdigest()[:8]}"
        
        # 创建助手轮次
        turn = DialogTurn(
            turn_id=turn_id,
            timestamp=datetime.now().isoformat(),
            role="assistant",
            content=content
        )
        
        # 添加到对话中
        context.turns.append(turn)
        context.updated_at = datetime.now().isoformat()
        
        # 控制历史长度
        self._trim_history(context)
        
        logger.debug(f"添加助手消息到对话 {conversation_id}: {content[:50]}...")
        return turn_id
    
    def _trim_history(self, context: ConversationContext):
        """
        修剪历史记录，保持最大轮次数量
        
        Args:
            context: 对话上下文
        """
        if len(context.turns) > self.max_history_turns * 2:  # 每轮包含用户和助手两条消息
            # 保留最近的max_history_turns轮对话
            context.turns = context.turns[-(self.max_history_turns * 2):]
    
    def get_recent_context(self, conversation_id: Optional[str] = None, 
                          max_turns: Optional[int] = None) -> List[DialogTurn]:
        """
        获取最近的对话上下文
        
        Args:
            conversation_id: 对话ID，如果为None则使用当前活动对话
            max_turns: 最大轮次数量，如果为None则使用默认值
            
        Returns:
            最近的对话轮次列表
        """
        if conversation_id is None:
            conversation_id = self.active_conversation_id
            
        if conversation_id is None:
            return []
            
        context = self.conversations.get(conversation_id)
        if context is None:
            return []
        
        if max_turns is None:
            max_turns = self.max_history_turns
            
        # 返回最近的max_turns轮对话（每轮包含用户和助手两条消息）
        return context.turns[-(max_turns * 2):] if context.turns else []
    
    def get_context_as_text(self, conversation_id: Optional[str] = None, 
                           max_turns: Optional[int] = None) -> str:
        """
        将对话上下文转换为文本格式
        
        Args:
            conversation_id: 对话ID，如果为None则使用当前活动对话
            max_turns: 最大轮次数量，如果为None则使用默认值
            
        Returns:
            格式化的对话上下文文本
        """
        recent_context = self.get_recent_context(conversation_id, max_turns)
        
        if not recent_context:
            return ""
        
        context_lines = []
        for turn in recent_context:
            role_name = "用户" if turn.role == "user" else "助手"
            context_lines.append(f"{role_name}: {turn.content}")
            
        return "\n".join(context_lines)
    
    def clear_conversation(self, conversation_id: Optional[str] = None):
        """
        清除对话历史
        
        Args:
            conversation_id: 对话ID，如果为None则清除当前活动对话
        """
        if conversation_id is None:
            conversation_id = self.active_conversation_id
            
        if conversation_id is None:
            return
            
        context = self.conversations.get(conversation_id)
        if context:
            context.turns.clear()
            context.updated_at = datetime.now().isoformat()
            logger.info(f"已清除对话 {conversation_id} 的历史记录")
    
    def delete_conversation(self, conversation_id: str):
        """
        删除对话
        
        Args:
            conversation_id: 对话ID
        """
        if conversation_id in self.conversations:
            del self.conversations[conversation_id]
            if self.active_conversation_id == conversation_id:
                self.active_conversation_id = None
            logger.info(f"已删除对话: {conversation_id}")
    
    def list_conversations(self) -> List[Dict[str, Any]]:
        """
        列出所有对话
        
        Returns:
            对话列表信息
        """
        conversations_info = []
        for conv_id, context in self.conversations.items():
            conversations_info.append({
                "conversation_id": conv_id,
                "turn_count": len(context.turns),
                "created_at": context.created_at,
                "updated_at": context.updated_at
            })
        return conversations_info
    
    def save_conversation(self, conversation_id: str, file_path: str):
        """
        保存对话到文件
        
        Args:
            conversation_id: 对话ID
            file_path: 文件路径
        """
        context = self.conversations.get(conversation_id)
        if context is None:
            raise ValueError(f"对话 {conversation_id} 不存在")
        
        # 转换为可序列化的字典
        context_dict = {
            "conversation_id": context.conversation_id,
            "turns": [
                {
                    "turn_id": turn.turn_id,
                    "timestamp": turn.timestamp,
                    "role": turn.role,
                    "content": turn.content,
                    "metadata": turn.metadata
                }
                for turn in context.turns
            ],
            "created_at": context.created_at,
            "updated_at": context.updated_at,
            "metadata": context.metadata
        }
        
        # 保存到文件
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(context_dict, f, ensure_ascii=False, indent=2)
        
        logger.info(f"对话 {conversation_id} 已保存到: {file_path}")
    
    def load_conversation(self, file_path: str) -> str:
        """
        从文件加载对话
        
        Args:
            file_path: 文件路径
            
        Returns:
            对话ID
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            context_dict = json.load(f)
        
        # 转换回对象
        turns = [
            DialogTurn(
                turn_id=turn_data["turn_id"],
                timestamp=turn_data["timestamp"],
                role=turn_data["role"],
                content=turn_data["content"],
                metadata=turn_data.get("metadata", {})
            )
            for turn_data in context_dict["turns"]
        ]
        
        context = ConversationContext(
            conversation_id=context_dict["conversation_id"],
            turns=turns,
            created_at=context_dict["created_at"],
            updated_at=context_dict["updated_at"],
            metadata=context_dict.get("metadata", {})
        )
        
        self.conversations[context.conversation_id] = context
        logger.info(f"对话 {context.conversation_id} 已从 {file_path} 加载")
        
        return context.conversation_id


class ContextualChatbot:
    """基于上下文的聊天机器人"""
    
    def __init__(self, context_manager: ContextManager):
        """
        初始化上下文聊天机器人
        
        Args:
            context_manager: 上下文管理器
        """
        self.context_manager = context_manager
    
    def start_new_conversation(self) -> str:
        """
        开始新对话
        
        Returns:
            对话ID
        """
        return self.context_manager.create_conversation()
    
    def chat(self, user_input: str, conversation_id: Optional[str] = None) -> str:
        """
        进行对话
        
        Args:
            user_input: 用户输入
            conversation_id: 对话ID
            
        Returns:
            助手回复
        """
        # 添加用户消息
        self.context_manager.add_user_message(user_input, conversation_id)
        
        # 获取上下文
        context_text = self.context_manager.get_context_as_text(conversation_id)
        
        # 生成回复（这里是一个模拟实现）
        assistant_reply = self._generate_response(user_input, context_text)
        
        # 添加助手消息
        self.context_manager.add_assistant_message(assistant_reply, conversation_id)
        
        return assistant_reply
    
    def _generate_response(self, user_input: str, context_text: str) -> str:
        """
        生成回复（模拟实现）
        
        Args:
            user_input: 用户输入
            context_text: 上下文文本
            
        Returns:
            助手回复
        """
        # 这里应该集成实际的LLM模型
        # 目前只是一个简单的模拟实现
        
        if context_text:
            return f"关于你说的'{user_input}'，我记得我们之前聊过相关话题。基于我们的对话历史，我认为..."
        else:
            return f"你说了'{user_input}'，这是一个很有趣的话题。我们可以深入讨论一下。"


if __name__ == "__main__":
    # 测试代码
    # 创建上下文管理器
    cm = ContextManager(max_history_turns=3)
    
    # 创建新对话
    conv_id = cm.create_conversation()
    print(f"创建对话: {conv_id}")
    
    # 添加一些对话轮次
    cm.add_user_message("你好，今天过得怎么样？", conv_id)
    cm.add_assistant_message("你好！我很好，谢谢你的关心。你今天过得如何？", conv_id)
    
    cm.add_user_message("还不错，刚刚看完一本书。", conv_id)
    cm.add_assistant_message("听起来很不错！是什么书呢？", conv_id)
    
    cm.add_user_message("是一本关于人工智能的小说。", conv_id)
    cm.add_assistant_message("哇，那一定很有趣！能跟我讲讲书中的情节吗？", conv_id)
    
    cm.add_user_message("书里讲述了一个AI逐渐产生自我意识的故事。", conv_id)
    cm.add_assistant_message("这听起来很有哲学意味呢！你觉得AI真的会产生自我意识吗？", conv_id)
    
    # 获取上下文
    context = cm.get_recent_context(conv_id)
    print(f"\n最近的对话轮次数量: {len(context)}")
    
    for turn in context:
        print(f"{turn.role}: {turn.content}")
    
    # 获取格式化的上下文文本
    context_text = cm.get_context_as_text(conv_id)
    print(f"\n格式化的上下文:\n{context_text}")
    
    # 测试上下文聊天机器人
    bot = ContextualChatbot(cm)
    reply = bot.chat("你觉得人工智能会超越人类吗？", conv_id)
    print(f"\n机器人回复: {reply}")
    
    # 查看更新后的上下文
    updated_context_text = cm.get_context_as_text(conv_id)
    print(f"\n更新后的上下文:\n{updated_context_text}")