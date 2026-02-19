import json
import os
from openai import AsyncOpenAI
from mcp import ClientSession

from dotenv import load_dotenv

load_dotenv()

class HANSBrain:
    def __init__(self):
        if not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY environment variable is not set.")
        
        self.client = AsyncOpenAI()
        self.messages = [
            {"role": "system", "content": "You are a helpful assistant. If a tool requires data from another tool, execute them in steps."}
        ]

    async def process_query(self, session: ClientSession, user_text: str, openai_tools: list, model_type: str = "gpt-4o") -> str:
        """
        Handles multi-step tool chaining.
        """
        print(f"   [AI Processing] User said: \"{user_text}\"")
        self.messages.append({"role": "user", "content": user_text})

        # Reasoning loop - continues indefinitely
        while True:
            # 1. Ask LLM
            response = await self.client.chat.completions.create(
                model=model_type,
                messages=self.messages,
                tools=openai_tools,
                tool_choice="auto"
            )

            response_msg = response.choices[0].message
            tool_calls = response_msg.tool_calls

            # 2. If no tools are needed - return the text.
            if not tool_calls:
                final_answer = response_msg.content
                self.messages.append({"role": "assistant", "content": final_answer})
                return final_answer

            # 3. If tools are requested - execute them
            print(f"   [Step] Model wants to run: {[t.function.name for t in tool_calls]}")
            self.messages.append(response_msg) # Add the "intent" to history

            for tool_call in tool_calls:
                func_name = tool_call.function.name
                func_args = json.loads(tool_call.function.arguments)

                # Execute MCP tool
                print(f"     > Running {func_name} with {func_args}")
                mcp_result = await session.call_tool(func_name, arguments=func_args)
                
                # Get output
                tool_output = mcp_result.content[0].text
                print(f"     < Result: {tool_output}")

                # Add result to history so LLM can see it in the next loop iteration
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_output
                })
            
            # The code now goes back to `while True`.
            # The LLM looks at the history, sees the tool output, and decides if it needs to call the next tool or give the answer.