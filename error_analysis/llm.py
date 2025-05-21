import os
import json
import re
from openai import OpenAI
from pydantic import BaseModel
from advice import get_advice_for_cluster
from parser import extract_errors_and_payload

class Precondition(BaseModel):
    function: str
    line: int
    precondition: str
    reasoning: str

class FunctionModel(BaseModel):
    function: str
    definition: str

class NewPreconditions(BaseModel):
    preconditions: list[Precondition]
    # func_models: list[FunctionModel]

class LLMProofWriter:
    """Proof writer using LLMs to generate CBMC proofs"""

    def __init__(self, openai_api_key, tag_name):
        self.tag_name = tag_name
        self.harness_name = re.match(r'(.*)_precon_\d+', self.tag_name).group(1)
        self.payload_dir = os.path.join("payloads_v2", self.tag_name)
        self.client = OpenAI(api_key=openai_api_key)
        self.vector_store = self._create_vector_store()
        

    def _create_vector_store(self):
        """
        Checks if a vector store already exists and initializes it with the relevant files if it does not
        """

        for store in self.client.vector_stores.list():
            if store.name == self.tag_name:
                print(f"Found existing vector store with ID {store.id}")
                return store

        print(f"Initializing vector store for {self.tag_name}")
        vector_store = self.client.vector_stores.create(
            name=self.tag_name
        )

        system_payload_path = os.path.join(self.payload_dir, f'{self.tag_name}_payload.json')

        self.client.vector_stores.files.upload_and_poll(
            vector_store_id=vector_store.id,
            file=open(system_payload_path, "rb")
        )
        
        print("Successfully uploaded files to vector store")
        return vector_store

    def iterate_proof(self):
        MAX_ITERATIONS = 2
        curr_errors = extract_errors_and_payload(self.harness_name, self.tag_name)

        iterations = 0
        while len(curr_errors) > 0 and iterations < MAX_ITERATIONS:

            # Technically should have a step here where I make sure the payload file is up to date
            llm_response = self.analyze_errors(curr_errors)
            self.update_harness(llm_response['preconditions'])
            curr_errors = extract_errors_and_payload(self.harness_name, self.tag_name)
            iterations += 1

    def analyze_errors(self, error_clusters):
        SYSTEM_PROMPT = f"""
            You are a helpful AI assistant that is assisting in the development of unit proofs.\
            A unit proof defines an input model of a target function so that the function can be verified with bounded model checking.\
            Our goal is to use preconditions (defined with __CPROVER_assume) to constrain the input model such that no error or violation is reported during verification.\

            We will provide the results of by running the harness defined in the “unit_proof” field.\
            This field contains the current definition of the harness, as well as the definition for any function models that mimic the behavior of complex functions using __CPROVER_assume statements.\
            You can suggest preconditions be added to these function models if you believe the error is the result of a variable returned from that function model.\
            
            The definitions for each function reachable during the execution of the harness can be found under the "target_functions" field.\
            If a function has a definition of "Unknown", this means the function is undefined. When this function is called, CBMC will return a random value for it's return type.\
            If you believe this random return value is the cause of an error, indicate that the undefined function will require a function model in your response.\

            Your objective is to provide a precondition or list of preconditions that can be added to the existing harness to resolve the given error.
            You are to follow the steps provided to understand the source of the error, and then determine an appropriate pre-condition that will resolve the error.
            Only come up with the most minimal (using 1) set of preconditions necessary. You must use __CPROVER_assume in the provided precondition.
            """
        
        for cluster, errors in error_clusters.items():
            debugging_steps = get_advice_for_cluster(cluster, self.harness_name)
            for error in errors:
                user_prompt = f"""
                Provide a minimal set of preconditions that can be added to the current harness definition to resolve the following error:\
                {error['line']} of {error['function']}: {error['msg']}

                To assist with debugging, we have provided the values of each variable passed to the target function before the error occured.\
                The variables are organized based on the context in which they were initialized:\
                {json.dumps(error['harness_vars'])}

                Before attempting to resolve the error, answer the following questions to identify the source of the error\
                and the specific variable(s) that are responsible.
                {'\n'.join([f'{i + 1}. {step}' for i, step in enumerate(debugging_steps)])}

                Your response should adhere to the specified JSON format, stating which function in the harness file requires the precondition and the line number where it should be inserted.\
                Remember this should be a minimal set of preconditions.
                """

                try:
                    response = self.client.responses.parse(
                        model='gpt-4.1', # Most recent tests were done on 4.1 I think
                        instructions=SYSTEM_PROMPT,
                        input=[{'role': 'user', 'content': user_prompt}],
                        text_format=NewPreconditions,
                        reasoning={'summary': 'auto'},
                        tool_choice="required",
                        tools=[{
                            "type": "file_search",
                            "vector_store_ids": [self.vector_store.id]
                        }],
                        include=["file_search_call.results"] 
                    )
                    print(response.output_text)
                    output = {"system_prompt": SYSTEM_PROMPT, "user_prompt": user_prompt, "response": response.output_text}
                    with open(f'./responses/{self.tag_name}_response.json', 'w') as f:
                        json.dump(output, f, indent=4)

                    return json.loads(response.output_text)
                except Exception as e:
                    print(f"Exception while making OpenAI API call: {str(e)}")
        
    def update_harness(self, preconditions):
        harness_path = os.path.join('..', '..', 'RIOT', 'cbmc', 'proofs', self.harness_name, f'{self.harness_name}_harness.c')

        with open(harness_path, 'r') as f:
            harness_lines = f.readlines()
        
        # Might need to sort preconditions by their insertion line

        insert_offset = 0
        for precondition in preconditions:
            insert_line = precondition['line'] + insert_offset
            prev_line = harness_lines[insert_line]
            if prev_line == '\n':
                i = 1
                while prev_line == '\n':
                    prev_line = harness_lines[insert_line - i]
                    i += 1
            indent = re.match(r'(\s*).*', prev_line).group(1)
            new_line = f'{indent}{precondition['precondition']}\n'
            harness_lines.insert(insert_line, new_line)
            insert_offset += 1

        with open(os.path.join(os.path.dirname(harness_path), f'{self.harness_name}_harness.c'), 'w') as f:
            f.writelines(harness_lines)