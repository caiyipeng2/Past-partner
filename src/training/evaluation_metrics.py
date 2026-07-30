"""
评估指标模块
定义和计算模型微调的评估指标
"""

import logging
from typing import List, Dict, Any, Union, Optional
from dataclasses import dataclass
import numpy as np

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 注意：此类需要安装scikit-learn库才能使用真实的BLEU计算
# pip install scikit-learn


@dataclass
class EvaluationMetrics:
    """评估指标数据类"""
    # 基础指标
    loss: float = 0.0
    perplexity: float = 0.0
    
    # 生成质量指标
    bleu_score: float = 0.0
    rouge_1: float = 0.0
    rouge_2: float = 0.0
    rouge_l: float = 0.0
    
    # 风格一致性指标
    style_consistency: float = 0.0
    emotional_alignment: float = 0.0
    length_similarity: float = 0.0
    
    # 上下文相关指标
    context_coherence: float = 0.0
    context_recall: float = 0.0
    
    # 效率指标
    inference_time: float = 0.0  # 平均推理时间（秒）
    tokens_per_second: float = 0.0
    
    def to_dict(self) -> Dict[str, float]:
        """转换为字典格式"""
        return {
            "loss": self.loss,
            "perplexity": self.perplexity,
            "bleu_score": self.bleu_score,
            "rouge_1": self.rouge_1,
            "rouge_2": self.rouge_2,
            "rouge_l": self.rouge_l,
            "style_consistency": self.style_consistency,
            "emotional_alignment": self.emotional_alignment,
            "length_similarity": self.length_similarity,
            "context_coherence": self.context_coherence,
            "context_recall": self.context_recall,
            "inference_time": self.inference_time,
            "tokens_per_second": self.tokens_per_second
        }


