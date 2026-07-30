"""
隐私保护工具模块
提供数据加密、匿名化和访问控制等隐私保护功能
"""

import os
import json
import hashlib
import logging
from typing import Dict, Any, List, Optional
import base64

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 注意：此类需要安装cryptography库才能使用加密功能
# pip install cryptography


class DataEncryption:
    """数据加密类（概念性实现）"""
    
    def __init__(self, password: Optional[str] = None):
        """
        初始化数据加密器（概念性实现）
        
        Args:
            password: 加密密码，如果为None则生成随机密钥
        """
        logger.warning("DataEncryption是概念性实现，实际使用需要安装cryptography库")
        
        # 模拟密钥生成
        if password:
            # 模拟从密码派生密钥
            self.key = base64.urlsafe_b64encode(
                hashlib.sha256(password.encode()).digest()
            )
        else:
            # 模拟生成随机密钥
            self.key = base64.urlsafe_b64encode(os.urandom(32))
        
        # 模拟加密套件
        self.cipher_suite = None
    
    def encrypt_data(self, data: str) -> str:
        """
        加密数据（概念性实现）
        
        Args:
            data: 要加密的字符串数据
            
        Returns:
            加密后的数据（Base64编码）
        """
        # 模拟加密过程
        # 实际实现需要使用cryptography库
        try:
            # 简单的异或加密（仅用于演示，不安全）
            key_bytes = base64.urlsafe_b64decode(self.key)
            data_bytes = data.encode()
            
            encrypted_bytes = bytearray()
            for i, byte in enumerate(data_bytes):
                encrypted_bytes.append(byte ^ key_bytes[i % len(key_bytes)])
            
            encrypted_data = base64.urlsafe_b64encode(encrypted_bytes).decode()
            return encrypted_data
        except Exception as e:
            logger.error(f"数据加密失败: {e}")
            raise
    
    def decrypt_data(self, encrypted_data: str) -> str:
        """
        解密数据（概念性实现）
        
        Args:
            encrypted_data: 加密的数据（Base64编码）
            
        Returns:
            解密后的原始数据
        """
        # 解密过程与加密过程相同（异或运算的特性）
        try:
            key_bytes = base64.urlsafe_b64decode(self.key)
            encrypted_bytes = base64.urlsafe_b64decode(encrypted_data.encode())
            
            decrypted_bytes = bytearray()
            for i, byte in enumerate(encrypted_bytes):
                decrypted_bytes.append(byte ^ key_bytes[i % len(key_bytes)])
            
            decrypted_data = decrypted_bytes.decode()
            return decrypted_data
        except Exception as e:
            logger.error(f"数据解密失败: {e}")
            raise
    
    def get_key(self) -> str:
        """
        获取加密密钥（Base64编码）
        
        Returns:
            加密密钥
        """
        return self.key.decode()
    
    def save_key(self, key_path: str):
        """
        保存密钥到文件
        
        Args:
            key_path: 密钥文件路径
        """
        try:
            with open(key_path, 'wb') as f:
                f.write(self.key)
            logger.info(f"密钥已保存到: {key_path}")
        except Exception as e:
            logger.error(f"保存密钥失败: {e}")
            raise
    
    @classmethod
    def load_key(cls, key_path: str) -> 'DataEncryption':
        """
        从文件加载密钥并创建加密器
        
        Args:
            key_path: 密钥文件路径
            
        Returns:
            DataEncryption实例
        """
        try:
            with open(key_path, 'rb') as f:
                key = f.read()
            
            instance = cls.__new__(cls)
            instance.key = key
            instance.cipher_suite = None
            return instance
        except Exception as e:
            logger.error(f"加载密钥失败: {e}")
            raise


