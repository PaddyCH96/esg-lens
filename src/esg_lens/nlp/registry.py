"""Load the CPU inference models once per process."""

from functools import cached_property

from esg_lens.config import settings

ESG_MODEL = "yiyanghkust/finbert-esg-9-categories"
SENTIMENT_MODEL = "ProsusAI/finbert"


class ModelRegistry:
    @cached_property
    def language(self):
        import spacy

        return spacy.load("en_core_web_sm")

    @cached_property
    def classifier(self):
        return self._pipeline(ESG_MODEL)

    @cached_property
    def sentiment(self):
        return self._pipeline(SENTIMENT_MODEL)

    @staticmethod
    def _pipeline(name):
        import torch
        from transformers import pipeline

        torch.set_num_threads(settings.TORCH_THREADS)
        return pipeline("text-classification", model=name, device=-1)

    def warm(self):
        _ = self.language, self.classifier, self.sentiment


model_registry = ModelRegistry()
