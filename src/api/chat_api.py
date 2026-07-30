"""
聊天服务API模块
提供RESTful API接口供前端或其他服务调用
"""

import logging
import uuid
import json
import os
from typing import Dict, Any, List, Optional
from datetime import datetime

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 模拟的聊天引擎（实际使用时需要导入真实的实现）
class MockChatEngine:
    """模拟聊天引擎"""
    
    def __init__(self):
        self.conversations = {}
    
    def start_new_conversation(self) -> str:
        """开始新对话"""
        conv_id = str(uuid.uuid4())
        self.conversations[conv_id] = {
            "id": conv_id,
            "created_at": datetime.now().isoformat(),
            "messages": []
        }
        return conv_id
    
    def chat(self, user_input: str, conversation_id: str) -> str:
        """进行对话"""
        if conversation_id not in self.conversations:
            raise ValueError("对话不存在")
        
        # 模拟AI回复
        ai_response = f"关于'{user_input}'，我觉得这是一个很有趣的话题呢😊"
        
        # 记录对话历史
        self.conversations[conversation_id]["messages"].append({
            "role": "user",
            "content": user_input,
            "timestamp": datetime.now().isoformat()
        })
        
        self.conversations[conversation_id]["messages"].append({
            "role": "assistant",
            "content": ai_response,
            "timestamp": datetime.now().isoformat()
        })
        
        return ai_response
    
    def get_conversation_history(self, conversation_id: str) -> List[Dict[str, Any]]:
        """获取对话历史"""
        if conversation_id not in self.conversations:
            raise ValueError("对话不存在")
        return self.conversations[conversation_id]["messages"]

# 创建模拟聊天引擎实例
chat_engine = MockChatEngine()

# 数据模型类
class ChatRequest:
    """聊天请求模型"""
    def __init__(self, message: str, conversation_id: Optional[str] = None):
        self.message = message
        self.conversation_id = conversation_id

class ChatResponse:
    """聊天响应模型"""
    def __init__(self, conversation_id: str, user_message: str, ai_response: str, timestamp: str):
        self.conversation_id = conversation_id
        self.user_message = user_message
        self.ai_response = ai_response
        self.timestamp = timestamp

class ConversationHistoryResponse:
    """对话历史响应模型"""
    def __init__(self, conversation_id: str, messages: List[Dict[str, Any]], created_at: str):
        self.conversation_id = conversation_id
        self.messages = messages
        self.created_at = created_at

class NewConversationResponse:
    """新对话响应模型"""
    def __init__(self, conversation_id: str, created_at: str):
        self.conversation_id = conversation_id
        self.created_at = created_at

class UploadChatDataRequest:
    """上传聊天数据请求模型"""
    def __init__(self, data: List[Dict[str, Any]], user_id: str = "default_user"):
        self.data = data
        self.user_id = user_id

class UploadChatDataResponse:
    """上传聊天数据响应模型"""
    def __init__(self, success: bool, message_count: int, message: str):
        self.success = success
        self.message_count = message_count
        self.message = message

