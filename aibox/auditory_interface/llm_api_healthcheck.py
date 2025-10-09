import os
import openai
from openai import OpenAI

from dotenv import load_dotenv

load_dotenv()

def main():

    openai.api_key = os.getenv("OPENAI_API_KEY")

    try:
        client = OpenAI(
            api_key=openai.api_key,
        )

        response = client.responses.create(
            model="gpt-4o",
            input="Hello! Can you respond to this message?",
        )

        print("LLM API is reachable. Response:")
        print(response.output_text)
    except Exception as e:
        print("Failed to reach LLM API.")
        print(f"Error: {e}")

if __name__ == "__main__":

    main()