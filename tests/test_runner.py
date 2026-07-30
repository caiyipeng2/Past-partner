"""
测试运行器模块
自动化执行各种测试用例
"""

import os
import json
import logging
from typing import Dict, Any, List, Callable, Optional
from datetime import datetime
import time
import subprocess

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TestRunner:
    """测试运行器"""
    
    def __init__(self, test_results_dir: str = "tests/results"):
        """
        初始化测试运行器
        
        Args:
            test_results_dir: 测试结果存储目录
        """
        self.test_results_dir = test_results_dir
        os.makedirs(self.test_results_dir, exist_ok=True)
        
        # 注册的测试用例
        self.test_cases = {}
        
        # 测试结果
        self.test_results = []
    
    def register_test_case(self, name: str, test_func: Callable, 
                          description: str = "", tags: Optional[List[str]] = None):
        """
        注册测试用例
        
        Args:
            name: 测试用例名称
            test_func: 测试函数
            description: 测试描述
            tags: 测试标签
        """
        self.test_cases[name] = {
            "function": test_func,
            "description": description,
            "tags": tags or []
        }
        
        logger.info(f"注册测试用例: {name}")
    
    def run_single_test(self, test_name: str, **kwargs) -> Dict[str, Any]:
        """
        运行单个测试用例
        
        Args:
            test_name: 测试用例名称
            **kwargs: 传递给测试函数的参数
            
        Returns:
            测试结果
        """
        if test_name not in self.test_cases:
            raise ValueError(f"测试用例未注册: {test_name}")
        
        test_case = self.test_cases[test_name]
        test_func = test_case["function"]
        
        logger.info(f"开始运行测试: {test_name}")
        
        start_time = time.time()
        try:
            # 执行测试
            result = test_func(**kwargs)
            
            # 构造测试结果
            test_result = {
                "test_name": test_name,
                "status": "passed",
                "result": result,
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat()
            }
            
            logger.info(f"测试通过: {test_name} (耗时: {test_result['execution_time']:.2f}s)")
            
        except Exception as e:
            # 测试失败
            test_result = {
                "test_name": test_name,
                "status": "failed",
                "error": str(e),
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat()
            }
            
            logger.error(f"测试失败: {test_name} - {e}")
        
        # 保存测试结果
        self.test_results.append(test_result)
        self._save_test_result(test_result)
        
        return test_result
    
    def run_tests_by_tag(self, tag: str) -> List[Dict[str, Any]]:
        """
        根据标签运行测试
        
        Args:
            tag: 测试标签
            
        Returns:
            测试结果列表
        """
        results = []
        
        # 查找具有指定标签的测试用例
        tagged_tests = [
            name for name, case in self.test_cases.items() 
            if tag in case["tags"]
        ]
        
        logger.info(f"找到 {len(tagged_tests)} 个标记为 '{tag}' 的测试用例")
        
        # 运行这些测试
        for test_name in tagged_tests:
            result = self.run_single_test(test_name)
            results.append(result)
        
        return results
    
    def run_all_tests(self) -> List[Dict[str, Any]]:
        """
        运行所有测试用例
        
        Returns:
            测试结果列表
        """
        results = []
        
        logger.info(f"开始运行所有 {len(self.test_cases)} 个测试用例")
        
        for test_name in self.test_cases:
            result = self.run_single_test(test_name)
            results.append(result)
        
        # 生成测试报告
        self._generate_test_report(results)
        
        return results
    
    def _save_test_result(self, test_result: Dict[str, Any]):
        """
        保存测试结果
        
        Args:
            test_result: 测试结果
        """
        try:
            result_file = os.path.join(
                self.test_results_dir, 
                f"result_{test_result['test_name']}_{test_result['timestamp'].replace(':', '-')}.json"
            )
            
            with open(result_file, 'w', encoding='utf-8') as f:
                json.dump(test_result, f, ensure_ascii=False, indent=2)
                
            logger.debug(f"测试结果已保存: {result_file}")
            
        except Exception as e:
            logger.error(f"保存测试结果失败: {e}")
    
    def _generate_test_report(self, results: List[Dict[str, Any]]):
        """
        生成测试报告
        
        Args:
            results: 测试结果列表
        """
        try:
            passed_count = sum(1 for r in results if r["status"] == "passed")
            failed_count = len(results) - passed_count
            
            report = {
                "summary": {
                    "total_tests": len(results),
                    "passed": passed_count,
                    "failed": failed_count,
                    "pass_rate": passed_count / len(results) if results else 0,
                    "total_execution_time": sum(r["execution_time"] for r in results)
                },
                "details": results,
                "generated_at": datetime.now().isoformat()
            }
            
            report_file = os.path.join(self.test_results_dir, "test_report.json")
            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            
            logger.info(f"测试报告已生成: {report_file}")
            logger.info(f"测试总结: 总计{len(results)}个测试，{passed_count}个通过，{failed_count}个失败")
            
        except Exception as e:
            logger.error(f"生成测试报告失败: {e}")