class MetricsCalculator:
    """指标计算器"""
    
    def __init__(self):
        pass
    
    def calculate_basic_metrics(self, predictions: List[str], 
                             references: List[str]) -> Dict[str, float]:
        """
        计算基础指标
        
        Args:
            predictions: 预测文本列表
            references: 参考文本列表
            
        Returns:
            基础指标字典
        """
        metrics = {}
        
        # 简化的BLEU分数计算（概念性实现）
        try:
            # 这里应该使用真实的BLEU实现
            # from sklearn.metrics import bleu_score
            # 但由于依赖问题，我们使用简化实现
            
            bleu_scores = []
            for pred, ref in zip(predictions, references):
                # 简单的n-gram重叠计算
                pred_words = pred.split()
                ref_words = ref.split()
                
                # 计算1-gram精度
                if len(pred_words) > 0:
                    overlap = len(set(pred_words) & set(ref_words))
                    precision = overlap / len(pred_words)
                    bleu_scores.append(precision)
                else:
                    bleu_scores.append(0.0)
            
            metrics["bleu_1"] = sum(bleu_scores) / len(bleu_scores) if bleu_scores else 0.0
            
        except Exception as e:
            logger.warning(f"计算BLEU分数时出错: {e}")
            metrics["bleu_1"] = 0.0
        
        return metrics
    
    def calculate_rouge_metrics(self, predictions: List[str], 
                             references: List[str]) -> Dict[str, float]:
        """
        计算ROUGE指标（简化实现）
        
        Args:
            predictions: 预测文本列表
            references: 参考文本列表
            
        Returns:
            ROUGE指标字典
        """
        # 简化实现，仅计算n-gram重叠
        rouge_metrics = {
            "rouge_1": 0.0,
            "rouge_2": 0.0,
            "rouge_l": 0.0
        }
        
        if not predictions or not references or len(predictions) != len(references):
            return rouge_metrics
        
        total_rouge_1 = 0.0
        total_rouge_2 = 0.0
        total_rouge_l = 0.0
        count = len(predictions)
        
        for pred, ref in zip(predictions, references):
            # 计算ROUGE-1 (unigram)
            pred_words = set(pred.split())
            ref_words = set(ref.split())
            overlap_1 = len(pred_words.intersection(ref_words))
            total_words_1 = len(pred_words.union(ref_words))
            rouge_1 = overlap_1 / total_words_1 if total_words_1 > 0 else 0.0
            total_rouge_1 += rouge_1
            
            # 计算ROUGE-2 (bigram)
            pred_bigrams = set(zip(pred.split(), pred.split()[1:]))
            ref_bigrams = set(zip(ref.split(), ref.split()[1:]))
            overlap_2 = len(pred_bigrams.intersection(ref_bigrams))
            total_words_2 = len(pred_bigrams.union(ref_bigrams))
            rouge_2 = overlap_2 / total_words_2 if total_words_2 > 0 else 0.0
            total_rouge_2 += rouge_2
            
            # 计算ROUGE-L (最长公共子序列)
            rouge_l = self._calculate_lcs_similarity(pred, ref)
            total_rouge_l += rouge_l
        
        rouge_metrics["rouge_1"] = total_rouge_1 / count if count > 0 else 0.0
        rouge_metrics["rouge_2"] = total_rouge_2 / count if count > 0 else 0.0
        rouge_metrics["rouge_l"] = total_rouge_l / count if count > 0 else 0.0
        
        return rouge_metrics
    
    def _calculate_lcs_similarity(self, pred: str, ref: str) -> float:
        """
        计算最长公共子序列相似度
        
        Args:
            pred: 预测文本
            ref: 参考文本
            
        Returns:
            LCS相似度
        """
        pred_words = pred.split()
        ref_words = ref.split()
        
        # 创建DP表
        dp = [[0] * (len(ref_words) + 1) for _ in range(len(pred_words) + 1)]
        
        # 填充DP表
        for i in range(1, len(pred_words) + 1):
            for j in range(1, len(ref_words) + 1):
                if pred_words[i-1] == ref_words[j-1]:
                    dp[i][j] = dp[i-1][j-1] + 1
                else:
                    dp[i][j] = max(dp[i-1][j], dp[i][j-1])
        
        # 计算相似度
        lcs_length = dp[len(pred_words)][len(ref_words)]
        total_length = len(pred_words) + len(ref_words)
        
        return (2 * lcs_length) / total_length if total_length > 0 else 0.0
    
    def calculate_style_metrics(self, predictions: List[str], 
                             style_profile: Dict[str, Any]) -> Dict[str, float]:
        """
        计算风格一致性指标
        
        Args:
            predictions: 预测文本列表
            style_profile: 风格画像
            
        Returns:
            风格指标字典
        """
        style_metrics = {
            "style_consistency": 0.0,
            "emotional_alignment": 0.0,
            "length_similarity": 0.0
        }
        
        if not predictions or not style_profile:
            return style_metrics
        
        # 从风格画像中提取参考特征
        ref_length = style_profile.get("message_length_distribution", {}).get("mean_length", 0)
        ref_emotion = style_profile.get("emotion_tendency", {})
        
        total_length_sim = 0.0
        total_emotion_align = 0.0
        count = len(predictions)
        
        # 简化的风格匹配计算
        for pred in predictions:
            # 长度相似度
            if ref_length > 0:
                length_sim = 1.0 - abs(len(pred) - ref_length) / ref_length
                length_sim = max(0.0, min(1.0, length_sim))  # 限制在[0,1]范围内
                total_length_sim += length_sim
            
            # 情绪对齐（简化实现）
            emotion_score = self._calculate_emotion_alignment(pred, ref_emotion)
            total_emotion_align += emotion_score
        
        style_metrics["length_similarity"] = total_length_sim / count if count > 0 else 0.0
        style_metrics["emotional_alignment"] = total_emotion_align / count if count > 0 else 0.0
        
        # 风格一致性（简化为长度和情绪的综合）
        style_metrics["style_consistency"] = (
            style_metrics["length_similarity"] * 0.5 + 
            style_metrics["emotional_alignment"] * 0.5
        )
        
        return style_metrics
    
    def _calculate_emotion_alignment(self, text: str, ref_emotion: Dict[str, int]) -> float:
        """
        计算情绪对齐度（简化实现）
        
        Args:
            text: 文本
            ref_emotion: 参考情绪分布
            
        Returns:
            情绪对齐度
        """
        # 简化的关键词匹配
        positive_keywords = ['开心', '高兴', '喜欢', '爱', '棒', '好', '不错', '赞', '😊', '😄', '👍']
        negative_keywords = ['难过', '生气', '讨厌', '烦', '差', '不好', '糟糕', '😔', '😞', '👎']
        
        positive_count = sum(1 for kw in positive_keywords if kw in text)
        negative_count = sum(1 for kw in negative_keywords if kw in text)
        
        # 确定文本的情绪倾向
        if positive_count > negative_count:
            text_emotion = 'positive'
        elif negative_count > positive_count:
            text_emotion = 'negative'
        else:
            text_emotion = 'neutral'
        
        # 计算与参考情绪的对齐度
        total_ref_emotions = sum(ref_emotion.values())
        if total_ref_emotions > 0:
            ref_emotion_prob = ref_emotion.get(text_emotion, 0) / total_ref_emotions
            return ref_emotion_prob
        
        return 0.5  # 默认值
    
    def calculate_context_metrics(self, predictions: List[str], 
                               contexts: List[List[str]]) -> Dict[str, float]:
        """
        计算上下文相关指标
        
        Args:
            predictions: 预测文本列表
            contexts: 上下文列表
            
        Returns:
            上下文指标字典
        """
        context_metrics = {
            "context_coherence": 0.0,
            "context_recall": 0.0
        }
        
        if not predictions or not contexts or len(predictions) != len(contexts):
            return context_metrics
        
        total_coherence = 0.0
        total_recall = 0.0
        count = len(predictions)
        
        for pred, context in zip(predictions, contexts):
            # 上下文连贯性（简化实现）
            coherence = self._calculate_context_coherence(pred, context)
            total_coherence += coherence
            
            # 上下文召回率（简化实现）
            recall = self._calculate_context_recall(pred, context)
            total_recall += recall
        
        context_metrics["context_coherence"] = total_coherence / count if count > 0 else 0.0
        context_metrics["context_recall"] = total_recall / count if count > 0 else 0.0
        
        return context_metrics
    
    def _calculate_context_coherence(self, prediction: str, context: List[str]) -> float:
        """
        计算上下文连贯性（简化实现）
        
        Args:
            prediction: 预测文本
            context: 上下文
            
        Returns:
            连贯性分数
        """
        if not context:
            return 1.0  # 没有上下文时认为完全连贯
        
        # 简单的关键词重叠计算
        pred_words = set(prediction.split())
        context_words = set()
        for ctx in context:
            context_words.update(ctx.split())
        
        if not context_words:
            return 1.0
            
        overlap = len(pred_words.intersection(context_words))
        union = len(pred_words.union(context_words))
        
        return overlap / union if union > 0 else 0.0
    
    def _calculate_context_recall(self, prediction: str, context: List[str]) -> float:
        """
        计算上下文召回率（简化实现）
        
        Args:
            prediction: 预测文本
            context: 上下文
            
        Returns:
            召回率分数
        """
        if not context:
            return 1.0  # 没有上下文时认为完全召回
        
        # 检查预测中是否包含了上下文的关键信息
        context_text = " ".join(context)
        context_keywords = set(context_text.split())
        pred_keywords = set(prediction.split())
        
        if not context_keywords:
            return 1.0
            
        overlap = len(pred_keywords.intersection(context_keywords))
        return overlap / len(context_keywords) if len(context_keywords) > 0 else 0.0
    
    def calculate_efficiency_metrics(self, inference_times: List[float], 
                                 token_counts: List[int]) -> Dict[str, float]:
        """
        计算效率指标
        
        Args:
            inference_times: 推理时间列表（秒）
            token_counts: token数量列表
            
        Returns:
            效率指标字典
        """
        efficiency_metrics = {
            "inference_time": 0.0,
            "tokens_per_second": 0.0
        }
        
        if not inference_times or not token_counts:
            return efficiency_metrics
        
        # 平均推理时间
        avg_inference_time = sum(inference_times) / len(inference_times)
        efficiency_metrics["inference_time"] = avg_inference_time
        
        # 平均tokens/s
        total_tokens = sum(token_counts)
        total_time = sum(inference_times)
        tokens_per_second = total_tokens / total_time if total_time > 0 else 0.0
        efficiency_metrics["tokens_per_second"] = tokens_per_second
        
        return efficiency_metrics


