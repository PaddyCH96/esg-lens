import torch
from transformers import pipeline, AutoTokenizer, AutoModelForSequenceClassification
import logging
from typing import Optional, Any
from src.esg_lens.config import settings

logger = logging.getLogger(__name__)

class ModelRegistry:
    """
    Process-wide singleton registry for NLP models.
    Ensures models are loaded once and shared across all requests.
    """
    def __init__(self):
        self._models = {}
        self._tokenizers = {}
        self.device = -1 if not torch.cuda.is_available() else 0
        torch.set_num_threads(settings.TORCH_THREADS)

    def get_pipeline(self, task: str, model_name: str) -> Any:
        key = f"{task}_{model_name}"
        if key not in self._models:
            logger.info(f"Loading model {model_name} for task {task} on device {self.device}...")
            self._models[key] = pipeline(
                task, 
                model=model_name, 
                tokenizer=model_name, 
                device=self.device,
                batch_size=16
            )
        return self._models[key]

    def get_tokenizer(self, model_name: str) -> Any:
        if model_name not in self._tokenizers:
            self._tokenizers[model_name] = AutoTokenizer.from_pretrained(model_name)
        return self._tokenizers[model_name]

    def clear(self):
        self._models.clear()
        self._tokenizers.clear()

# Singleton instance
model_registry = ModelRegistry()
