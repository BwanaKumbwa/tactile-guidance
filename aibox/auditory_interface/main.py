import asyncio
import sys

from mcp.client.stdio import stdio_client
from mcp import ClientSession

from mcp_config import get_server_parameters, convert_mcp_to_openai_tools
from audio_engine import AudioEngine
from query_processing import HANSBrain

current_server = "server_hans.py"
wake_word = "hello"

async def main():
    # 1. Initialize Components
    try:
        brain = HANSBrain()
    except ValueError as e:
        print(e)
        return

    audio = AudioEngine(wake_word=wake_word, snippet_duration=1.0)
    server_params = get_server_parameters(current_server)

    print("\n--- Connecting to MCP Server... ---")

    # 2. Establish MCP Connection
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            # 3. Load Tools
            mcp_tools = await session.list_tools()
            openai_tools = convert_mcp_to_openai_tools(mcp_tools)
            print(f"--- Connected! Tools: {[t.name for t in mcp_tools.tools]} ---")
            
            # 4. Main Loop
            try:
                while True:
                    # A. Listen
                    await audio.listen_for_wake_word()

                    # B. Record Command
                    command_text = await audio.capture_command()
                    
                    if not command_text or len(command_text) < 2:
                        print("   [Error] Could not understand command.")
                        continue

                    # C. Process
                    answer = await brain.process_query(
                        session, command_text, openai_tools
                    )

                    # D. Output
                    print(f"\n[AI ANSWER]: {answer}\n")
            
            except KeyboardInterrupt:
                print("\n[System] Stopping Voice Agent...")

if __name__ == "__main__":
    asyncio.run(main())