class DataAnonymizer:
    """数据匿名化类"""
    
    def __init__(self):
        self.hash_salt = "personalized_ai_anonymization_salt"
    
    def anonymize_text(self, text: str, method: str = "hash") -> str:
        """
        匿名化文本
        
        Args:
            text: 要匿名化的文本
            method: 匿名化方法 ("hash", "mask", "replace")
            
        Returns:
            匿名化后的文本
        """
        if method == "hash":
            return self._hash_text(text)
        elif method == "mask":
            return self._mask_text(text)
        elif method == "replace":
            return self._replace_text(text)
        else:
            raise ValueError(f"不支持的匿名化方法: {method}")
    
    def _hash_text(self, text: str) -> str:
        """
        使用哈希算法匿名化文本
        
        Args:
            text: 原始文本
            
        Returns:
            哈希值
        """
        hash_input = (self.hash_salt + text).encode()
        return hashlib.sha256(hash_input).hexdigest()[:16]  # 取前16位
    
    def _mask_text(self, text: str) -> str:
        """
        使用掩码匿名化文本
        
        Args:
            text: 原始文本
            
        Returns:
            掩码后的文本
        """
        if len(text) <= 2:
            return "*" * len(text)
        
        # 保留首尾字符，中间用*替代
        return text[0] + "*" * (len(text) - 2) + text[-1]
    
    def _replace_text(self, text: str) -> str:
        """
        使用占位符替换文本
        
        Args:
            text: 原始文本
            
        Returns:
            替换后的文本
        """
        return "[ANONYMIZED]"
    
    def anonymize_conversation(self, conversation: List[Dict[str, Any]], 
                            fields_to_anonymize: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        匿名化对话数据
        
        Args:
            conversation: 对话数据列表
            fields_to_anonymize: 需要匿名化的字段列表
            
        Returns:
            匿名化后的对话数据
        """
        if fields_to_anonymize is None:
            fields_to_anonymize = ["sender", "message"]
        
        anonymized_conversation = []
        
        for record in conversation:
            anonymized_record = record.copy()
            
            for field in fields_to_anonymize:
                if field in anonymized_record:
                    anonymized_record[field] = self.anonymize_text(
                        str(anonymized_record[field]), 
                        method="hash"
                    )
            
            anonymized_conversation.append(anonymized_record)
        
        return anonymized_conversation


class AccessControl:
    """访问控制类"""
    
    def __init__(self, authorized_users: Optional[List[str]] = None):
        """
        初始化访问控制器
        
        Args:
            authorized_users: 授权用户列表
        """
        self.authorized_users = authorized_users or []
        self.access_log = []
    
    def add_authorized_user(self, user_id: str):
        """
        添加授权用户
        
        Args:
            user_id: 用户ID
        """
        if user_id not in self.authorized_users:
            self.authorized_users.append(user_id)
            logger.info(f"用户 {user_id} 已添加到授权列表")
    
    def remove_authorized_user(self, user_id: str):
        """
        移除授权用户
        
        Args:
            user_id: 用户ID
        """
        if user_id in self.authorized_users:
            self.authorized_users.remove(user_id)
            logger.info(f"用户 {user_id} 已从授权列表移除")
    
    def check_access(self, user_id: str) -> bool:
        """
        检查用户访问权限
        
        Args:
            user_id: 用户ID
            
        Returns:
            是否有访问权限
        """
        has_access = user_id in self.authorized_users
        self._log_access_attempt(user_id, has_access)
        return has_access
    
    def _log_access_attempt(self, user_id: str, granted: bool):
        """
        记录访问尝试
        
        Args:
            user_id: 用户ID
            granted: 是否授权
        """
        import datetime
        log_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "user_id": user_id,
            "access_granted": granted
        }
        self.access_log.append(log_entry)
    
    def get_access_log(self) -> List[Dict[str, Any]]:
        """
        获取访问日志
        
        Returns:
            访问日志列表
        """
        return self.access_log.copy()


class PrivacyPreserver:
    """隐私保护器"""
    
    def __init__(self, encryption_password: Optional[str] = None):
        """
        初始化隐私保护器
        
        Args:
            encryption_password: 加密密码
        """
        self.encryption = DataEncryption(encryption_password)
        self.anonymizer = DataAnonymizer()
        self.access_control = AccessControl()
        
        # 敏感信息关键词列表
        self.sensitive_keywords = [
            "密码", "password", "passwd", "pwd",
            "身份证", "idcard", "id_card",
            "电话", "phone", "telephone",
            "地址", "address",
            "银行卡", "bankcard", "creditcard",
            "邮箱", "email", "mail"
        ]
    
    def preserve_privacy(self, data: List[Dict[str, Any]], 
                       user_id: str = "default_user") -> List[Dict[str, Any]]:
        """
        保护数据隐私
        
        Args:
            data: 原始数据
            user_id: 用户ID
            
        Returns:
            隐私保护后的数据
        """
        # 1. 检查访问权限
        if not self.access_control.check_access(user_id):
            raise PermissionError(f"用户 {user_id} 没有访问权限")
        
        # 2. 匿名化处理
        anonymized_data = self.anonymizer.anonymize_conversation(data)
        
        # 3. 检查敏感信息
        filtered_data = self._filter_sensitive_info(anonymized_data)
        
        logger.info(f"已完成数据隐私保护，处理 {len(data)} 条记录")
        return filtered_data
    
    def _filter_sensitive_info(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        过滤敏感信息
        
        Args:
            data: 数据列表
            
        Returns:
            过滤后的数据
        """
        filtered_data = []
        
        for record in data:
            filtered_record = record.copy()
            
            # 检查消息内容是否包含敏感信息
            message = str(filtered_record.get("message", ""))
            if self._contains_sensitive_info(message):
                # 对包含敏感信息的记录进行特殊处理
                filtered_record["message"] = "[SENSITIVE_CONTENT_FILTERED]"
                logger.warning(f"检测到敏感信息，已过滤: {message[:50]}...")
            
            filtered_data.append(filtered_record)
        
        return filtered_data
    
    def _contains_sensitive_info(self, text: str) -> bool:
        """
        检查文本是否包含敏感信息
        
        Args:
            text: 文本内容
            
        Returns:
            是否包含敏感信息
        """
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in self.sensitive_keywords)
    
    def encrypt_and_save_data(self, data: List[Dict[str, Any]], file_path: str):
        """
        加密并保存数据
        
        Args:
            data: 数据列表
            file_path: 文件路径
        """
        # 将数据转换为JSON字符串
        data_str = json.dumps(data, ensure_ascii=False, indent=2)
        
        # 加密数据
        encrypted_data = self.encryption.encrypt_data(data_str)
        
        # 保存加密数据
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(encrypted_data)
        
        logger.info(f"数据已加密并保存到: {file_path}")
    
    def load_and_decrypt_data(self, file_path: str) -> List[Dict[str, Any]]:
        """
        加载并解密数据
        
        Args:
            file_path: 文件路径
            
        Returns:
            解密后的数据
        """
        # 读取加密数据
        with open(file_path, 'r', encoding='utf-8') as f:
            encrypted_data = f.read()
        
        # 解密数据
        decrypted_data_str = self.encryption.decrypt_data(encrypted_data)
        
        # 解析JSON
        data = json.loads(decrypted_data_str)
        
        logger.info(f"数据已解密并加载自: {file_path}")
        return data
    
    def add_authorized_user(self, user_id: str):
        """
        添加授权用户
        
        Args:
            user_id: 用户ID
        """
        self.access_control.add_authorized_user(user_id)
    
    def remove_authorized_user(self, user_id: str):
        """
        移除授权用户
        
        Args:
            user_id: 用户ID
        """
        self.access_control.remove_authorized_user(user_id)


