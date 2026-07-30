"""
训练流水线模块
管理端到端的模型微调和迭代优化流程
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import asdict

from src.preprocessing.data_parser import ChatDataParser
from src.preprocessing.preprocessor import ChatPreprocessor
from src.preprocessing.style_analyzer import StyleAnalyzer
from src.training.fine_tuner import FineTuner
from models.configs.training_config import TrainingConfig, DEFAULT_TRAINING_CONFIG

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TrainingPipeline:
    """训练流水线"""
    
    def __init__(self, config: Optional[TrainingConfig] = None):
        """
        初始化训练流水线
        
        Args:
            config: 训练配置对象
        """
        self.config = config or DEFAULT_TRAINING_CONFIG
        self.pipeline_history: List[Dict[str, Any]] = []
        
    def run_full_pipeline(self, chat_data_path: str, output_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        运行完整的训练流水线
        
        Args:
            chat_data_path: 聊天数据文件路径
            output_dir: 输出目录，如果为None则使用配置中的目录
            
        Returns:
            训练结果字典
        """
        logger.info("开始运行完整的训练流水线")
        
        # 记录流水线开始时间
        start_time = datetime.now()
        
        try:
            # 1. 数据解析
            parsed_data = self._parse_data(chat_data_path)
            
            # 2. 数据预处理
            processed_data = self._preprocess_data(parsed_data)
            
            # 3. 风格分析
            style_profile = self._analyze_style(processed_data)
            
            # 4. 准备训练数据
            train_data_path = self._prepare_training_data(processed_data, output_dir)
            
            # 5. 更新训练配置
            if output_dir:
                self.config.output_dir = output_dir
                
            # 6. 模型微调
            training_metrics = self._fine_tune_model(train_data_path)
            
            # 7. 保存风格画像
            style_profile_path = self._save_style_profile(style_profile, output_dir)
            
            # 8. 记录流水线历史
            pipeline_result = {
                "pipeline_id": f"pipeline_{start_time.strftime('%Y%m%d_%H%M%S')}",
                "start_time": start_time.isoformat(),
                "end_time": datetime.now().isoformat(),
                "duration": (datetime.now() - start_time).total_seconds(),
                "chat_data_path": chat_data_path,
                "output_dir": self.config.output_dir,
                "training_metrics": training_metrics,
                "style_profile_path": style_profile_path,
                "status": "success"
            }
            
            self.pipeline_history.append(pipeline_result)
            
            logger.info("训练流水线运行完成")
            return pipeline_result
            
        except Exception as e:
            error_result = {
                "pipeline_id": f"pipeline_{start_time.strftime('%Y%m%d_%H%M%S')}",
                "start_time": start_time.isoformat(),
                "end_time": datetime.now().isoformat(),
                "duration": (datetime.now() - start_time).total_seconds(),
                "chat_data_path": chat_data_path,
                "output_dir": self.config.output_dir,
                "error": str(e),
                "status": "failed"
            }
            
            self.pipeline_history.append(error_result)
            logger.error(f"训练流水线运行失败: {e}")
            raise
    
    def _parse_data(self, chat_data_path: str) -> List[Dict[str, Any]]:
        """
        解析聊天数据
        
        Args:
            chat_data_path: 聊天数据文件路径
            
        Returns:
            解析后的数据列表
        """
        logger.info(f"正在解析聊天数据: {chat_data_path}")
        
        parser = ChatDataParser()
        parsed_data = parser.parse_chat_data(chat_data_path)
        
        logger.info(f"数据解析完成，共 {len(parsed_data)} 条记录")
        return parsed_data
    
    def _preprocess_data(self, parsed_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        预处理数据
        
        Args:
            parsed_data: 解析后的数据
            
        Returns:
            预处理后的数据
        """
        logger.info("正在进行数据预处理")
        
        preprocessor = ChatPreprocessor()
        processed_data = preprocessor.preprocess_conversation(parsed_data)
        
        logger.info(f"数据预处理完成，共 {len(processed_data)} 条有效记录")
        return processed_data
    
    def _analyze_style(self, processed_data: List[Dict[str, Any]]):
        """
        分析风格特征
        
        Args:
            processed_data: 预处理后的数据
            
        Returns:
            风格画像对象
        """
        logger.info("正在进行风格分析")
        
        analyzer = StyleAnalyzer()
        style_profile = analyzer.generate_style_profile(processed_data)
        
        logger.info("风格分析完成")
        return style_profile
    
    def _prepare_training_data(self, processed_data: List[Dict[str, Any]], 
                             output_dir: Optional[str] = None) -> str:
        """
        准备训练数据
        
        Args:
            processed_data: 预处理后的数据
            output_dir: 输出目录
            
        Returns:
            训练数据文件路径
        """
        if output_dir is None:
            output_dir = self.config.output_dir
            
        # 确保输出目录存在
        os.makedirs(output_dir, exist_ok=True)
        
        # 创建训练数据目录
        train_data_dir = os.path.join(output_dir, "training_data")
        os.makedirs(train_data_dir, exist_ok=True)
        
        # 创建训练对
        preprocessor = ChatPreprocessor()
        training_pairs = preprocessor.create_training_pairs(
            [item['original'] for item in processed_data],
            context_window=5
        )
        
        # 转换为训练格式
        training_data = []
        for context, response in training_pairs:
            training_data.append({
                "context": context,
                "response": response
            })
        
        # 保存训练数据
        train_data_path = os.path.join(train_data_dir, "train.json")
        with open(train_data_path, 'w', encoding='utf-8') as f:
            json.dump(training_data, f, ensure_ascii=False, indent=2)
            
        # 更新配置
        self.config.train_data_path = train_data_path
        
        logger.info(f"训练数据准备完成，共 {len(training_data)} 个训练样本")
        logger.info(f"训练数据已保存到: {train_data_path}")
        
        return train_data_path
    
    def _fine_tune_model(self, train_data_path: str) -> Dict[str, Any]:
        """
        微调模型
        
        Args:
            train_data_path: 训练数据文件路径
            
        Returns:
            训练指标字典
        """
        logger.info("正在进行模型微调")
        
        # 创建微调器（概念性实现）
        tuner = FineTuner(self.config)
        
        # 准备模型和分词器
        tuner.prepare_model_and_tokenizer()
        
        # 准备数据集
        tuner.prepare_datasets()
        
        # 设置训练器
        tuner.setup_trainer()
        
        # 开始训练
        metrics = tuner.train()
        
        # 保存配置
        tuner.save_config()
        
        logger.info("模型微调完成")
        return metrics
    
    def _save_style_profile(self, style_profile: Dict[str, Any], 
                          output_dir: Optional[str] = None) -> str:
        """
        保存风格画像
        
        Args:
            style_profile: 风格画像字典
            output_dir: 输出目录
            
        Returns:
            风格画像文件路径
        """
        if output_dir is None:
            output_dir = self.config.output_dir
            
        # 确保输出目录存在
        os.makedirs(output_dir, exist_ok=True)
        
        # 保存风格画像
        style_profile_path = os.path.join(output_dir, "style_profile.json")
        with open(style_profile_path, 'w', encoding='utf-8') as f:
            json.dump(style_profile, f, ensure_ascii=False, indent=2)
            
        logger.info(f"风格画像已保存到: {style_profile_path}")
        return style_profile_path
    
    def run_iteration_pipeline(self, chat_data_path: str, base_model_path: str,
                             iteration_name: str) -> Dict[str, Any]:
        """
        运行迭代训练流水线（基于已有模型进行进一步微调）
        
        Args:
            chat_data_path: 新的聊天数据文件路径
            base_model_path: 基础模型路径
            iteration_name: 迭代名称
            
        Returns:
            训练结果字典
        """
        logger.info(f"开始运行迭代训练流水线: {iteration_name}")
        
        # 更新配置使用基础模型
        original_model_path = self.config.model_path
        self.config.model_path = base_model_path
        
        # 设置迭代输出目录
        iteration_output_dir = os.path.join(self.config.output_dir, f"iteration_{iteration_name}")
        
        try:
            # 运行完整流水线
            result = self.run_full_pipeline(chat_data_path, iteration_output_dir)
            result["iteration_name"] = iteration_name
            result["base_model_path"] = base_model_path
            
            logger.info(f"迭代训练完成: {iteration_name}")
            return result
            
        finally:
            # 恢复原始模型路径
            self.config.model_path = original_model_path
    
    def compare_iterations(self, iteration_ids: List[str]) -> Dict[str, Any]:
        """
        比较不同迭代的结果
        
        Args:
            iteration_ids: 迭代ID列表
            
        Returns:
            比较结果字典
        """
        iterations = [h for h in self.pipeline_history if h.get("iteration_name") in iteration_ids]
        
        comparison = {
            "iterations": iterations,
            "comparison_metrics": {}
        }
        
        # 比较关键指标
        if len(iterations) >= 2:
            # 简单比较最后两个迭代
            last_iter = iterations[-1]
            prev_iter = iterations[-2]
            
            comparison["comparison_metrics"] = {
                "loss_improvement": prev_iter.get("training_metrics", {}).get("train_loss", 0) - 
                                  last_iter.get("training_metrics", {}).get("train_loss", 0),
                "training_time_diff": last_iter.get("duration", 0) - prev_iter.get("duration", 0)
            }
        
        return comparison
    
    def get_pipeline_history(self) -> List[Dict[str, Any]]:
        """
        获取流水线历史记录
        
        Returns:
            流水线历史记录列表
        """
        return self.pipeline_history
    
    def export_pipeline_report(self, output_path: str):
        """
        导出流水线报告
        
        Args:
            output_path: 输出文件路径
        """
        report = {
            "generated_at": datetime.now().isoformat(),
            "pipeline_history": self.pipeline_history,
            "summary": {
                "total_pipelines": len(self.pipeline_history),
                "successful_pipelines": len([h for h in self.pipeline_history if h.get("status") == "success"]),
                "failed_pipelines": len([h for h in self.pipeline_history if h.get("status") == "failed"])
            }
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            
        logger.info(f"流水线报告已导出到: {output_path}")


class FeedbackIntegrationPipeline:
    """反馈集成流水线"""
    
    def __init__(self, training_pipeline: TrainingPipeline):
        """
        初始化反馈集成流水线
        
        Args:
            training_pipeline: 训练流水线对象
        """
        self.training_pipeline = training_pipeline
        
    def integrate_user_feedback(self, feedback_data_path: str, 
                             base_model_path: str,
                             iteration_name: str) -> Dict[str, Any]:
        """
        集成用户反馈数据进行迭代训练
        
        Args:
            feedback_data_path: 用户反馈数据路径
            base_model_path: 基础模型路径
            iteration_name: 迭代名称
            
        Returns:
            训练结果字典
        """
        logger.info(f"开始集成用户反馈进行迭代训练: {iteration_name}")
        
        # 这里可以添加对反馈数据的特殊处理
        # 例如：加权处理、错误样本重点训练等
        
        # 直接运行迭代训练
        result = self.training_pipeline.run_iteration_pipeline(
            feedback_data_path, 
            base_model_path, 
            iteration_name
        )
        
        logger.info(f"反馈集成训练完成: {iteration_name}")
        return result


def main():
    """主函数 - 使用示例"""
    logger.info("训练流水线模块演示")
    
    # 创建训练流水线
    pipeline = TrainingPipeline()
    
    # 创建示例聊天数据文件（如果不存在）
    sample_data_path = "data/raw/sample_chat.json"
    os.makedirs(os.path.dirname(sample_data_path), exist_ok=True)
    
    if not os.path.exists(sample_data_path):
        sample_data = [
            {"timestamp": "2023-01-01 10:00:00", "sender": "user", "message": "你好呀！今天天气真不错"},
            {"timestamp": "2023-01-01 10:00:30", "sender": "assistant", "message": "你好！是呀，很适合出去走走呢😊"},
            {"timestamp": "2023-01-01 10:01:00", "sender": "user", "message": "你在干嘛呢？"},
            {"timestamp": "2023-01-01 10:01:15", "sender": "assistant", "message": "在看书呢，你呢？"},
            {"timestamp": "2023-01-01 10:01:30", "sender": "user", "message": "我刚看完一本小说，讲的是AI的故事"},
            {"timestamp": "2023-01-01 10:02:00", "sender": "assistant", "message": "哇，听起来很有趣！能跟我讲讲吗？"}
        ]
        
        with open(sample_data_path, 'w', encoding='utf-8') as f:
            json.dump(sample_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"示例数据已创建: {sample_data_path}")
    
    # 运行完整流水线（概念性演示）
    logger.info("以下是训练流水线的概念性演示")
    logger.warning("实际运行需要安装依赖并提供真实数据")
    
    # 概念性演示流程：
    # 1. 运行完整训练流水线
    # result = pipeline.run_full_pipeline(sample_data_path, "models/finetuned/demo_run")
    # 
    # 2. 查看结果
    # print(f"训练结果: {result}")
    # 
    # 3. 导出报告
    # pipeline.export_pipeline_report("reports/pipeline_report.json")


if __name__ == "__main__":
    main()