class ModelEvaluator:
    """模型评估器"""
    
    def __init__(self):
        self.metrics_calculator = MetricsCalculator()
    
    def evaluate_model(self, predictions: List[str], 
                     references: List[str],
                     contexts: List[List[str]],
                     inference_times: List[float],
                     token_counts: List[int],
                     style_profile: Optional[Dict[str, Any]] = None,
                     losses: Optional[List[float]] = None) -> EvaluationMetrics:
        """
        全面评估模型性能
        
        Args:
            predictions: 预测文本列表
            references: 参考文本列表
            contexts: 上下文列表
            inference_times: 推理时间列表
            token_counts: token数量列表
            style_profile: 风格画像
            losses: 损失值列表
            
        Returns:
            评估指标对象
        """
        metrics = EvaluationMetrics()
        
        # 计算基础指标
        if losses:
            metrics.loss = sum(losses) / len(losses)
            metrics.perplexity = np.exp(metrics.loss) if metrics.loss < 100 else float('inf')
        
        # 计算生成质量指标
        basic_metrics = self.metrics_calculator.calculate_basic_metrics(predictions, references)
        rouge_metrics = self.metrics_calculator.calculate_rouge_metrics(predictions, references)
        
        metrics.bleu_score = basic_metrics.get("bleu_1", 0.0)
        metrics.rouge_1 = rouge_metrics.get("rouge_1", 0.0)
        metrics.rouge_2 = rouge_metrics.get("rouge_2", 0.0)
        metrics.rouge_l = rouge_metrics.get("rouge_l", 0.0)
        
        # 计算风格一致性指标
        if style_profile:
            style_metrics = self.metrics_calculator.calculate_style_metrics(predictions, style_profile)
            metrics.style_consistency = style_metrics.get("style_consistency", 0.0)
            metrics.emotional_alignment = style_metrics.get("emotional_alignment", 0.0)
            metrics.length_similarity = style_metrics.get("length_similarity", 0.0)
        
        # 计算上下文相关指标
        context_metrics = self.metrics_calculator.calculate_context_metrics(predictions, contexts)
        metrics.context_coherence = context_metrics.get("context_coherence", 0.0)
        metrics.context_recall = context_metrics.get("context_recall", 0.0)
        
        # 计算效率指标
        efficiency_metrics = self.metrics_calculator.calculate_efficiency_metrics(
            inference_times, token_counts
        )
        metrics.inference_time = efficiency_metrics.get("inference_time", 0.0)
        metrics.tokens_per_second = efficiency_metrics.get("tokens_per_second", 0.0)
        
        return metrics