def main():
    """主函数 - 使用示例"""
    logger.info("隐私保护工具模块演示")
    
    # 创建隐私保护器
    privacy_preserver = PrivacyPreserver(encryption_password="my_secret_password")
    
    # 添加授权用户
    privacy_preserver.add_authorized_user("user_001")
    privacy_preserver.add_authorized_user("admin")
    
    # 示例对话数据
    sample_conversation = [
        {"timestamp": "2023-01-01 10:00:00", "sender": "Alice", "message": "你好，我的密码是123456"},
        {"timestamp": "2023-01-01 10:00:30", "sender": "Bob", "message": "你好Alice，我是Bob"},
        {"timestamp": "2023-01-01 10:01:00", "sender": "Alice", "message": "我的电话号码是13800138000"},
        {"timestamp": "2023-01-01 10:01:30", "sender": "Bob", "message": "我们明天见面吧"},
    ]
    
    try:
        # 保护隐私
        protected_data = privacy_preserver.preserve_privacy(
            sample_conversation, 
            user_id="user_001"
        )
        
        print("原始数据:")
        for record in sample_conversation:
            print(f"  {record}")
        
        print("\n隐私保护后数据:")
        for record in protected_data:
            print(f"  {record}")
        
        # 加密并保存数据
        encrypted_file_path = "data/protected_conversation.encrypted"
        os.makedirs(os.path.dirname(encrypted_file_path), exist_ok=True)
        privacy_preserver.encrypt_and_save_data(protected_data, encrypted_file_path)
        print(f"\n数据已加密保存到: {encrypted_file_path}")
        
        # 加载并解密数据
        decrypted_data = privacy_preserver.load_and_decrypt_data(encrypted_file_path)
        print("\n解密后的数据:")
        for record in decrypted_data:
            print(f"  {record}")
            
    except PermissionError as e:
        print(f"访问被拒绝: {e}")
    except Exception as e:
        print(f"处理过程中出错: {e}")


if __name__ == "__main__":
    main()