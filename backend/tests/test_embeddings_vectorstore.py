from app import vectorstore
from app.embeddings import cosine, embed, embed_batch
from app.models import Memory


def test_embed_is_deterministic_and_unit_length():
    a = embed("my accountant is Jane Doe")
    b = embed("my accountant is Jane Doe")
    assert a == b
    assert abs(sum(x * x for x in a) - 1.0) < 1e-6  # normalized


def test_cosine_similar_texts_score_higher():
    q = embed("I prefer morning meetings")
    near = embed("I like meetings in the morning")
    far = embed("the capital of France is Paris")
    assert cosine(q, near) > cosine(q, far)


def test_vectorstore_add_and_search_json_path(db):
    m1 = Memory(content="Matt's accountant is Jane Doe", category="people")
    m2 = Memory(content="Matt prefers morning meetings", category="preferences")
    vectorstore.add(db, m1)
    vectorstore.add(db, m2)
    hits = vectorstore.search(db, "who does my taxes / accountant", k=2)
    assert hits, "expected at least one hit"
    assert hits[0][0].content == "Matt's accountant is Jane Doe"
