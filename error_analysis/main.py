import sys
import os
from dotenv import load_dotenv
from llm import LLMProofWriter
load_dotenv()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: main.py <tag name>")
        sys.exit(1)

    tag_name = sys.argv[1]
    openai_api_key = os.getenv("OPENAI_API_KEY", None)
    if not openai_api_key:
        raise EnvironmentError("No OpenAI API key found")

    proof_writer = LLMProofWriter(openai_api_key, tag_name, test_mode=True)
    proof_writer.iterate_proof(max_attempts=3)
