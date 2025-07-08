import re
from typing import Optional, Tuple
from .custom_prompt_template import custom_prompt
from langchain.chains import LLMChain
from langchain_openai import ChatOpenAI
import os
import logging

logger = logging.getLogger(__name__)

# This function builds an LLM instance (can be reused from utils if needed)
def build_llm(callbacks=None):
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model="gpt-4o",
        temperature=0.5,
        streaming=True,
        callbacks=callbacks or [],
        api_key=os.getenv("OPENAI_API_KEY"),
        verbose=False
    )

# Utility to extract SQL and explanation from LLM response
def extract_sql_and_explanation(response: str) -> Tuple[Optional[str], Optional[str]]:
    sql_match = re.search(r"SQL:\s*(.*?)\n+EXPLANATION:", response, re.DOTALL)
    explanation_match = re.search(r"EXPLANATION:\s*(.*?)\n+FORMAT:", response, re.DOTALL)
    sql_query = sql_match.group(1).strip() if sql_match else None
    explanation = explanation_match.group(1).strip() if explanation_match else None
    return sql_query, explanation

# # Main function to generate SQL and explanation
# def generate_sql_and_explanation(user_question, llm=None):
#     if llm is None:
#         llm = build_llm()
#     chain = LLMChain(llm=llm, prompt=custom_prompt)
#     response = chain.run(user_question)
#     sql_query, explanation = extract_sql_and_explanation(response)
#     logger.info(f"Generated SQL: {sql_query}\nExplanation: {explanation}")
#     return sql_query, explanation, response
