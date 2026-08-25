import sys
sys.path.insert(0, '.')

from src.m03_db import init_db, get_session, DocumentChunk, Document, ProjectDocumentLink
from sqlmodel import select
from rank_bm25 import BM25Okapi
import spacy
from src.m09_rag import _german_stem

# Setup
init_db()
nlp = spacy.load('de_core_news_sm')

STOPWORDS = {'der', 'die', 'das', 'den', 'dem', 'ist', 'sind', 'war', 'waren', 'nicht', 'ein', 'eine'}

def tokenize(text):
    doc = nlp(text[:5000])
    tokens = []
    for token in doc:
        if not token.is_alpha or len(token.lemma_) < 3:
            continue
        lemma = token.lemma_.lower()
        if lemma in STOPWORDS or token.is_stop:
            continue
        stemmed = _german_stem(lemma)
        tokens.append(stemmed)
    return tokens

# Lade Chunks
project_key = '<YOUR_PROJECT_KEY>'  # Ersetze mit dem gewünschten project_key aus der DB

with get_session() as ses:
    doc_query = select(DocumentChunk).join(Document).where(Document.is_deleted == False)
    doc_query = doc_query.join(ProjectDocumentLink, ProjectDocumentLink.document_id == Document.id).where(ProjectDocumentLink.project_key == project_key)
    doc_query = doc_query.where(Document.classification != 'FAQ/Fragen-Katalog')
    chunks = list(ses.exec(doc_query).all())
    
    # Tokenisiere alle Chunks
    tokenized_corpus = []
    chunk_metadata = []
    for chunk in chunks:
        tokens = tokenize(chunk.chunk_text or '')
        if tokens:
            tokenized_corpus.append(tokens)
            chunk_metadata.append((chunk.id, tokens))
    
    # Finde Chunk 939 im Korpus
    idx939 = None
    for i, (cid, toks) in enumerate(chunk_metadata):
        if cid == 939:
            idx939 = i
            print(f'Chunk 939 Index: {i}')
            print(f'Tokens: {toks}')
            print()
            break
    
    # BM25 erstellen
    bm25 = BM25Okapi(tokenized_corpus)
    
    # Query tokenisieren
    query_tokens = ['subunternehm']
    print(f'Query Tokens: {query_tokens}')
    
    # Scores berechnen
    scores = bm25.get_scores(query_tokens)
    
    if idx939 is not None:
        score939 = scores[idx939]
        print(f'Score für Chunk 939: {score939}')
        if score939 > 0:
            print('OK: Score ist positiv!')
        else:
            print('FEHLER: Score ist 0!')
    
    # Zeige Top-5 Scores
    print()
    print('Top-5 Scores:')
    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:5]
    for i in top_indices:
        cid, toks = chunk_metadata[i]
        print(f'  Chunk {cid}: score={scores[i]:.3f}')
