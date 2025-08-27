import re
import sys
import os
import subprocess
import json
import shutil
from dotenv import load_dotenv
from agent import OpenAIAgent
from pathlib import Path
from makefile.output_models import MakefileFields

load_dotenv()

class LLMMakefileGenerator(OpenAIAgent):

    SYSTEM_PROMPT = """
        You are a helpful AI assistant that is assisting in the development of Makefiles for CBMC proof harnesses. \
        Your objective is to complete the provided Makefile template such that the harness compiles successfully with CBMC. \
        
        In completing the Makefile, you should will need to: \
        1. Identify and include any paths necessary for successful compilation (H_INC). \
        2. Specify any necessary CBMC flags (H_CBMCFLAGS) required for the harness, such as loop unwinding limits. \
        3. Define any environment variables (H_DEF) that are needed to resolve errors during compilation. \
        4. List any additional source files or libraries (LINK) that need to be linked. \

        You have been provided with the file containing the definition of the target function, and any include statements that may be relevant. \
        Additionally, you have access to a function that will allow you to run bash commands such as grep that can help search the repo for relevant information. \
        If the makefile fails to compile, you will provided with the error output, and you should iteratively refine the Makefile until it compiles successfully. \
        
        Below is an example of what a completed Makefile might look like:
        """

    def __init__(self, target_func, harness_path, target_file_path, openai_api_key, test_mode=False):
        super().__init__(
            openai_api_key,
            agent_name="makefile",
            harness_name=target_func,
            harness_path=harness_path
        )
        self.func_file = target_file_path
        self.root_dir = os.path.dirname(self.harness_path) # This will get overwritten later, this is the dir where we run the bash commands

        with open('./Makefile.example', 'r') as file:
            example_makefile = file.read()
            LLMMakefileGenerator.SYSTEM_PROMPT += f"\n```\n{example_makefile}\n```\n"

    def run_bash_command(self, cmd):
        """Run a command-line command and return the output."""
        try:
            print("Running command: ", cmd)
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, check=True, cwd=self.root_dir
            )
            return {"cmd": cmd, "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        except subprocess.CalledProcessError as e:
            print(f"Command failed with error:\n{e.stderr}")
            return None

    def run_make(self):
        try:
            result = subprocess.run(
                "make", shell=True, capture_output=True, text=True, cwd=os.path.dirname(self.harness_path), timeout=60
            )
            print(result.stdout)
            print(result.stderr) 
            return {"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        except Exception as e:
            if isinstance(e, subprocess.TimeoutExpired):
                print("Make command timed out.")
                return "timeout"
            else:
                print(f"An error occurred while running make: {e}")
                return result.stderr
            
    def _upload_vector_store_files(self):
        upload_complete = self.client.vector_stores.files.upload_and_poll(
            vector_store_id=self.vector_store.id,
            file=open(self.func_file, "rb"),
            poll_interval_ms=1000,
        )
        return upload_complete

    def generate_makefile(self):

        # First, go through and insert anything we need programmatically
        self._upload_vector_store_files()

        backup_path = self._backup_makefile()
        makefile_lines = []
        with open('./Makefile.template', 'r') as file:
            for line in file.readlines():
                if line.startswith('ROOT'):
                    root_path = line.split('=')[1].strip()
                    self.root_dir = (Path(os.path.dirname(self.harness_path)) / Path(root_path)).resolve()

                if line.startswith('LINK'):
                    # Insert the file path
                    makefile_lines.append('LINK = ' + re.sub(rf"{str(self.root_dir)}/(.*)", r"$(ROOT)/\1", str(self.func_file)))
                elif line.startswith('H_ENTRY'):
                    # Insert the target function
                    makefile_lines.append(f'H_ENTRY = {self.harness_name}')
                else:
                    makefile_lines.append(line)
        
        makefile_content = ''.join(makefile_lines)
        
        with open(self.harness_path, 'w') as file:
            file.write(makefile_content)

        make_results = self.run_make()
        while make_results['stderr'] != "":

            makefile_updates = self.llm_complete_makefile(makefile_content, make_results['stderr'])
            makefile_content = self.update_makefile(makefile_content, makefile_updates['response'])
            make_results = self.run_make()

        # self.run_make()

        # self._restore_makefile(backup_path)

    def llm_complete_makefile(self, makefile_content, make_error):
        """
        Prompt the LLM to provide the remaining fields needed to complete the makefile
        """

        user_prompt = f"""
        Below is the current Makefile used for compiling a CBMC proof harness for the target function '{self.harness_name}': \
        {makefile_content}

        When running the `make` command in the directory `{os.path.dirname(self.harness_path)}`, the following error occured.
        {make_error}

        If the error is the result of a failed include statement, then you should attempt to expand the include paths under H_INC to include that file. \
        
        If the error is the result of a syntax or syntax-related error, it is likely due to an environment variable that needs to be set under H_DEF. \
        Use command line tools to determine the cause of the syntax error, and find any locations where an environment variable may cause that syntax error.
        
        Please provide any necessary additions to the H_INC, H_CBMCFLAGS, H_DEF, and LINK fields to ensure successful compilation of the harness. \
        If you would like to get more information about the codebase to help you fill in these fields, \
        you can run bash commands using the provided function 'run_bash_command'. \
        These commands will be run from the directory {self.root_dir}, and the output will be formatted as a JSON object \
        containing the original command, the exit code, stdout output, and stderr output.  \
        
        Once you have gathered enough information, please provide a JSON object containing the added fields in the specified format. \
        Please only respond with entirely new entries for each file, and do not include any already existing entries from the provided Makefile. \
        
        Please ensure that your suggestions for additions to the H_INC field are not redundant with any existing paths under that field. \

        If no additions are needed for that field, return an empty array for that field. Please only include one copy of each output field. \
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

        response = self.client.responses.create(
            model='gpt-4.1', # Most recent tests were done on 4.1 I think
            instructions=LLMMakefileGenerator.SYSTEM_PROMPT,
            input=input_messages,
            # text_format=MakefileFields,
            tool_choice="auto",
            tools=llm_tools,
            temperature=1.0, # Sometimes constraints on preconditions are randomly ignored, so hopefully this will help fix it
            include=["file_search_call.results"] 
        )

        # Continue running bash commands for the model until it gives an output message
        while not any([output.type == "message" for output in response.output]):

            for tool_call in response.output:
                if tool_call.type != "function_call":
                    continue
                func_name = tool_call.name
                func_args = json.loads(tool_call.arguments)

                if func_name == "run_bash_command":
                    bash_result = self.run_bash_command(func_args['cmd'])
                    input_messages.append(tool_call)
                    input_messages.append({
                        "type": "function_call_output",
                        "call_id": tool_call.call_id,
                        "output": str(bash_result)
                    })
                    response = self.client.responses.create(
                        model='gpt-4.1', # Most recent tests were done on 4.1 I think
                        instructions=LLMMakefileGenerator.SYSTEM_PROMPT,
                        input=input_messages,
                        text={
                            "format": {
                                "type": "json_schema",
                                "name": "makefile_fields",
                                "strict": True,
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "LINK": {
                                            "type": "array",
                                            "items": { "type": "string" }
                                        },
                                        "H_CBMCFLAGS": {
                                            "type": "array",
                                            "items": { "type": "string" }
                                        },
                                        "H_DEF": {
                                            "type": "array",
                                            "items": { "type": "string" }
                                        },
                                        "H_INC": {
                                            "type": "array",
                                            "items": { "type": "string" }
                                        },
                                        "reasoning": {
                                            "type": 'string'
                                        }
                                    },
                                    "additionalProperties": False,
                                    "required": ["LINK", "H_CBMCFLAGS", "H_DEF", "H_INC", "reasoning"],
                                },

                            }
                        },
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

        ins_h_inc = False # Need a variable to track this bc there are initially several includes on multiple lines

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
                line = line + ' ' + env_vars[0] + ' \\'
                updated_lines.append(line)
                for var in env_vars[1:]:
                    updated_lines.append(' ' * 7 + var + ' \\')
            
            elif line.startswith('H_INC =') and len(include_paths) > 0:
                ins_h_inc = True
                updated_lines.append(line)

            elif ins_h_inc and (line == '' or line.startswith('#') or line.startswith('MAKE_INCLUDE_PATH')):
                # Once we've iterated past the other H_INC statements
                for inc in include_paths:
                    updated_lines.append(' ' * 8 + inc + ' \\')

                updated_lines.append(line)
                ins_h_inc = False
                    
            else:
                updated_lines.append(line)

        makefile_content = '\n'.join(updated_lines)
        
        # IDK where I'd be dumping these file contents for now
        with open(self.harness_path, 'w') as file:
            file.write(makefile_content)

        return makefile_content

    def _backup_makefile(self, backup_suffix='temp'):
        """
        Create an unmodified copy of the harness file that we can restore
        """
        backup_path = os.path.join(os.path.dirname(self.harness_path), 'Makefile.backup')
        shutil.copy(self.harness_path, backup_path)
        return backup_path

    def _restore_makefile(self, backup_path):
        if not os.path.exists(backup_path):
            print(f"Backup file {backup_path} does not exist. Cannot restore harness.")
            return

        shutil.copy(backup_path, self.harness_path)
        print(f"Restored harness from {backup_path} to {self.harness_path}")
        os.remove(backup_path)

    def _update_files_in_vector_store(self):
        pass

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