# API服务类
class ChatAPIService:
    """聊天API服务类"""
    
    def __init__(self):
        self.chat_engine = MockChatEngine()
    
    def send_message(self, request: ChatRequest) -> ChatResponse:
        """
        发送聊天消息
        
        Args:
            request: 聊天请求对象
            
        Returns:
            ChatResponse: 聊天响应对象
        """
        try:
            # 如果没有提供对话ID，创建新的对话
            if not request.conversation_id:
                conversation_id = self.chat_engine.start_new_conversation()
            else:
                conversation_id = request.conversation_id
            
            # 获取AI回复
            ai_response = self.chat_engine.chat(request.message, conversation_id)
            
            # 返回响应
            response = ChatResponse(
                conversation_id=conversation_id,
                user_message=request.message,
                ai_response=ai_response,
                timestamp=datetime.now().isoformat()
            )
            
            logger.info(f"聊天请求处理完成: {conversation_id}")
            return response
            
        except Exception as e:
            logger.error(f"聊天请求处理失败: {e}")
            raise
    
    def create_conversation(self) -> NewConversationResponse:
        """
        创建新对话
        
        Returns:
            NewConversationResponse: 新对话响应对象
        """
        try:
            conversation_id = self.chat_engine.start_new_conversation()
            
            response = NewConversationResponse(
                conversation_id=conversation_id,
                created_at=datetime.now().isoformat()
            )
            
            logger.info(f"新对话已创建: {conversation_id}")
            return response
            
        except Exception as e:
            logger.error(f"创建新对话失败: {e}")
            raise
    
    def get_conversation_history(self, conversation_id: str) -> ConversationHistoryResponse:
        """
        获取对话历史
        
        Args:
            conversation_id: 对话ID
            
        Returns:
            ConversationHistoryResponse: 对话历史响应对象
        """
        try:
            messages = self.chat_engine.get_conversation_history(conversation_id)
            
            # 获取对话创建时间
            conversation = self.chat_engine.conversations.get(conversation_id, {})
            created_at = conversation.get("created_at", datetime.now().isoformat())
            
            response = ConversationHistoryResponse(
                conversation_id=conversation_id,
                messages=messages,
                created_at=created_at
            )
            
            logger.info(f"获取对话历史: {conversation_id}")
            return response
            
        except ValueError as e:
            logger.warning(f"对话不存在: {conversation_id}")
            raise
        except Exception as e:
            logger.error(f"获取对话历史失败: {e}")
            raise
    
    def delete_conversation(self, conversation_id: str) -> Dict[str, str]:
        """
        删除对话
        
        Args:
            conversation_id: 对话ID
            
        Returns:
            Dict: 删除结果
        """
        try:
            if conversation_id in self.chat_engine.conversations:
                del self.chat_engine.conversations[conversation_id]
                logger.info(f"对话已删除: {conversation_id}")
                return {"message": "对话删除成功"}
            else:
                logger.warning(f"尝试删除不存在的对话: {conversation_id}")
                return {"error": "对话不存在"}
                
        except Exception as e:
            logger.error(f"删除对话失败: {e}")
            return {"error": str(e)}
    
    def upload_chat_data(self, request: UploadChatDataRequest) -> UploadChatDataResponse:
        """
        上传聊天数据用于训练
        
        Args:
            request: 上传聊天数据请求对象
            
        Returns:
            UploadChatDataResponse: 上传聊天数据响应对象
        """
        try:
            # 确保数据目录存在
            data_dir = "data/uploaded"
            os.makedirs(data_dir, exist_ok=True)
            
            # 生成唯一文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"chat_data_{request.user_id}_{timestamp}.json"
            file_path = os.path.join(data_dir, filename)
            
            # 保存聊天数据
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(request.data, f, ensure_ascii=False, indent=2)
            
            message_count = len(request.data)
            logger.info(f"聊天数据已保存: {file_path}, 共 {message_count} 条消息")
            
            return UploadChatDataResponse(
                success=True,
                message_count=message_count,
                message=f"成功上传 {message_count} 条聊天记录"
            )
            
        except Exception as e:
            logger.error(f"上传聊天数据失败: {e}")
            return UploadChatDataResponse(
                success=False,
                message_count=0,
                message=f"上传失败: {str(e)}"
            )
    
    def health_check(self) -> Dict[str, Any]:
        """
        健康检查
        
        Returns:
            Dict: 健康状态信息
        """
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "service": "personalized-ai-chat-api"
        }

# 创建API服务实例
api_service = ChatAPIService()

def main():
    """主函数 - API使用示例"""
    logger.info("聊天服务API模块已加载")
    
    # 创建新对话
    new_conv_response = api_service.create_conversation()
    print(f"创建新对话: {new_conv_response.conversation_id}")
    
    # 发送消息
    chat_request = ChatRequest(
        message="你好，今天天气怎么样？",
        conversation_id=new_conv_response.conversation_id
    )
    
    chat_response = api_service.send_message(chat_request)
    print(f"用户消息: {chat_response.user_message}")
    print(f"AI回复: {chat_response.ai_response}")
    
    # 再发送一条消息
    chat_request2 = ChatRequest(
        message="你能告诉我一个笑话吗？",
        conversation_id=new_conv_response.conversation_id
    )
    
    chat_response2 = api_service.send_message(chat_request2)
    print(f"用户消息: {chat_response2.user_message}")
    print(f"AI回复: {chat_response2.ai_response}")
    
    # 获取对话历史
    history_response = api_service.get_conversation_history(new_conv_response.conversation_id)
    print(f"\n对话历史 ({len(history_response.messages)} 条消息):")
    for msg in history_response.messages:
        print(f"  {msg['role']}: {msg['content']}")
    
    # 模拟上传聊天数据
    sample_chat_data = [
        {"timestamp": "2023-01-01 10:00:00", "sender": "user1", "message": "你好，今天天气怎么样？"},
        {"timestamp": "2023-01-01 10:00:30", "sender": "assistant", "message": "你好！天气很好呢😊"},
        {"timestamp": "2023-01-01 10:01:00", "sender": "user1", "message": "我想知道你的名字"},
        {"timestamp": "2023-01-01 10:01:15", "sender": "assistant", "message": "我是你的AI助手"},
    ]
    
    upload_request = UploadChatDataRequest(data=sample_chat_data, user_id="test_user")
    upload_response = api_service.upload_chat_data(upload_request)
    print(f"\n上传聊天数据结果: {upload_response.message}")
    
    # 健康检查
    health_status = api_service.health_check()
    print(f"\n健康状态: {health_status}")

if __name__ == "__main__":
    main()
