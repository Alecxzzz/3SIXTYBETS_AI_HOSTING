import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

API_KEY = os.getenv("GROQ_API_KEY")
BASE_URL = "https://api.groq.com/openai/v1"
MODEL = "llama-3.1-8b-instant"

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)