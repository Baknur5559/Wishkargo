# ai_brain.py - Мозг ИИ (DeepSeek + Gemini Fallback)
import os
import logging
import asyncio
from openai import AsyncOpenAI
import google.generativeai as genai
from dotenv import load_dotenv

# Настройка
load_dotenv()
logger = logging.getLogger(__name__)

# 1. Настройка DeepSeek (через библиотеку OpenAI)
DS_KEY = os.getenv("DEEPSEEK_API_KEY")
deepseek_client = None
if DS_KEY:
    deepseek_client = AsyncOpenAI(api_key=DS_KEY, base_url="https://api.deepseek.com")

# 2. Настройка Gemini (Google)
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
gemini_model = None
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    gemini_model = genai.GenerativeModel('gemini-1.5-flash')

async def get_ai_response(messages_history: list, context_prompt: str = "") -> str:
    """
    ВЕРСИЯ 2.0: Поддержка истории диалога.
    messages_history = [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
    """
    
    # --- ПОПЫТКА 1: DeepSeek ---
    if deepseek_client:
        try:
            # Собираем полный контекст: Системный промпт + История
            full_messages = [{"role": "system", "content": context_prompt}] + messages_history
            
            response = await deepseek_client.chat.completions.create(
                model="deepseek-chat",
                messages=full_messages,
                timeout=60.0
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.warning(f"⚠️ DeepSeek сбой: {e}")

    # --- ПОПЫТКА 2: Gemini (Ограниченная поддержка истории) ---
    # Gemini Flash (через google-generativeai) имеет свой формат истории.
    # Для простоты мы склеим историю в один текст, если DeepSeek упал.
    if gemini_model:
        try:
            chat_history_text = ""
            for msg in messages_history:
                role_name = "Клиент" if msg['role'] == 'user' else "Ты"
                chat_history_text += f"{role_name}: {msg['content']}\n"
            
            full_prompt = f"{context_prompt}\n\nИСТОРИЯ ДИАЛОГА:\n{chat_history_text}\n\nТВОЙ ОТВЕТ:"
            response = await gemini_model.generate_content_async(full_prompt)
            return response.text
        except Exception as e:
            logger.error(f"⚠️ Gemini сбой: {e}")

    return "Извините, я перегружен. Повторите вопрос."