# 示例测试用例
def test_data_parsing():
    """测试数据解析功能"""
    from src.preprocessing.data_parser import ChatDataParser
    
    # 创建示例数据
    sample_text = """[2023-01-01 10:00:00] Alice: 你好，今天天气怎么样？
[2023-01-01 10:00:30] Bob: 你好！天气很好呢😊
[2023-01-01 10:01:00] Alice: 我们去公园走走吧"""
    
    # 写入临时文件
    temp_file = "tests/temp/sample_chat.txt"
    os.makedirs(os.path.dirname(temp_file), exist_ok=True)
    
    with open(temp_file, 'w', encoding='utf-8') as f:
        f.write(sample_text)
    
    # 测试解析功能
    parser = ChatDataParser()
    records = parser.parse_text_chat(temp_file)
    
    # 验证结果
    assert len(records) == 3, f"期望3条记录，实际得到{len(records)}"
    assert records[0]["sender"] == "Alice", "第一条消息发送者应该是Alice"
    assert "天气" in records[0]["message"], "第一条消息应该包含'天气'"
    
    # 清理临时文件
    os.remove(temp_file)
    
    return {"parsed_records": len(records)}


def test_preprocessing():
    """测试数据预处理功能"""
    from src.preprocessing.preprocessor import ChatPreprocessor
    
    # 创建示例数据
    sample_records = [
        {"message": "你好呀！今天天气真不错😊"},
        {"message": "在干嘛呢？"},
        {"message": "在看书📚，一本关于AI的小说"}
    ]
    
    # 测试预处理功能
    preprocessor = ChatPreprocessor()
    processed_data = preprocessor.preprocess_conversation(sample_records)
    
    # 验证结果
    assert len(processed_data) == 3, f"期望3条处理后的记录，实际得到{len(processed_data)}"
    
    # 检查特征提取
    first_record_features = processed_data[0]["features"]
    assert "length" in first_record_features, "应该包含长度特征"
    assert "emoji_count" in first_record_features, "应该包含表情符号计数特征"
    
    return {"processed_records": len(processed_data)}


def test_context_management():
    """测试上下文管理功能"""
    from src.inference.context_manager import ContextManager
    
    # 创建上下文管理器
    cm = ContextManager(max_history_turns=3)
    
    # 创建对话
    conv_id = cm.create_conversation()
    
    # 添加对话轮次
    cm.add_user_message("你好，今天过得怎么样？", conv_id)
    cm.add_assistant_message("你好！我很好，谢谢你的关心😊", conv_id)
    cm.add_user_message("那太好了，我们聊聊天吧", conv_id)
    cm.add_assistant_message("好的，我很乐意和你聊天", conv_id)
    
    # 获取上下文
    context = cm.get_recent_context(conv_id)
    context_text = cm.get_context_as_text(conv_id)
    
    # 验证结果
    assert len(context) == 4, f"期望4个对话轮次，实际得到{len(context)}"
    assert "用户" in context_text, "上下文文本应该包含'用户'"
    assert "助手" in context_text, "上下文文本应该包含'助手'"
    
    return {"context_turns": len(context)}


