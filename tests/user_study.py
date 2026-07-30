"""
用户研究模块
用于组织和管理用户测试研究
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import csv

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class UserStudyManager:
    """用户研究管理器"""
    
    def __init__(self, study_dir: str = "tests/user_studies"):
        """
        初始化用户研究管理器
        
        Args:
            study_dir: 用户研究数据存储目录
        """
        self.study_dir = study_dir
        os.makedirs(self.study_dir, exist_ok=True)
        
        # 当前研究项目
        self.current_study = None
    
    def create_study(self, study_name: str, study_config: Dict[str, Any]) -> bool:
        """
        创建用户研究项目
        
        Args:
            study_name: 研究项目名称
            study_config: 研究配置
            
        Returns:
            创建是否成功
        """
        try:
            study_path = os.path.join(self.study_dir, study_name)
            os.makedirs(study_path, exist_ok=True)
            
            # 保存研究配置
            config_file = os.path.join(study_path, "study_config.json")
            study_config["created_at"] = datetime.now().isoformat()
            
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(study_config, f, ensure_ascii=False, indent=2)
            
            # 创建数据目录
            os.makedirs(os.path.join(study_path, "participants"), exist_ok=True)
            os.makedirs(os.path.join(study_path, "sessions"), exist_ok=True)
            os.makedirs(os.path.join(study_path, "feedback"), exist_ok=True)
            
            # 设置为当前研究项目
            self.current_study = {
                "name": study_name,
                "path": study_path,
                "config": study_config
            }
            
            logger.info(f"用户研究项目已创建: {study_name}")
            return True
            
        except Exception as e:
            logger.error(f"创建用户研究项目失败: {e}")
            return False
    
    def load_study(self, study_name: str) -> bool:
        """
        加载用户研究项目
        
        Args:
            study_name: 研究项目名称
            
        Returns:
            加载是否成功
        """
        try:
            study_path = os.path.join(self.study_dir, study_name)
            
            if not os.path.exists(study_path):
                logger.warning(f"研究项目不存在: {study_name}")
                return False
            
            # 加载研究配置
            config_file = os.path.join(study_path, "study_config.json")
            with open(config_file, 'r', encoding='utf-8') as f:
                study_config = json.load(f)
            
            # 设置为当前研究项目
            self.current_study = {
                "name": study_name,
                "path": study_path,
                "config": study_config
            }
            
            logger.info(f"用户研究项目已加载: {study_name}")
            return True
            
        except Exception as e:
            logger.error(f"加载用户研究项目失败: {e}")
            return False
    
    def register_participant(self, participant_id: str, 
                          participant_info: Dict[str, Any]) -> bool:
        """
        注册参与者
        
        Args:
            participant_id: 参与者ID
            participant_info: 参与者信息
            
        Returns:
            注册是否成功
        """
        if not self.current_study:
            logger.warning("没有加载的研究项目")
            return False
        
        try:
            participant_info["registered_at"] = datetime.now().isoformat()
            
            participant_file = os.path.join(
                self.current_study["path"], 
                "participants", 
                f"participant_{participant_id}.json"
            )
            
            with open(participant_file, 'w', encoding='utf-8') as f:
                json.dump(participant_info, f, ensure_ascii=False, indent=2)
            
            logger.info(f"参与者已注册: {participant_id}")
            return True
            
        except Exception as e:
            logger.error(f"注册参与者失败: {e}")
            return False
    
    def record_session(self, session_id: str, session_data: Dict[str, Any]) -> bool:
        """
        记录测试会话
        
        Args:
            session_id: 会话ID
            session_data: 会话数据
            
        Returns:
            记录是否成功
        """
        if not self.current_study:
            logger.warning("没有加载的研究项目")
            return False
        
        try:
            session_data["recorded_at"] = datetime.now().isoformat()
            
            session_file = os.path.join(
                self.current_study["path"], 
                "sessions", 
                f"session_{session_id}.json"
            )
            
            with open(session_file, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"会话数据已记录: {session_id}")
            return True
            
        except Exception as e:
            logger.error(f"记录会话数据失败: {e}")
            return False
    
    def collect_feedback(self, feedback_id: str, feedback_data: Dict[str, Any]) -> bool:
        """
        收集反馈数据
        
        Args:
            feedback_id: 反馈ID
            feedback_data: 反馈数据
            
        Returns:
            收集是否成功
        """
        if not self.current_study:
            logger.warning("没有加载的研究项目")
            return False
        
        try:
            feedback_data["collected_at"] = datetime.now().isoformat()
            
            feedback_file = os.path.join(
                self.current_study["path"], 
                "feedback", 
                f"feedback_{feedback_id}.json"
            )
            
            with open(feedback_file, 'w', encoding='utf-8') as f:
                json.dump(feedback_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"反馈数据已收集: {feedback_id}")
            return True
            
        except Exception as e:
            logger.error(f"收集反馈数据失败: {e}")
            return False
    
    def generate_study_report(self) -> Dict[str, Any]:
        """
        生成研究报告
        
        Returns:
            研究报告数据
        """
        if not self.current_study:
            logger.warning("没有加载的研究项目")
            return {}
        
        try:
            study_path = self.current_study["path"]
            
            report = {
                "study_name": self.current_study["name"],
                "generated_at": datetime.now().isoformat(),
                "participants": {},
                "sessions": {},
                "feedback": {}
            }
            
            # 统计参与者数据
            participants_dir = os.path.join(study_path, "participants")
            if os.path.exists(participants_dir):
                participant_files = [f for f in os.listdir(participants_dir) if f.endswith('.json')]
                report["participants"] = {
                    "count": len(participant_files),
                    "files": participant_files
                }
            
            # 统计会话数据
            sessions_dir = os.path.join(study_path, "sessions")
            if os.path.exists(sessions_dir):
                session_files = [f for f in os.listdir(sessions_dir) if f.endswith('.json')]
                report["sessions"] = {
                    "count": len(session_files),
                    "files": session_files
                }
            
            # 统计反馈数据
            feedback_dir = os.path.join(study_path, "feedback")
            if os.path.exists(feedback_dir):
                feedback_files = [f for f in os.listdir(feedback_dir) if f.endswith('.json')]
                report["feedback"] = {
                    "count": len(feedback_files),
                    "files": feedback_files
                }
            
            # 保存报告
            report_file = os.path.join(study_path, "study_report.json")
            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            
            logger.info(f"研究报告已生成: {report_file}")
            return report
            
        except Exception as e:
            logger.error(f"生成研究报告失败: {e}")
            return {}


class StudyDataAnalyzer:
    """研究数据分析器"""
    
    def __init__(self):
        pass
    
    def analyze_feedback_ratings(self, feedback_data_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        分析反馈评分数据
        
        Args:
            feedback_data_list: 反馈数据列表
            
        Returns:
            分析结果
        """
        if not feedback_data_list:
            return {}
        
        # 收集所有评分数据
        all_ratings = {}
        for feedback_data in feedback_data_list:
            if "ratings" in feedback_data:
                for dimension, rating in feedback_data["ratings"].items():
                    if dimension not in all_ratings:
                        all_ratings[dimension] = []
                    all_ratings[dimension].append(rating)
        
        # 计算统计信息
        analysis_results = {}
        for dimension, ratings in all_ratings.items():
            analysis_results[dimension] = {
                "count": len(ratings),
                "average": sum(ratings) / len(ratings) if ratings else 0,
                "min": min(ratings) if ratings else 0,
                "max": max(ratings) if ratings else 0,
                "distribution": self._calculate_rating_distribution(ratings)
            }
        
        return analysis_results
    
    def _calculate_rating_distribution(self, ratings: List[int]) -> Dict[int, int]:
        """
        计算评分分布
        
        Args:
            ratings: 评分列表
            
        Returns:
            评分分布字典
        """
        distribution = {}
        for rating in ratings:
            distribution[rating] = distribution.get(rating, 0) + 1
        return distribution
    
    def analyze_session_interactions(self, session_data_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        分析会话交互数据
        
        Args:
            session_data_list: 会话数据列表
            
        Returns:
            分析结果
        """
        if not session_data_list:
            return {}
        
        total_interactions = 0
        total_response_time = 0.0
        interaction_types = {}
        
        for session_data in session_data_list:
            if "interactions" in session_data:
                interactions = session_data["interactions"]
                total_interactions += len(interactions)
                
                for interaction in interactions:
                    # 统计交互类型
                    interaction_type = interaction.get("interaction_type", "unknown")
                    interaction_types[interaction_type] = interaction_types.get(interaction_type, 0) + 1
                    
                    # 累加响应时间
                    response_time = interaction.get("response_time", 0)
                    total_response_time += response_time
        
        analysis_results = {
            "total_interactions": total_interactions,
            "average_response_time": total_response_time / total_interactions if total_interactions > 0 else 0,
            "interaction_types": interaction_types
        }
        
        return analysis_results
    
    def export_to_csv(self, data: Dict[str, Any], output_file: str) -> bool:
        """
        导出数据到CSV文件
        
        Args:
            data: 要导出的数据
            output_file: 输出文件路径
            
        Returns:
            导出是否成功
        """
        try:
            with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)
                
                # 写入标题行
                if data:
                    keys = list(data.keys())
                    writer.writerow(keys)
                    
                    # 写入数据行
                    values = [str(data[key]) for key in keys]
                    writer.writerow(values)
            
            logger.info(f"数据已导出到CSV: {output_file}")
            return True
            
        except Exception as e:
            logger.error(f"导出CSV失败: {e}")
            return False


def main():
    """主函数 - 用户研究示例"""
    logger.info("用户研究模块演示")
    
    # 创建用户研究管理器
    study_manager = UserStudyManager()
    
    # 创建研究项目
    study_config = {
        "title": "个性化AI聊天伴侣用户体验研究",
        "description": "评估AI聊天伴侣在模仿特定人物风格方面的能力",
        "duration": "2 weeks",
        "target_participants": 50,
        "research_questions": [
            "AI是否能有效模仿目标人物的聊天风格？",
            "用户对AI回复的满意度如何？",
            "AI在多轮对话中的表现如何？"
        ]
    }
    
    study_manager.create_study("personalized_ai_study", study_config)
    
    # 注册参与者
    participants = [
        {
            "participant_id": "P001",
            "age": 25,
            "gender": "female",
            "tech_experience": "high",
            "preferred_communication_style": "friendly"
        },
        {
            "participant_id": "P002",
            "age": 32,
            "gender": "male",
            "tech_experience": "medium",
            "preferred_communication_style": "professional"
        }
    ]
    
    for participant in participants:
        pid = participant["participant_id"]
        info = {k: v for k, v in participant.items() if k != "participant_id"}
        study_manager.register_participant(pid, info)
    
    print(f"已注册 {len(participants)} 名参与者")
    
    # 记录会话数据
    session_data = {
        "participant_id": "P001",
        "session_duration": "15 minutes",
        "interactions": [
            {
                "interaction_type": "chat",
                "user_input": "你好，今天过得怎么样？",
                "ai_response": "你好！我今天过得很充实，看了几章书😊",
                "response_time": 0.8,
                "timestamp": "2023-01-01T10:00:00"
            },
            {
                "interaction_type": "chat",
                "user_input": "你在看什么书？",
                "ai_response": "在看一本关于人工智能发展的书，很有意思📚",
                "response_time": 0.6,
                "timestamp": "2023-01-01T10:01:00"
            }
        ]
    }
    
    study_manager.record_session("S001_P001", session_data)
    print("已记录会话数据")
    
    # 收集反馈
    feedback_data = {
        "participant_id": "P001",
        "session_id": "S001_P001",
        "ratings": {
            "naturalness": 4,
            "style_matching": 5,
            "responsiveness": 4,
            "overall_satisfaction": 4
        },
        "comments": {
            "positives": "AI回复很自然，风格也很贴近预期",
            "improvements": "有时理解不够准确",
            "suggestions": "希望能记住更多上下文信息"
        }
    }
    
    study_manager.collect_feedback("F001_P001", feedback_data)
    print("已收集反馈数据")
    
    # 生成研究报告
    report = study_manager.generate_study_report()
    print(f"研究报告已生成: {report.get('study_name', 'Unknown')}")
    
    # 数据分析示例
    analyzer = StudyDataAnalyzer()
    
    # 分析反馈评分
    feedback_list = [feedback_data]
    rating_analysis = analyzer.analyze_feedback_ratings(feedback_list)
    print(f"\n评分分析结果: {rating_analysis}")
    
    # 分析会话交互
    session_list = [session_data]
    interaction_analysis = analyzer.analyze_session_interactions(session_list)
    print(f"交互分析结果: {interaction_analysis}")


if __name__ == "__main__":
    main()