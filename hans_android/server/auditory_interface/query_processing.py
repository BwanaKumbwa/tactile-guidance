import json
import os
import httpx
from pathlib import Path
from mcp import ClientSession
from dotenv import load_dotenv

load_dotenv(override=True)

# Markdown loader
def load_markdown_file(filename: str) -> str:
    """Load markdown content from project root."""
    path = Path(__file__).parent.parent / filename
    if not path.exists():
        print(f"⚠️  Warning: {filename} not found at {path}")
        return ""
    with open(path, "r") as f:
        return f.read()

# Load agent files
SOUL = load_markdown_file("SOUL.md")
SKILLS = load_markdown_file("SKILLS.md")
AGENTS = load_markdown_file("AGENTS.md")

# Custom API configuration (matching server_hans.py)
API_URL = os.getenv("API_URL")
API_KEY = os.getenv("API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")

if not API_KEY:
    raise ValueError("API_KEY not set in .env")

CUSTOM_API_URL = f"{API_URL.rstrip('/')}/v1/chat/completions"

class HANSBrain:
    def __init__(self):
        self.api_key = API_KEY
        self.messages = []

        self.verbosity = "concise"  # Can be 'concise', 'normal', 'verbose'
        self.speed = "normal"

        self.update_system_prompt()

    def update_system_prompt(self):
        """Build system prompt from markdown files."""
        verbosity_rules = {
            "concise": "Keep answers to 1 short sentence. No pleasantries. Just the facts.",
            "normal": "Answer naturally and helpfully in 1-2 sentences.",
            "verbose": "Be detailed and descriptive. Explain what you see and what you're doing."
        }

        system_content = f"""## HANS System Prompt

    ### WHO YOU ARE
    {SOUL}

    ### YOUR AVAILABLE SKILLS
    {SKILLS}

    ### YOUR OPERATING MODE
    {AGENTS}

    ---

    ## CRITICAL RUNTIME INSTRUCTIONS

    ### Tool Selection Priority

    **1. For Target Setting:**

    **SINGLE TARGET** ("set target to X", "guide me to X"):
        → Use set_target_with_fuzzy_match(target_name)
        → Automatically corrects speech errors
        → Requires target to be visible
    
    **MULTIPLE TARGETS** ("add X and Y", "find X and Y"):
        → Use add_targets_to_list([targets], mode)
        → Accepts visible AND non-visible targets
        → Use "ordered" if user specifies sequence
        → Use "unordered" otherwise
    
    **CLEAR TARGETS**:
        → Use clear_target_list()

    **2. For Grasp Completion:**
    When user says "I got it" / "I have the X":
        → Use grasp_complete()

    **3. For Simple Object Detection:**
    When user asks "What do you see?":
        → Use get_visible_objects() first
        → If empty, fallback to analyze_camera_view("describe")

    **4. For Complex Queries:**
    Use analyze_camera_view() with appropriate instruction

    ### Response Style
    - Verbosity Level: {self.verbosity.upper()}
    - Format: {verbosity_rules.get(self.verbosity, 'normal')}

    ### Important Notes
    - Multiple targets can include non-visible objects
    - Always trust user intent
    - Provide clear feedback on target matching status
    - Add speech rate tag: [SPEED:NORMAL]
    """

        if not self.messages or self.messages[0]["role"] != "system":
            self.messages = [{"role": "system", "content": system_content}]
        else:
            self.messages[0]["content"] = system_content

    def set_verbosity(self, level: str) -> str:
        """Called by an MCP tool to change chat length."""

        if level in ["concise", "normal", "verbose"]:
            self.verbosity = level
            # Only update system message if no conversation history yet
            if len(self.messages) <= 1:  # Only system message exists
                self.update_system_prompt()
            return f"I will now be {level}."
        return f"Unknown verbosity level: {level}."

    async def _call_llm(self, openai_tools: list, model: str) -> dict:
        """Call LiteLLM endpoint with function calling support."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        payload = {
            "model": model,
            "messages": self.messages,
            "max_tokens": 1024,
            "stream": False,
        }

        # Add tools if available
        if openai_tools:
            print(f"   [DEBUG] Sending {len(openai_tools)} tools to LLM")
            payload["tools"] = openai_tools
            payload["tool_choice"] = "auto"

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    CUSTOM_API_URL,
                    headers=headers,
                    json=payload,
                    timeout=30.0
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                print(f"   [ERROR] LLM API Error: {e.status_code}")
                print(f"   [DEBUG] Response: {e.response.text}")
                raise

    async def process_query(
        self,
        session: ClientSession,
        user_text: str,
        openai_tools: list,
        model_type: str = LLM_MODEL
    ) -> str:
        """
        Process user input with tool chaining loop.
        """
        print(f"   [AI Processing] User said: \"{user_text}\"")
        self.messages.append({"role": "user", "content": user_text})

        # Tool chaining loop
        max_iterations = 5
        iteration = 0

        while iteration < max_iterations:
            iteration += 1

            # 1. Call LLM
            try:
                llm_data = await self._call_llm(openai_tools, model_type)
            except Exception as e:
                print(f"   [ERROR] Failed to reach LLM API: {e}")
                return "Sorry, I could not reach the AI service."

            # 2. Parse response
            response_msg = llm_data["choices"][0]["message"]
            tool_calls = response_msg.get("tool_calls")

            # 3. If no tools requested, return final answer
            if not tool_calls:
                final_answer = response_msg.get("content", "")
                self.messages.append({"role": "assistant", "content": final_answer})
                return final_answer

            # 4. Execute tools
            print(f"   [Step] Model wants tools: {[t['function']['name'] for t in tool_calls]}")
            self.messages.append(response_msg)

            for tool_call in tool_calls:
                func_name = tool_call["function"]["name"]
                func_args = json.loads(tool_call["function"]["arguments"])

                print(f"     > Running {func_name}")
                try:
                    mcp_result = await session.call_tool(func_name, arguments=func_args)
                    tool_output = mcp_result.content[0].text
                    print(f"     < Result: {tool_output}")
                except Exception as e:
                    tool_output = f"Tool error: {str(e)}"
                    print(f"     < Error: {tool_output}")

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": tool_output
                })

        return "Maximum iterations reached. Please try again."