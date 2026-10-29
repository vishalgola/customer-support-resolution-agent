from functools import lru_cache

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

from app.config import settings

load_dotenv()  # puts provider API keys from .env into os.environ for LangChain


@lru_cache(maxsize=1)
def get_llm():
    if not settings.llm_model:
        raise RuntimeError("Set LLM_MODEL in .env, e.g. google_genai:gemini-2.5-flash")
    return init_chat_model(settings.llm_model, temperature=0)