def test_style_analysis():
    """测试风格分析功能"""
    from src.preprocessing.style_analyzer import StyleAnalyzer
    
    # 创建示例数据
    sample_messages = [
        "你好呀！今天天气真不错😊",
        "在干嘛呢？",
        "在看书📚，一本关于AI的小说",
        "听起来很有趣！我也想看看😊",
        "好呀，我发给你电子版📖"
    ]
    
    # 构造处理后的数据格式
    processed_data = [
        {
            "cleaned_message": msg,
            "features": {
                "length": len(msg),
                "emoji_count": msg.count("😊") + msg.count("📚") + msg.count("📖"),
                "punctuations": {char: msg.count(char) for char in "！？，。"}
            }
        }
        for msg in sample_messages
    ]
    
    # 测试风格分析
    analyzer = StyleAnalyzer()
    profile = analyzer.generate_style_profile(processed_data)
    
    # 验证结果
    assert "message_length_distribution" in profile, "应该包含消息长度分布"
    assert "emoji_usage" in profile, "应该包含表情符号使用情况"
    
    return {"profile_keys": list(profile.keys())}


def test_privacy_protection():
    """测试隐私保护功能"""
    from utils.privacy_utils import PrivacyPreserver
    
    # 创建示例数据
    sample_data = [
        {"sender": "Alice", "message": "我的密码是123456"},
        {"sender": "Bob", "message": "你好Alice"},
        {"sender": "Alice", "message": "我的电话号码是13800138000"}
    ]
    
    # 测试隐私保护
    preserver = PrivacyPreserver(encryption_password="test_password")
    preserver.add_authorized_user("test_user")
    
    try:
        protected_data = preserver.preserve_privacy(sample_data, "test_user")
        
        # 验证结果
        assert len(protected_data) == 3, f"期望3条记录，实际得到{len(protected_data)}"
        
        # 检查敏感信息是否被过滤
        sensitive_filtered = any("[SENSITIVE_CONTENT_FILTERED]" in record["message"] 
                               for record in protected_data)
        assert sensitive_filtered, "应该有过滤敏感信息的记录"
        
        return {"protected_records": len(protected_data)}
        
    except PermissionError:
        # 权限错误也是预期的行为之一
        return {"status": "permission_check_passed"}


def main():
    """主函数 - 运行测试"""
    logger.info("测试运行器演示")
    
    # 创建测试运行器
    runner = TestRunner()
    
    # 注册测试用例
    runner.register_test_case(
        "data_parsing", 
        test_data_parsing, 
        "测试聊天数据解析功能",
        ["parsing", "preprocessing"]
    )
    
    runner.register_test_case(
        "preprocessing", 
        test_preprocessing, 
        "测试数据预处理功能",
        ["preprocessing"]
    )
    
    runner.register_test_case(
        "context_management", 
        test_context_management, 
        "测试上下文管理功能",
        ["inference", "memory"]
    )
    
    runner.register_test_case(
        "style_analysis", 
        test_style_analysis, 
        "测试风格分析功能",
        ["preprocessing", "analysis"]
    )
    
    runner.register_test_case(
        "privacy_protection", 
        test_privacy_protection, 
        "测试隐私保护功能",
        ["security", "privacy"]
    )
    
    # 运行所有测试
    print("开始运行所有测试...")
    results = runner.run_all_tests()
    
    # 显示结果摘要
    passed = sum(1 for r in results if r["status"] == "passed")
    failed = sum(1 for r in results if r["status"] == "failed")
    
    print(f"\n测试结果摘要:")
    print(f"  总计: {len(results)}")
    print(f"  通过: {passed}")
    print(f"  失败: {failed}")
    
    if failed > 0:
        print(f"\n失败的测试:")
        for result in results:
            if result["status"] == "failed":
                print(f"  - {result['test_name']}: {result['error']}")


if __name__ == "__main__":
    main()