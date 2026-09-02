import re
import spacy
import logging
from typing import List

logger = logging.getLogger(__name__)

class TextCleaner:
    """
    Standardizes text for NLP processing.
    Handles boilerplate removal, sentence splitting, and normalization.
    """
    def __init__(self):
        try:
            self.nlp = spacy.load("en_core_web_sm")
        except OSError:
            logger.warning("spacy model en_core_web_sm not found. Please run 'python -m spacy download en_core_web_sm'")
            self.nlp = None

    def clean_boilerplate(self, text: str) -> str:
        """Removes common news/filing boilerplate."""
        # Simple regex for common noise (e.g., "Read more at...", "Follow us on...")
        patterns = [
            r"Read more at.*",
            r"Follow us on.*",
            r"Copyright \d{4}.*",
            r"All rights reserved.*"
        ]
        for pattern in patterns:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)
        return text.strip()

    def split_into_sentences(self, text: str) -> List[str]:
        """Splits document into sentences using spaCy's sentencizer."""
        if not self.nlp:
            # Fallback to simple split if spacy failed to load
            return [s.strip() for s in text.split(". ") if s.strip()]
        
        doc = self.nlp(text)
        return [sent.text.strip() for sent in doc.sents if len(sent.text.strip()) > 20]

    def normalize(self, text: str) -> str:
        """Standardizes whitespace and case."""
        text = re.sub(r"\s+", " ", text)
        return text.strip()

# Singleton instance
text_cleaner = TextCleaner()
