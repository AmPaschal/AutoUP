import re
import sys
import os
import subprocess
import json
from openai import OpenAI
from dotenv import load_dotenv
from agent import OpenAIAgent
from output_models import MakefileFields

load_dotenv()

class LLMMakefileGenerator(OpenAIAgent):

    SYSTEM_PROMPT = """
        Hello!
        """

    def __init__(self, target_func, harness_path, target_file_path, openai_api_key):
        super().__init__(openai_api_key, "makefile", target_func, harness_path)
        self.func_file = target_file_path

    def run_bash_command(self, cmd):
        """Run a command-line command and return the output."""
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, check=True
            )
            return {"cmd": cmd, "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        except subprocess.CalledProcessError as e:
            print(f"Command failed with error:\n{e.stderr}")
            return None

    def run_make(self):
        try:
            result = subprocess.run(
                "make", shell=True, capture_output=True, text=True, cwd=self.harness_path, timeout=60
            )
            return None
        except Exception as e:
            if isinstance(e, subprocess.TimeoutExpired):
                print("Make command timed out.")
                return "timeout"
            else:
                print(f"An error occurred while running make: {e}")
                return result.stderr
            
    def _upload_vector_store_files(self):
        self.client.vector_stores.files.upload_and_poll(
            vector_store_id=self.vector_store.id,
            file=open(self.func_file, "rb"),
        )

    def generate_makefile(self):

        # First, go through and insert anything we need programmatically
        self._upload_vector_store_files()

        makefile_lines = []
        with open('./Makefile.template', 'r') as file:
            for line in file.readlines():
                if line.startswith('LINK ='):
                    # Insert the file path
                    makefile_lines.append('LINK = ' + re.sub(r".*?\/(.*)", r"$(ROOT)/\1", file_path) + '\n')
                elif line.startswith('H_ENTRY ='):
                    # Insert the target function
                    makefile_lines.append(f'H_ENTRY = {target_function}\n')
                else:
                    makefile_lines.append(line)
        
        makefile_content = '\n'.join(makefile_lines)
        
        makefile_updates = self.llm_complete_makefile(makefile_content)
        self.update_makefile(makefile_content, makefile_updates['response'])

        self.run_make()


    def llm_complete_makefile(self, makefile_content):
        """
        Prompt the LLM to provide the remaining fields needed to complete the makefile
        """

        user_prompt = f"""
        TBD
        """

        llm_tools = [{
                "type": "file_search",
                "vector_store_ids": [self.vector_store.id]
            },
            {
                "type": "function",
                "name": "run_bash_command",
                "description": "Run a command-line command to search the repo for relevant information, and return the output",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cmd": {
                            "type": "string",
                            "description": "A bash command-line command to run"
                        }
                    },
                    "required": ["cmd"],
                    "additionalProperties": False
                }
            }
        ]
        
        input_messages = [{'role': 'user', 'content': user_prompt}]

        response = self.client.responses.parse(
            model='gpt-4.1', # Most recent tests were done on 4.1 I think
            instructions=LLMMakefileGenerator.SYSTEM_PROMPT,
            input=input_messages,
            text_format=MakefileFields,
            tool_choice="auto",
            tools=llm_tools,
            temperature=1.0, # Sometimes constraints on preconditions are randomly ignored, so hopefully this will help fix it
            include=["file_search_call.results"] 
        )

        # Continue running bash commands for the model until it gives an output message
        while response.output[0].type == "function_call":
            tool_call = response.output[0]
            func_name = tool_call.name
            func_args = json.loads(tool_call.arguments)

            if func_name == "run_bash_command":
                bash_result = self.run_bash_command(func_args['cmd'])
                input_messages.append({
                    "type": "function_call_output",
                    "call_id": tool_call.call_id,
                    "output": bash_result
                })
                response = self.client.responses.parse(
                    model='gpt-4.1', # Most recent tests were done on 4.1 I think
                    instructions=LLMMakefileGenerator.SYSTEM_PROMPT,
                    input=input_messages,
                    text_format=MakefileFields,
                    tool_choice="auto",
                    tools=llm_tools,
                    temperature=1.0, # Sometimes constraints on preconditions are randomly ignored, so hopefully this will help fix it
                    include=["file_search_call.results"] 
                )
            else:
                raise ValueError(f"Unknown function call: {func_name}")

        return { 'id': response.id, 'response': json.loads(response.output_text), 'usage': response.usage}

    def update_makefile(self, makefile_content, makefile_updates):
        link = makefile_updates.get('LINK', [])
        cbmc_flags = makefile_updates.get('H_CBMCFLAGS', [])
        env_vars = makefile_updates.get('H_DEF', [])
        include_paths = makefile_updates.get('H_INC', [])

        updated_lines = []
        for line in makefile_content.splitlines():
            if line.startswith('LINK =') and len(link) > 0:
                # Insert the file path 
                updated_lines.append(line + ' \\')
                for l in link:
                    updated_lines.append(' ' * 7 + l + ' \\')

            elif line.startswith('H_CBMCFLAGS =') and len(cbmc_flags) > 0:
                # Insert the target function
                updated_lines.append(line + ' '.join(cbmc_flags))

            elif line.startswith('H_DEF =') and len(env_vars) > 0:
                line = line + ' ' + env_vars + ' \\'
                updated_lines.append(line)
                for var in env_vars[1:]:
                    updated_lines.append(' ' * 7 + var + ' \\')
            
            elif line.startswith('H_INC =') and len(include_paths) > 0:
                line = line + ' ' + include_paths + ' \\'
                updated_lines.append(line)
                for inc in include_paths[1:]:
                    updated_lines.append(' ' * 7 + inc + ' \\')

        makefile_content = '\n'.join(updated_lines)
        
        # IDK where I'd be dumping these file contents for now

if __name__ == "__main__":

    if len(sys.argv) < 3:
        print("Usage: python gen_makefile.py <target function> <file path>")

    target_function = sys.argv[1]
    file_path = sys.argv[2]
    openai_api_key = os.getenv("OPENAI_API_KEY", None)
    if not openai_api_key:
        raise EnvironmentError("No OpenAI API key found")

    makefile_generator = LLMMakefileGenerator(target_function, os.getcwd(), file_path, openai_api_key)
    makefile_generator.generate_makefile()

