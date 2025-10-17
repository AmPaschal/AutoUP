""" GPT LLM"""

# System
import os
import json

# OpenAI
import openai

# AutoUp
from agent_tool import AgentTool
from make_tool import MakeTool
from overwrite_file_tool import OverrideFileTool


class Agent:
    """ OpenAI GPT manager"""

    def __init__(self) -> None:8c5eb0b44b7a3d3c21654f62f15bc0110c557684
        openai_api_key = os.getenv("OPENAI_API_KEY", None)
        if not openai_api_key:
            raise EnvironmentError("No OpenAI API key found")
        self.client = openai.OpenAI(api_key=openai_api_key)
        self.tools: list[AgentTool] = [
            MakeTool(),
            OverrideFileTool(),
        ]

    def create_chat(self, system_prompt: str, user_prompt: str):
        """Create a Chat with the LLM using the corresponding input"""
        chat = self.client.chat.completions.create(
            model="gpt-5",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            tools=[tool.get_tool_signature() for tool in self.tools],
            tool_choice="auto",
        )
        print(chat.choices[0].message.tool_calls)
        tool_calls = chat.choices[0].message.tool_calls
        if tool_calls:
            args = tool_calls[0].function.arguments
            parsed = json.loads(args)
            result = self.__get_tool_by_name(
                tool_calls[0].function.name).use_tool(**parsed)
            print("Tool result:", result)

    def __get_tool_by_name(self, name: str) -> AgentTool | None:
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None


Agent().create_chat(
    system_prompt="You are a helpful assistant.",
    user_prompt="Hello, how are you? This is a test, can you run make using the tool I provided in this directory? /home/rcalvome/Documents/AutoUp/framework/RIOT/cbmc/proofs/dns_msg_parse_reply/",
)
