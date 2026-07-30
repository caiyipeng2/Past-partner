"""
测试框架模块
提供用户测试和反馈收集功能
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import uuid

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class UserTestManager:
    """用户测试管理器"""
    
    def __init__(self, test_data_dir: str = "tests/user_tests"):
        """
        初始化用户测试管理器
        
        Args:
            test_data_dir: 测试数据存储目录
        """
        self.test_data_dir = test_data_dir
        os.makedirs(self.test_data_dir, exist_ok=True)
        
        # 测试会话存储
        self.active_sessions = {}
    
    def create_test_session(self, user_id: str, test_config: Dict[str, Any]) -> str:
        """
        创建测试会话
        
        Args:
            user_id: 用户ID
            test_config: 测试配置
            
        Returns:
            会话ID
        """
        session_id = str(uuid.uuid4())
        
        session_data = {
            "session_id": session_id,
            "user_id": user_id,
            "start_time": datetime.now().isoformat(),
            "test_config": test_config,
            "interactions": [],
            "feedback": {},
            "status": "active"
        }
        
        self.active_sessions[session_id] = session_data
        
        # 保存会话数据
        session_file = os.path.join(self.test_data_dir, f"session_{session_id}.json")
        with open(session_file, 'w', encoding='utf-8') as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"创建测试会话: {session_id} (用户: {user_id})")
        return session_id
    
    def record_interaction(self, session_id: str, interaction_data: Dict[str, Any]) -> bool:
        """
        记录用户交互数据
        
        Args:
            session_id: 会话ID
            interaction_data: 交互数据
            
        Returns:
            记录是否成功
        """
        if session_id not in self.active_sessions:
            logger.warning(f"会话不存在: {session_id}")
            return False
        
        session_data = self.active_sessions[session_id]
        
        # 添加时间戳
        interaction_data["timestamp"] = datetime.now().isoformat()
        
        # 记录交互
        session_data["interactions"].append(interaction_data)
        
        # 更新会话文件
        session_file = os.path.join(self.test_data_dir, f"session_{session_id}.json")
        with open(session_file, 'w', encoding='utf-8') as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)
        
        logger.debug(f"记录交互数据: {session_id}")
        return True
    
    def submit_feedback(self, session_id: str, feedback_data: Dict[str, Any]) -> bool:
        """
        提交用户反馈
        
        Args:
            session_id: 会话ID
            feedback_data: 反馈数据
            
        Returns:
            提交是否成功
        """
        if session_id not in self.active_sessions:
            logger.warning(f"会话不存在: {session_id}")
            return False
        
        session_data = self.active_sessions[session_id]
        
        # 更新反馈数据
        session_data["feedback"].update(feedback_data)
        session_data["feedback"]["submit_time"] = datetime.now().isoformat()
        
        # 更新会话文件
        session_file = os.path.join(self.test_data_dir, f"session_{session_id}.json")
        with open(session_file, 'w', encoding='utf-8') as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"提交用户反馈: {session_id}")
        return True
    
    def end_test_session(self, session_id: str, final_feedback: Optional[Dict[str, Any]] = None) -> bool:
        """
        结束测试会话
        
        Args:
            session_id: 会话ID
            final_feedback: 最终反馈数据
            
        Returns:
            结束是否成功
        """
        if session_id not in self.active_sessions:
            logger.warning(f"会话不存在: {session_id}")
            return False
        
        session_data = self.active_sessions[session_id]
        
        # 更新最终反馈
        if final_feedback:
            session_data["feedback"].update(final_feedback)
        
        # 更新状态和结束时间
        session_data["status"] = "completed"
        session_data["end_time"] = datetime.now().isoformat()
        
        # 更新会话文件
        session_file = os.path.join(self.test_data_dir, f"session_{session_id}.json")
        with open(session_file, 'w', encoding='utf-8') as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)
        
        # 从活跃会话中移除
        del self.active_sessions[session_id]
        
        logger.info(f"测试会话结束: {session_id}")
        return True
    
    def get_session_data(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        获取会话数据
        
        Args:
            session_id: 会话ID
            
        Returns:
            会话数据或None
        """
        return self.active_sessions.get(session_id)
    
    def list_sessions(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        列出会话
        
        Args:
            user_id: 用户ID（可选）
            
        Returns:
            会话列表
        """
        sessions = []
        
        for session_id, session_data in self.active_sessions.items():
            if user_id is None or session_data["user_id"] == user_id:
                sessions.append({
                    "session_id": session_id,
                    "user_id": session_data["user_id"],
                    "start_time": session_data["start_time"],
                    "status": session_data["status"],
                    "interaction_count": len(session_data["interactions"])
                })
        
        return sessions


class FeedbackCollector:
    """反馈收集器"""
    
    def __init__(self, feedback_dir: str = "tests/feedback"):
        """
        初始化反馈收集器
        
        Args:
            feedback_dir: 反馈数据存储目录
        """
        self.feedback_dir = feedback_dir
        os.makedirs(self.feedback_dir, exist_ok=True)
    
    def collect_quantitative_feedback(self, session_id: str, ratings: Dict[str, int]) -> bool:
        """
        收集定量反馈（评分）
        
        Args:
            session_id: 会话ID
            ratings: 评分数据（维度:分数）
            
        Returns:
            收集是否成功
        """
        feedback_data = {
            "type": "quantitative",
            "session_id": session_id,
            "ratings": ratings,
            "timestamp": datetime.now().isoformat()
        }
        
        return self._save_feedback(feedback_data)
    
    def collect_qualitative_feedback(self, session_id: str, comments: Dict[str, str]) -> bool:
        """
        收集定性反馈（评论）
        
        Args:
            session_id: 会话ID
            comments: 评论数据（维度:评论内容）
            
        Returns:
            收集是否成功
        """
        feedback_data = {
            "type": "qualitative",
            "session_id": session_id,
            "comments": comments,
            "timestamp": datetime.now().isoformat()
        }
        
        return self._save_feedback(feedback_data)
    
    def collect_bug_report(self, session_id: str, bug_data: Dict[str, Any]) -> bool:
        """
        收集bug报告
        
        Args:
            session_id: 会话ID
            bug_data: bug数据
            
        Returns:
            收集是否成功
        """
        feedback_data = {
            "type": "bug_report",
            "session_id": session_id,
            "bug_data": bug_data,
            "timestamp": datetime.now().isoformat()
        }
        
        return self._save_feedback(feedback_data)
    
    def collect_feature_request(self, session_id: str, request_data: Dict[str, Any]) -> bool:
        """
        收集功能请求
        
        Args:
            session_id: 会话ID
            request_data: 请求数据
            
        Returns:
            收集是否成功
        """
        feedback_data = {
            "type": "feature_request",
            "session_id": session_id,
            "request_data": request_data,
            "timestamp": datetime.now().isoformat()
        }
        
        return self._save_feedback(feedback_data)
    
    def _save_feedback(self, feedback_data: Dict[str, Any]) -> bool:
        """
        保存反馈数据
        
        Args:
            feedback_data: 反馈数据
            
        Returns:
            保存是否成功
        """
        try:
            feedback_id = str(uuid.uuid4())
            feedback_data["feedback_id"] = feedback_id
            
            feedback_file = os.path.join(self.feedback_dir, f"feedback_{feedback_id}.json")
            with open(feedback_file, 'w', encoding='utf-8') as f:
                json.dump(feedback_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"反馈数据已保存: {feedback_id} (类型: {feedback_data['type']})")
            return True
            
        except Exception as e:
            logger.error(f"保存反馈数据失败: {e}")
            return False
    
    def get_feedback_summary(self) -> Dict[str, Any]:
        """
        获取反馈摘要
        
        Returns:
            反馈摘要数据
        """
        summary = {
            "total_feedback": 0,
            "by_type": {},
            "by_date": {}
        }
        
        try:
            for filename in os.listdir(self.feedback_dir):
                if filename.startswith("feedback_") and filename.endswith(".json"):
                    summary["total_feedback"] += 1
                    
                    file_path = os.path.join(self.feedback_dir, filename)
                    with open(file_path, 'r', encoding='utf-8') as f:
                        feedback_data = json.load(f)
                    
                    # 按类型统计
                    feedback_type = feedback_data.get("type", "unknown")
                    summary["by_type"][feedback_type] = summary["by_type"].get(feedback_type, 0) + 1
                    
                    # 按日期统计
                    timestamp = feedback_data.get("timestamp", "")
                    if timestamp:
                        date = timestamp.split("T")[0]  # 提取日期部分
                        summary["by_date"][date] = summary["by_date"].get(date, 0) + 1
        
        except Exception as e:
            logger.error(f"生成反馈摘要失败: {e}")
        
        return summary


class ABTester:
    """A/B测试器"""
    
    def __init__(self, test_configs_dir: str = "tests/ab_configs"):
        """
        初始化A/B测试器
        
        Args:
            test_configs_dir: 测试配置存储目录
        """
        self.test_configs_dir = test_configs_dir
        os.makedirs(self.test_configs_dir, exist_ok=True)
        
        # 测试组分配
        self.user_groups = {}
    
    def create_ab_test(self, test_name: str, group_a_config: Dict[str, Any], 
                      group_b_config: Dict[str, Any]) -> bool:
        """
        创建A/B测试
        
        Args:
            test_name: 测试名称
            group_a_config: A组配置
            group_b_config: B组配置
            
        Returns:
            创建是否成功
        """
        try:
            test_config = {
                "test_name": test_name,
                "created_time": datetime.now().isoformat(),
                "group_a": group_a_config,
                "group_b": group_b_config
            }
            
            config_file = os.path.join(self.test_configs_dir, f"ab_test_{test_name}.json")
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(test_config, f, ensure_ascii=False, indent=2)
            
            logger.info(f"A/B测试已创建: {test_name}")
            return True
            
        except Exception as e:
            logger.error(f"创建A/B测试失败: {e}")
            return False
    
    def assign_user_group(self, user_id: str, test_name: str) -> str:
        """
        分配用户到测试组
        
        Args:
            user_id: 用户ID
            test_name: 测试名称
            
        Returns:
            分配的组别 ("A" 或 "B")
        """
        # 简单的哈希分配方法
        import hashlib
        hash_value = int(hashlib.md5((user_id + test_name).encode()).hexdigest(), 16)
        group = "A" if hash_value % 2 == 0 else "B"
        
        self.user_groups[user_id] = {
            "test_name": test_name,
            "group": group,
            "assigned_time": datetime.now().isoformat()
        }
        
        logger.info(f"用户 {user_id} 已分配到测试 {test_name} 的 {group} 组")
        return group
    
    def get_user_group(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        获取用户所属的测试组
        
        Args:
            user_id: 用户ID
            
        Returns:
            用户组信息或None
        """
        return self.user_groups.get(user_id)


def main():
    """主函数 - 使用示例"""
    logger.info("测试框架模块演示")
    
    # 创建用户测试管理器
    test_manager = UserTestManager()
    
    # 创建测试会话
    test_config = {
        "test_version": "1.0.0",
        "test_duration": "30 minutes",
        "focus_areas": ["conversation_quality", "style_matching", "response_speed"]
    }
    
    session_id = test_manager.create_test_session("user_001", test_config)
    print(f"创建测试会话: {session_id}")
    
    # 模拟用户交互
    interactions = [
        {
            "interaction_type": "chat",
            "user_input": "你好，今天天气怎么样？",
            "ai_response": "你好！今天天气很好呢，阳光明媚😊",
            "response_time": 0.5
        },
        {
            "interaction_type": "chat",
            "user_input": "你能讲个笑话吗？",
            "ai_response": "当然可以！为什么电脑去医院？因为它需要\"重启\"一下😄",
            "response_time": 0.8
        }
    ]
    
    for interaction in interactions:
        test_manager.record_interaction(session_id, interaction)
    
    print(f"记录了 {len(interactions)} 次交互")
    
    # 提交反馈
    feedback = {
        "overall_satisfaction": 4,
        "conversation_quality": 5,
        "style_matching": 4,
        "comments": "AI回复很自然，风格也很贴近预期"
    }
    
    test_manager.submit_feedback(session_id, feedback)
    print("已提交反馈")
    
    # 结束测试会话
    final_feedback = {
        "would_recommend": True,
        "final_comments": "整体体验很好，期待后续版本"
    }
    
    test_manager.end_test_session(session_id, final_feedback)
    print("测试会话已结束")
    
    # 创建反馈收集器
    feedback_collector = FeedbackCollector()
    
    # 收集不同类型反馈
    ratings = {
        "naturalness": 4,
        "helpfulness": 5,
        "engagement": 4
    }
    feedback_collector.collect_quantitative_feedback(session_id, ratings)
    
    comments = {
        "strengths": "回复很及时，语气自然",
        "improvements": "有时理解不够准确",
        "suggestions": "希望能记住更多上下文信息"
    }
    feedback_collector.collect_qualitative_feedback(session_id, comments)
    
    # 获取反馈摘要
    summary = feedback_collector.get_feedback_summary()
    print(f"\n反馈摘要: {summary}")
    
    # A/B测试示例
    ab_tester = ABTester()
    
    # 创建A/B测试
    group_a_config = {
        "temperature": 0.7,
        "top_p": 0.9,
        "style_weight": 0.8
    }
    
    group_b_config = {
        "temperature": 0.8,
        "top_p": 0.95,
        "style_weight": 0.9
    }
    
    ab_tester.create_ab_test("response_style_test", group_a_config, group_b_config)
    
    # 分配用户到测试组
    group = ab_tester.assign_user_group("user_001", "response_style_test")
    print(f"用户被分配到 {group} 组")
    
    user_group = ab_tester.get_user_group("user_001")
    print(f"用户组信息: {user_group}")


if __name__ == "__main__":
    main()