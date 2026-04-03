"""
Retrieval Configuration Loader - Phase 3: Domain-Agnostic Query Intelligence
Lädt und verwaltet retrieval.yaml Config für RAG-System.
"""
from __future__ import annotations
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

# Config-Pfad relativ zum Modul
CONFIG_PATH = Path(__file__).parent.parent / "config" / "retrieval.yaml"


@dataclass
class BM25Config:
    """BM25 Keyword Search Configuration"""
    use_german_stemming: bool = True
    priority_terms: list[str] = field(default_factory=list)
    coverage_weight: float = 2.0
    idf_weight: float = 0.75
    priority_boost: float = 6.0
    min_token_length: int = 10


@dataclass
class SemanticConfig:
    """Semantic Search Configuration"""
    model: str = "text-embedding-3-small"
    discovery_threshold_multiplier: float = 0.35
    min_discovery_threshold: float = 0.15
    min_similarity: float = 0.5


@dataclass
class HybridConfig:
    """Hybrid Search (RRF) Configuration"""
    rrf_k: int = 60
    max_chunks_per_document: int | None = None  # None = berechnet als limit // 2
    guaranteed_doc_threshold: float = 0.10
    pflichtenheft_fallback: bool = True
    pflichtenheft_min_score: float = 0.10


@dataclass
class QueryConfig:
    """Query Intelligence Configuration"""
    enable_distillation: bool = True
    distillation_provider: str = "openai"
    distillation_model: str = "gpt-4o-mini"
    distillation_temperature: float = 0.0
    distillation_max_tokens: int = 100
    enable_multi_hypothesis: bool = False
    hypothesis_count: int = 3
    strategies: list[str] = field(default_factory=lambda: ["distillation"])


@dataclass
class FilenameBoostConfig:
    """Filename Boosting Configuration"""
    enable: bool = True
    boost_amount: float = 0.10
    min_word_length: int = 3
    min_prefix_match: int = 5


@dataclass
class CacheConfig:
    """Caching Configuration"""
    enable: bool = True
    max_size: int = 100


@dataclass
class RetrievalConfig:
    """Complete Retrieval Configuration"""
    bm25: BM25Config
    semantic: SemanticConfig
    hybrid: HybridConfig
    query: QueryConfig
    filename_boost: FilenameBoostConfig
    cache: CacheConfig
    
    @classmethod
    def from_yaml(cls, path: Path | str | None = None) -> 'RetrievalConfig':
        """
        Lädt Config aus YAML-Datei.
        
        Args:
            path: Optionaler Pfad zur Config-Datei. Nutzt CONFIG_PATH wenn None.
        
        Returns:
            RetrievalConfig mit geladenen Werten (oder Defaults bei Fehler)
        """
        config_path = Path(path) if path else CONFIG_PATH
        
        try:
            if not config_path.exists():
                print(f"⚠️ Config nicht gefunden: {config_path} - nutze Defaults")
                return cls._defaults()
            
            with open(config_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            
            if not data:
                print(f"⚠️ Leere Config-Datei: {config_path} - nutze Defaults")
                return cls._defaults()
            
            # BM25 Config
            bm25_data = data.get('bm25', {})
            bm25 = BM25Config(
                use_german_stemming=bm25_data.get('use_german_stemming', True),
                priority_terms=bm25_data.get('priority_terms', []),
                coverage_weight=bm25_data.get('scoring', {}).get('coverage_weight', 2.0),
                idf_weight=bm25_data.get('scoring', {}).get('idf_weight', 0.75),
                priority_boost=bm25_data.get('scoring', {}).get('priority_boost', 6.0),
                min_token_length=bm25_data.get('scoring', {}).get('min_token_length', 10),
            )
            
            # Semantic Config
            sem_data = data.get('semantic', {})
            semantic = SemanticConfig(
                model=sem_data.get('model', 'text-embedding-3-small'),
                discovery_threshold_multiplier=sem_data.get('discovery_threshold_multiplier', 0.35),
                min_discovery_threshold=sem_data.get('min_discovery_threshold', 0.15),
                min_similarity=sem_data.get('min_similarity', 0.5),
            )
            
            # Hybrid Config
            hyb_data = data.get('hybrid', {})
            hybrid = HybridConfig(
                rrf_k=hyb_data.get('rrf_k', 60),
                max_chunks_per_document=hyb_data.get('max_chunks_per_document'),
                guaranteed_doc_threshold=hyb_data.get('guaranteed_doc_threshold', 0.10),
                pflichtenheft_fallback=hyb_data.get('pflichtenheft_fallback', True),
                pflichtenheft_min_score=hyb_data.get('pflichtenheft_min_score', 0.10),
            )
            
            # Query Config
            q_data = data.get('query', {})
            query = QueryConfig(
                enable_distillation=q_data.get('enable_distillation', True),
                distillation_provider=q_data.get('distillation_provider', 'openai'),
                distillation_model=q_data.get('distillation_model', 'gpt-4o-mini'),
                distillation_temperature=q_data.get('distillation_temperature', 0.0),
                distillation_max_tokens=q_data.get('distillation_max_tokens', 100),
                enable_multi_hypothesis=q_data.get('enable_multi_hypothesis', False),
                hypothesis_count=q_data.get('hypothesis_count', 3),
                strategies=q_data.get('strategies', ['distillation']),
            )
            
            # Filename Boost Config
            fb_data = data.get('filename_boost', {})
            filename_boost = FilenameBoostConfig(
                enable=fb_data.get('enable', True),
                boost_amount=fb_data.get('boost_amount', 0.10),
                min_word_length=fb_data.get('min_word_length', 3),
                min_prefix_match=fb_data.get('min_prefix_match', 5),
            )
            
            # Cache Config
            c_data = data.get('cache', {})
            cache = CacheConfig(
                enable=c_data.get('enable', True),
                max_size=c_data.get('max_size', 100),
            )
            
            return cls(
                bm25=bm25,
                semantic=semantic,
                hybrid=hybrid,
                query=query,
                filename_boost=filename_boost,
                cache=cache,
            )
            
        except Exception as e:
            print(f"❌ Fehler beim Laden der Config: {e} - nutze Defaults")
            return cls._defaults()
    
    @classmethod
    def _defaults(cls) -> 'RetrievalConfig':
        """Erstellt Config mit Default-Werten (Fallback bei Config-Fehler)"""
        return cls(
            bm25=BM25Config(),
            semantic=SemanticConfig(),
            hybrid=HybridConfig(),
            query=QueryConfig(),
            filename_boost=FilenameBoostConfig(),
            cache=CacheConfig(),
        )


# Global Config Instance (Singleton-Pattern)
_CONFIG: RetrievalConfig | None = None


def get_retrieval_config(reload: bool = False) -> RetrievalConfig:
    """
    Gibt globale Retrieval-Config zurück (lazy loaded).
    
    Args:
        reload: Wenn True, Config neu laden (für Config-Änderungen zur Laufzeit)
    
    Returns:
        RetrievalConfig Instance
    """
    global _CONFIG
    if _CONFIG is None or reload:
        _CONFIG = RetrievalConfig.from_yaml()
    return _CONFIG
