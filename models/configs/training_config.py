"""
训练配置模块
定义模型微调的配置参数
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class TrainingConfig:
    """训练配置类"""
    
    # 基础配置
    model_name: str = "qwen1.5-7b-chat"
    model_type: str = "qwen"
    model_path: str = "Qwen/Qwen1.5-7B-Chat"
    
    # 数据配置
    train_data_path: str = "data/datasets/train.json"
    val_data_path: Optional[str] = None
    test_data_path: Optional[str] = None
    max_seq_length: int = 2048
    padding_strategy: str = "max_length"  # max_length, longest
    
    # 训练超参数
    num_train_epochs: int = 3
    per_device_train_batch_size: int = 4
    per_device_eval_batch_size: int = 4
    gradient_accumulation_steps: int = 8
    learning_rate: float = 5e-5
    weight_decay: float = 0.01
    warmup_ratio: float = 0.1
    warmup_steps: int = 0
    
    # 优化器配置
    optimizer: str = "adamw"
    adam_beta1: float = 0.9
    adam_beta2: float = 0.999
    adam_epsilon: float = 1e-8
    max_grad_norm: float = 1.0
    
    # 学习率调度
    lr_scheduler_type: str = "cosine"
    lr_scheduler_kwargs: Dict[str, Any] = field(default_factory=dict)
    
    # 量化配置
    quantization: bool = True
    quantization_bits: int = 8  # 4, 8
    
    # LoRA配置（如果使用LoRA微调）
    use_lora: bool = True
    lora_r: int = 8
    lora_alpha: int = 32
    lora_dropout: float = 0.1
    lora_target_modules: List[str] = field(default_factory=lambda: [
        "q_proj", "k_proj", "v_proj", "o_proj"
    ])
    
    # 评估配置
    evaluation_strategy: str = "steps"
    eval_steps: int = 500
    save_strategy: str = "steps"
    save_steps: int = 500
    save_total_limit: int = 3
    
    # 日志配置
    logging_strategy: str = "steps"
    logging_steps: int = 100
    report_to: str = "tensorboard"
    
    # 设备配置
    device: str = "cuda"
    fp16: bool = True
    bf16: bool = False
    
    # 其他配置
    seed: int = 42
    dataloader_num_workers: int = 4
    remove_unused_columns: bool = False
    load_best_model_at_end: bool = True
    metric_for_best_model: str = "eval_loss"
    greater_is_better: bool = False
    
    # 早停配置
    early_stopping_patience: int = 3
    early_stopping_threshold: float = 0.01
    
    # 输出配置
    output_dir: str = "models/finetuned/personal_companion"
    overwrite_output_dir: bool = True
    do_train: bool = True
    do_eval: bool = True
    do_predict: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "model_name": self.model_name,
            "model_type": self.model_type,
            "model_path": self.model_path,
            "train_data_path": self.train_data_path,
            "val_data_path": self.val_data_path,
            "test_data_path": self.test_data_path,
            "max_seq_length": self.max_seq_length,
            "padding_strategy": self.padding_strategy,
            "num_train_epochs": self.num_train_epochs,
            "per_device_train_batch_size": self.per_device_train_batch_size,
            "per_device_eval_batch_size": self.per_device_eval_batch_size,
            "gradient_accumulation_steps": self.gradient_accumulation_steps,
            "learning_rate": self.learning_rate,
            "weight_decay": self.weight_decay,
            "warmup_ratio": self.warmup_ratio,
            "warmup_steps": self.warmup_steps,
            "optimizer": self.optimizer,
            "adam_beta1": self.adam_beta1,
            "adam_beta2": self.adam_beta2,
            "adam_epsilon": self.adam_epsilon,
            "max_grad_norm": self.max_grad_norm,
            "lr_scheduler_type": self.lr_scheduler_type,
            "lr_scheduler_kwargs": self.lr_scheduler_kwargs,
            "quantization": self.quantization,
            "quantization_bits": self.quantization_bits,
            "use_lora": self.use_lora,
            "lora_r": self.lora_r,
            "lora_alpha": self.lora_alpha,
            "lora_dropout": self.lora_dropout,
            "lora_target_modules": self.lora_target_modules,
            "evaluation_strategy": self.evaluation_strategy,
            "eval_steps": self.eval_steps,
            "save_strategy": self.save_strategy,
            "save_steps": self.save_steps,
            "save_total_limit": self.save_total_limit,
            "logging_strategy": self.logging_strategy,
            "logging_steps": self.logging_steps,
            "report_to": self.report_to,
            "device": self.device,
            "fp16": self.fp16,
            "bf16": self.bf16,
            "seed": self.seed,
            "dataloader_num_workers": self.dataloader_num_workers,
            "remove_unused_columns": self.remove_unused_columns,
            "load_best_model_at_end": self.load_best_model_at_end,
            "metric_for_best_model": self.metric_for_best_model,
            "greater_is_better": self.greater_is_better,
            "early_stopping_patience": self.early_stopping_patience,
            "early_stopping_threshold": self.early_stopping_threshold,
            "output_dir": self.output_dir,
            "overwrite_output_dir": self.overwrite_output_dir,
            "do_train": self.do_train,
            "do_eval": self.do_eval,
            "do_predict": self.do_predict,
        }


# 默认配置实例
DEFAULT_TRAINING_CONFIG = TrainingConfig()

if __name__ == "__main__":
    # 测试代码
    config = TrainingConfig()
    print("默认训练配置:")
    for key, value in config.to_dict().items():
        print(f"  {key}: {value}")