def main():
    """主函数 - 使用示例"""
    logger.info("评估指标模块演示")
    
    # 创建评估器
    evaluator = ModelEvaluator()
    
    # 示例数据
    predictions = [
        "你好！今天天气真不错😊",
        "在看书呢，你呢？",
        "哇，听起来很有趣！能跟我讲讲吗？"
    ]
    
    references = [
        "你好呀！今天天气真好呢",
        "我在看书，你干什么呢？",
        "听起来很有意思！可以分享一下吗？"
    ]
    
    contexts = [
        ["你好", "今天天气怎么样"],
        ["你好", "在做什么呢"],
        ["你好", "在做什么呢", "我刚看完一本书"]
    ]
    
    inference_times = [0.5, 0.3, 0.4]  # 秒
    token_counts = [10, 8, 15]  # token数量
    
    # 简化的风格画像
    style_profile = {
        "message_length_distribution": {
            "mean_length": 15.0
        },
        "emotion_tendency": {
            "positive": 60,
            "neutral": 30,
            "negative": 10
        }
    }
    
    # 执行评估
    metrics = evaluator.evaluate_model(
        predictions=predictions,
        references=references,
        contexts=contexts,
        inference_times=inference_times,
        token_counts=token_counts,
        style_profile=style_profile,
        losses=[0.3, 0.2, 0.25]
    )
    
    # 显示结果
    print("模型评估结果:")
    metrics_dict = metrics.to_dict()
    for key, value in metrics_dict.items():
        print(f"  {key}: {value:.4f}")


if __name__ == "__main__":
    main()