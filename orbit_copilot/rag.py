import math
import re
from collections import Counter
from sqlalchemy import select
from ..database import async_session
from ..models import Document

def _tokenize(text: str) -> list[str]:
    return re.findall(r'\b\w+\b', text.lower())

def _compute_tf(tokens: list[str]) -> dict:
    tf = {}
    count = len(tokens)
    for word, freq in Counter(tokens).items():
        tf[word] = freq / count
    return tf

async def query_documents(user_id: str, query: str, top_k: int = 2) -> list[dict]:
    """
    Search a user's uploaded documents using an on-the-fly TF-IDF algorithm.
    Returns the most relevant snippets for RAG context.
    """
    async with async_session() as db:
        result = await db.execute(select(Document).where(Document.user_id == user_id))
        docs = result.scalars().all()
        
    if not docs:
        return []

    # Prepare corpus
    corpus = []
    df = Counter()
    for doc in docs:
        tokens = _tokenize(doc.content)
        if tokens:
            tf = _compute_tf(tokens)
            corpus.append({"doc": doc, "tf": tf, "tokens": tokens})
            for word in tf.keys():
                df[word] += 1

    N = len(corpus)
    if N == 0:
        return []

    # Compute IDF
    idf = {word: math.log(N / (count + 1)) for word, count in df.items()}

    def _get_vector(tf: dict) -> dict:
        return {w: tf[w] * idf.get(w, 0.0) for w in tf}

    # Query vector
    q_tokens = _tokenize(query)
    q_tf = _compute_tf(q_tokens)
    q_vec = _get_vector(q_tf)

    # Score documents
    results = []
    for item in corpus:
        d_vec = _get_vector(item["tf"])
        
        # Cosine similarity
        intersection = set(q_vec.keys()) & set(d_vec.keys())
        dot_product = sum(q_vec[w] * d_vec[w] for w in intersection)
        
        mag1 = math.sqrt(sum(v**2 for v in q_vec.values()))
        mag2 = math.sqrt(sum(v**2 for v in d_vec.values()))
        
        score = 0.0
        if mag1 and mag2:
            score = dot_product / (mag1 * mag2)
            
        if score > 0.01:
            # Extract a 300 char snippet highlighting the first matched term
            text = item["doc"].content
            snippet = text[:300] + "..."
            for q_word in q_tokens:
                idx = text.lower().find(q_word)
                if idx != -1:
                    start = max(0, idx - 150)
                    end = min(len(text), idx + 150)
                    snippet = ("..." if start > 0 else "") + text[start:end] + "..."
                    break
                    
            results.append({
                "filename": item["doc"].filename,
                "score": score,
                "snippet": snippet
            })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]
