from app.llm import get_llm

print(get_llm().invoke("Reply with the single word: ready").content)