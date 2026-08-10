"""
数据解析模块
用于解析不同格式的历史聊天记录
"""

import logging
from collections.abc import Iterable
from typing import List, Dict, Any

from src.learning.style_profile import StyleProfileExtractor
from src.preprocessing.parser_registry import ParserRegistry

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ChatDataParser:
    """聊天数据解析器"""

    def __init__(self, registry: ParserRegistry | None = None):
        self.registry = registry or ParserRegistry.with_builtins()
    
    def parse_text_chat(self, file_path: str) -> List[Dict[str, Any]]:
        """
        解析纯文本格式的聊天记录
        假设格式为: [时间] 发送者: 消息内容
        
        Args:
            file_path: 文件路径
            
        Returns:
            解析后的聊天记录列表
        """
        return self._parse_legacy(file_path, "generic_text")
    
    def parse_json_chat(self, file_path: str) -> List[Dict[str, Any]]:
        """
        解析JSON格式的聊天记录
        
        Args:
            file_path: 文件路径
            
        Returns:
            解析后的聊天记录列表
        """
        return self._parse_legacy(file_path, "generic_json")
    
    def parse_chat_data(self, file_path: str, format_type: str = 'auto') -> List[Dict[str, Any]]:
        """
        自动解析聊天记录
        
        Args:
            file_path: 文件路径
            format_type: 格式类型 ('text', 'json', 'auto')
            
        Returns:
            解析后的聊天记录列表
        """
        metadata = None
        if format_type == "text":
            metadata = {"source_type": "generic_text"}
        elif format_type == "json":
            metadata = {"source_type": "generic_json"}
        elif format_type == "jsonl":
            metadata = {"source_type": "generic_jsonl"}
        elif format_type != "auto":
            raise ValueError(f"不支持的格式类型: {format_type}")

        result = self.registry.parse(file_path, metadata)
        return [self._legacy_record(record.to_dict(), index) for index, record in enumerate(result.records, 1)]

    def generate_style_profile(
        self,
        file_path: str,
        persona_sender_ids: Iterable[str],
        *,
        format_type: str = "auto",
        user_sender_ids: Iterable[str] = (),
        known_addresses: Iterable[str] = (),
        relationship_type: str | None = None,
        relationship_label: str | None = None,
        preferred_address: str | None = None,
    ) -> Dict[str, Any]:
        """Generate a profile from canonical parser output without retaining source text."""

        metadata = self._format_metadata(format_type)
        result = self.registry.parse(file_path, metadata)
        profile = StyleProfileExtractor().extract(
            result.records,
            persona_sender_ids=persona_sender_ids,
            user_sender_ids=user_sender_ids,
            known_addresses=known_addresses,
            relationship_type=relationship_type,
            relationship_label=relationship_label,
            preferred_address=preferred_address,
        ).to_dict()
        profile["source_type"] = result.source_type
        return profile

    def _parse_legacy(self, file_path: str, source_type: str) -> List[Dict[str, Any]]:
        result = self.registry.parse(file_path, {"source_type": source_type})
        return [self._legacy_record(record.to_dict(), index) for index, record in enumerate(result.records, 1)]

    @staticmethod
    def _format_metadata(format_type: str) -> Dict[str, str]:
        if format_type == "auto":
            return {}
        if format_type in {"text", "json", "jsonl"}:
            return {
                "source_type": {
                    "text": "generic_text",
                    "json": "generic_json",
                    "jsonl": "generic_jsonl",
                }[format_type]
            }
        raise ValueError(f"不支持的格式类型: {format_type}")

    @staticmethod
    def _legacy_record(record: Dict[str, Any], line_num: int) -> Dict[str, Any]:
        """Keep the old training/preprocessing aliases during migration."""
        record["sender"] = record["sender_id"]
        record["message"] = record["content"]
        record["line_num"] = line_num
        return record


if __name__ == "__main__":
    # 测试代码
    parser = ChatDataParser()
    
    # 示例使用
    # records = parser.parse_chat_data("data/raw/sample.txt", "text")
    # records = parser.parse_chat_data("data/raw/sample.json", "json")
    pass
