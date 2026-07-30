"""
数据处理工具模块
结合隐私保护功能的数据处理工具
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional
from utils.privacy_utils import PrivacyPreserver

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SecureDataProcessor:
    """安全数据处理器"""
    
    def __init__(self, encryption_password: Optional[str] = None):
        """
        初始化安全数据处理器
        
        Args:
            encryption_password: 加密密码
        """
        self.privacy_preserver = PrivacyPreserver(encryption_password)
    
    def process_chat_data(self, input_path: str, output_path: str, 
                         user_id: str = "default_user") -> bool:
        """
        处理聊天数据（包括隐私保护）
        
        Args:
            input_path: 输入文件路径
            output_path: 输出文件路径
            user_id: 用户ID
            
        Returns:
            处理是否成功
        """
        try:
            # 1. 读取原始数据
            logger.info(f"正在读取数据: {input_path}")
            with open(input_path, 'r', encoding='utf-8') as f:
                raw_data = json.load(f)
            
            # 2. 隐私保护处理
            logger.info("正在进行隐私保护处理")
            protected_data = self.privacy_preserver.preserve_privacy(raw_data, user_id)
            
            # 3. 保存处理后的数据
            logger.info(f"正在保存处理后的数据: {output_path}")
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(protected_data, f, ensure_ascii=False, indent=2)
            
            logger.info("数据处理完成")
            return True
            
        except Exception as e:
            logger.error(f"数据处理失败: {e}")
            return False
    
    def secure_train_test_split(self, data_path: str, train_ratio: float = 0.8,
                              train_output_path: Optional[str] = None,
                              test_output_path: Optional[str] = None) -> bool:
        """
        安全地分割训练集和测试集
        
        Args:
            data_path: 数据文件路径
            train_ratio: 训练集比例
            train_output_path: 训练集输出路径
            test_output_path: 测试集输出路径
            
        Returns:
            分割是否成功
        """
        try:
            # 读取数据
            with open(data_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 随机分割数据
            import random
            random.shuffle(data)
            
            split_index = int(len(data) * train_ratio)
            train_data = data[:split_index]
            test_data = data[split_index:]
            
            # 保存训练集
            if train_output_path:
                os.makedirs(os.path.dirname(train_output_path), exist_ok=True)
                with open(train_output_path, 'w', encoding='utf-8') as f:
                    json.dump(train_data, f, ensure_ascii=False, indent=2)
                logger.info(f"训练集已保存到: {train_output_path}")
            
            # 保存测试集
            if test_output_path:
                os.makedirs(os.path.dirname(test_output_path), exist_ok=True)
                with open(test_output_path, 'w', encoding='utf-8') as f:
                    json.dump(test_data, f, ensure_ascii=False, indent=2)
                logger.info(f"测试集已保存到: {test_output_path}")
            
            logger.info(f"数据分割完成: 训练集 {len(train_data)} 条，测试集 {len(test_data)} 条")
            return True
            
        except Exception as e:
            logger.error(f"数据分割失败: {e}")
            return False
    
    def add_authorized_user(self, user_id: str):
        """
        添加授权用户
        
        Args:
            user_id: 用户ID
        """
        self.privacy_preserver.add_authorized_user(user_id)
    
    def remove_authorized_user(self, user_id: str):
        """
        移除授权用户
        
        Args:
            user_id: 用户ID
        """
        self.privacy_preserver.remove_authorized_user(user_id)


class DataIntegrityChecker:
    """数据完整性检查器"""
    
    def __init__(self):
        pass
    
    def calculate_checksum(self, file_path: str) -> str:
        """
        计算文件校验和
        
        Args:
            file_path: 文件路径
            
        Returns:
            MD5校验和
        """
        import hashlib
        
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    
    def verify_data_integrity(self, file_path: str, expected_checksum: str) -> bool:
        """
        验证数据完整性
        
        Args:
            file_path: 文件路径
            expected_checksum: 期望的校验和
            
        Returns:
            数据是否完整
        """
        actual_checksum = self.calculate_checksum(file_path)
        is_valid = actual_checksum.lower() == expected_checksum.lower()
        
        if is_valid:
            logger.info(f"数据完整性验证通过: {file_path}")
        else:
            logger.warning(f"数据完整性验证失败: {file_path}")
            logger.warning(f"期望校验和: {expected_checksum}")
            logger.warning(f"实际校验和: {actual_checksum}")
        
        return is_valid
    
    def generate_integrity_report(self, file_paths: List[str]) -> Dict[str, str]:
        """
        生成完整性报告
        
        Args:
            file_paths: 文件路径列表
            
        Returns:
            完整性报告字典
        """
        report = {}
        for file_path in file_paths:
            if os.path.exists(file_path):
                checksum = self.calculate_checksum(file_path)
                report[file_path] = checksum
            else:
                report[file_path] = "FILE_NOT_FOUND"
        
        return report


def main():
    """主函数 - 使用示例"""
    logger.info("安全数据处理工具演示")
    
    # 创建安全数据处理器
    processor = SecureDataProcessor(encryption_password="data_processing_password")
    
    # 添加授权用户
    processor.add_authorized_user("data_processor")
    
    # 创建示例数据
    sample_data_path = "data/raw/sample_data.json"
    os.makedirs(os.path.dirname(sample_data_path), exist_ok=True)
    
    sample_data = [
        {"timestamp": "2023-01-01 10:00:00", "sender": "user1", "message": "你好，今天天气怎么样？"},
        {"timestamp": "2023-01-01 10:00:30", "sender": "assistant", "message": "你好！天气很好呢😊"},
        {"timestamp": "2023-01-01 10:01:00", "sender": "user1", "message": "我想知道你的名字"},
        {"timestamp": "2023-01-01 10:01:15", "sender": "assistant", "message": "我是你的AI助手"},
        {"timestamp": "2023-01-01 10:01:30", "sender": "user1", "message": "你能告诉我一个秘密吗？"},
        {"timestamp": "2023-01-01 10:02:00", "sender": "assistant", "message": "当然可以，但我不会泄露你的隐私信息"},
    ]
    
    with open(sample_data_path, 'w', encoding='utf-8') as f:
        json.dump(sample_data, f, ensure_ascii=False, indent=2)
    
    print(f"示例数据已创建: {sample_data_path}")
    
    # 处理数据
    processed_data_path = "data/processed/protected_data.json"
    success = processor.process_chat_data(
        sample_data_path, 
        processed_data_path, 
        user_id="data_processor"
    )
    
    if success:
        print(f"数据处理成功，结果保存到: {processed_data_path}")
        
        # 显示处理后的数据
        with open(processed_data_path, 'r', encoding='utf-8') as f:
            processed_data = json.load(f)
        
        print("\n处理后的数据:")
        for record in processed_data:
            print(f"  {record}")
        
        # 数据分割
        train_path = "data/datasets/train.json"
        test_path = "data/datasets/test.json"
        
        split_success = processor.secure_train_test_split(
            processed_data_path,
            train_ratio=0.7,
            train_output_path=train_path,
            test_output_path=test_path
        )
        
        if split_success:
            print(f"\n数据分割成功:")
            print(f"  训练集: {train_path}")
            print(f"  测试集: {test_path}")
        
        # 完整性检查
        checker = DataIntegrityChecker()
        checksum = checker.calculate_checksum(processed_data_path)
        print(f"\n数据文件校验和: {checksum}")
        
        is_valid = checker.verify_data_integrity(processed_data_path, checksum)
        print(f"数据完整性验证: {'通过' if is_valid else '失败'}")
        
    else:
        print("数据处理失败")


if __name__ == "__main__":
    main()