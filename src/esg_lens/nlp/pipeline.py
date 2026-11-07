"""Turn stored documents into versioned, auditable ESG signals."""

import json
import re
from datetime import datetime, timezone

from esg_lens.config import load_yaml
from esg_lens.scoring.weights import evidence_weight, polarity, recency_weight, scoring_weight


def signal_version(cfg):
    return f"{cfg.scoring['model_version']}:{cfg.config_hash}"


def source_tier(domain, sources):
    host = (domain or "").casefold().rstrip(".")
    for tier, definition in sources["tiers"].items():
        if any(host == item or host.endswith("." + item) for item in definition["domains"]):
            return tier
    return 3


def mentions(text, alias):
    return bool(re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", text, re.IGNORECASE))


class Controversies:
    def __init__(self, nlp, config):
        from spacy.matcher import PhraseMatcher

        self.matcher = PhraseMatcher(nlp.vocab, attr="LEMMA")
        self.severities = {}
        self.guard = config["negation_guard"]
        for tier, rules in config["tiers"].items():
            for phrase, parsed in zip(rules["triggers"], nlp.pipe(rules["triggers"])):
                key = f"trigger:{phrase}"
                self.matcher.add(key, [parsed])
                self.severities[key] = tier

    def match(self, doc):
        severity, terms = 0, set()
        for match, start, end in self.matcher(doc):
            key = doc.vocab.strings[match]
            context = doc[max(0, start - self.guard["window"]):start].text
            tier = self.severities[key]
            if any(mentions(context, phrase) for phrase in self.guard["phrases"]):
                tier -= 1
            severity = max(severity, tier)
            terms.add(doc[start:end].text.casefold())
        return severity, sorted(terms)


class SignalPipeline:
    def __init__(self, models, cfg):
        self.models = models
        self.cfg = cfg
        self.sources = load_yaml("sources")
        self.controversies = Controversies(models.language, load_yaml("controversy_lexicon"))
        self.categories = {name.casefold(): name for name in cfg.category_to_pillar}

    def _chunks(self, title, parsed):
        # Keep complete sentences where possible, then split unusually long sentences by tokens.
        tokenizer = self.models.classifier.tokenizer
        limit = min(tokenizer.model_max_length, 512) - tokenizer.num_special_tokens_to_add()
        texts = [sentence.text.strip() for sentence in parsed.sents if sentence.text.strip()]
        if title and title not in texts:
            texts.insert(0, title)
        chunks = []
        current = ""
        for sentence in texts:
            tokens = tokenizer.encode(sentence, add_special_tokens=False)
            joined = f"{current} {sentence}".strip()
            if len(tokenizer.encode(joined, add_special_tokens=False)) <= limit:
                current = joined
                continue
            if current:
                chunks.append(current)
                current = ""
            if len(tokens) <= limit:
                current = sentence
            else:
                chunks.extend(tokenizer.decode(tokens[i:i + limit]) for i in range(0, len(tokens), limit))
        if current:
            chunks.append(current)
        return chunks

    def analyze(self, documents, aliases, now):
        results, chunks, owners = [], [], []
        texts = [re.sub(r"\s+", " ", row["body"] or row["title"] or "").strip() for row in documents]
        batch_size = self.cfg.scoring["batch_size"]
        for row, parsed in zip(documents, self.models.language.pipe(texts, batch_size=batch_size)):
            signal = {"document_id": row["id"], "ticker": row["ticker"],
                      "model_version": signal_version(self.cfg), "included": 0}
            results.append(signal)
            if row["language"] and row["language"].lower() != "en":
                signal["exclusion_reason"] = "unsupported_language"
                continue
            published = datetime.fromisoformat(row["published_at"]) if row["published_at"] else None
            if published and published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            if published is None or (now - published).total_seconds() / 86400 > self.cfg.max_age_days:
                signal["exclusion_reason"] = "too_old"
                continue
            entities = [entity.text for entity in parsed.ents if entity.label_ == "ORG"]
            signal["entities_json"] = json.dumps(entities)
            title = row["title"] or ""
            # The collector resolves EDGAR documents through the issuer's CIK.
            # Generic filing titles need not repeat the company's name.
            if row["source"] != "edgar" and not any(
                mentions(title, alias) or any(mentions(name, alias) for name in entities)
                for alias in aliases
            ):
                signal["exclusion_reason"] = "entity_mismatch"
                continue
            parts = self._chunks(title, parsed)
            if not parts:
                signal["exclusion_reason"] = "not_esg_relevant"
                continue
            severity, terms = self.controversies.match(parsed)
            signal.update(controversy_severity=severity, controversy_terms=json.dumps(terms))
            owners.extend([len(results) - 1] * len(parts))
            chunks.extend(parts)

        best = {}
        if chunks:
            predictions = self.models.classifier(chunks, truncation=True, batch_size=batch_size)
            for owner, chunk, prediction in zip(owners, chunks, predictions, strict=True):
                category = self.categories.get(prediction["label"].casefold())
                if category and (owner not in best or prediction["score"] > best[owner][1]):
                    best[owner] = (category, prediction["score"], chunk)

        selected = [i for i, (_, score, _) in best.items() if score >= self.cfg.tau]
        sentiments = self.models.sentiment(
            [best[i][2] for i in selected], truncation=True, top_k=None, batch_size=batch_size
        ) if selected else []
        for i, distribution in zip(selected, sentiments, strict=True):
            row, signal = documents[i], results[i]
            category, relevance, _ = best[i]
            probs = {item["label"].casefold(): item["score"] for item in distribution}
            sentiment = probs["positive"] - probs["negative"]
            published = datetime.fromisoformat(row["published_at"])
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            age = max(0.0, (now - published).total_seconds() / 86400)
            tier = source_tier(row["domain"], self.sources)
            source_weights = {"edgar": self.sources["filing"]["weight"],
                              "sec_litigation": self.sources["sec_litigation"]["weight"]}
            source_weight = source_weights.get(row["source"], self.sources["tiers"][tier]["weight"])
            recent = recency_weight(age, self.cfg.half_life[row["doc_type"]])
            materiality = self.cfg.category_weights[category]
            evidence = evidence_weight(source_weight, recent, relevance)
            signal.update(
                included=1, pillar=self.cfg.category_to_pillar[category], category=category,
                relevance=relevance, sentiment_score=sentiment,
                sentiment_label=max(probs, key=probs.get),
                polarity=polarity(sentiment, signal["controversy_severity"], self.cfg),
                weight_source=source_weight, weight_recency=recent, weight_category=materiality,
                weight_evidence=evidence, weight_total=scoring_weight(evidence, materiality),
            )
        for signal in results:
            if not signal["included"]:
                signal.setdefault("exclusion_reason", "not_esg_relevant")
        return results

    def persist(self, conn, documents, aliases, now):
        version = signal_version(self.cfg)
        pending = [row for row in documents if not conn.execute(
            "SELECT 1 FROM esg_signals WHERE document_id = ? AND model_version = ?",
            (row["id"], version),
        ).fetchone()]
        with conn:
            for offset in range(0, len(pending), self.cfg.scoring["batch_size"]):
                for signal in self.analyze(pending[offset:offset + self.cfg.scoring["batch_size"]], aliases, now):
                    columns = ", ".join(signal)
                    values = ", ".join("?" for _ in signal)
                    conn.execute(
                        f"INSERT INTO esg_signals ({columns}) VALUES ({values}) "
                        "ON CONFLICT(document_id, model_version) DO NOTHING", tuple(signal.values())
                    )
