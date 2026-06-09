from dotenv import load_dotenv
load_dotenv()

import os
from openai import OpenAI
from langchain_openai import ChatOpenAI

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://api.deepseek.com/v1"
)

llm = ChatOpenAI(
    model=os.getenv("MODEL_NAME", "deepseek-v4-flash"),
    openai_api_key=os.getenv("OPENAI_API_KEY"),
    openai_api_base="https://api.deepseek.com/v1",
    temperature=0
)
