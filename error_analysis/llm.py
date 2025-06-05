import os
import json
import re
import shutil
import csv
from openai import OpenAI
from pydantic import BaseModel
from typing import Optional
from advice import get_advice_for_cluster
from parser import extract_errors_and_payload


class ExistingPrecondition(BaseModel):
    precondition: str
    function: str
    line: int

class PreconditionChecks(BaseModel):
    uses_cprover_assume_format: bool
    only_harness_variables: bool
    placed_in_harness_before_last_line: bool
    placed_in_harness_after_initialization: bool
    is_not_redundant: bool

class PreconditionFunction(BaseModel):
    name: str
    local_vars: list[str]

class NewPrecondition(BaseModel):
    function: PreconditionFunction
    precondition: str
    precondition_as_code: str
    previous_line_of_code: str # Keeps inserting things at the wrong line numbers
    previous_line_number: int # Don't actually need this line number, but requiring it can discourage hallucinations
    next_line_of_code: str
    next_line_number: int 
    reasoning: str
    is_valid: PreconditionChecks

class FunctionModel(BaseModel):
    function: str
    definition: str

class Variable(BaseModel):
    name: str
    provided_value: str
    original_scope: str
    modifications_after_harness: list[str]
    value_at_point_of_error: str

class OptionalQuestions(BaseModel):
    question: str
    analysis: str

class DebuggingQuestions(BaseModel):
    provided_debugging_step: int
    provided_debugging_question: str
    further_analysis_questions: Optional[list[OptionalQuestions]]
    was_cause_of_error: bool
    problem_variables: Optional[list[Variable]]
    reasoning: str

class PreviousSuggestions(BaseModel):
    preconditions: list[str]
    resolved_error: bool
    analyis: str

class NewPreconditions(BaseModel):
    existing_preconditions: list[ExistingPrecondition]
    debugging_analysis_questions: list[DebuggingQuestions]
    previous_suggestions: Optional[list[PreviousSuggestions]]
    new_preconditions: list[NewPrecondition]
    # func_models: list[FunctionModel]

class LLMProofWriter:
    """Proof writer using LLMs to generate CBMC proofs"""

    def __init__(self, openai_api_key, tag_name, test_mode=False):
        self.tag_name = tag_name
        self.harness_name = re.match(r'(.*)_precon_\d+', self.tag_name).group(1)
        if self.harness_name == '_rbuf_add':
            self.harness_name = '_rbuf_add2'

        self.harness_path = os.path.join('..', '..', 'RIOT', 'cbmc', 'proofs', self.harness_name, f'{self.harness_name if self.harness_name != '_rbuf_add2' else '_rbuf_add'}_harness.c')
        self.payload_dir = os.path.join("payloads_v2", self.tag_name)
        self.client = OpenAI(api_key=openai_api_key)
        self.vector_store = self._create_vector_store()
        self.test_mode = test_mode
        

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
            name=self.tag_name,
            chunking_strategy={
                'type': 'static',
                'static': {
                    'chunk_overlap_tokens': 0, #I believe that having this as a non-zero value can cause hallucinations about file contents
                    'max_chunk_size_tokens': 800 # Unsure if this matters

                }
            }
        )
        
        return vector_store

    def _create_vector_store_files(self):
        curr_files = self.client.vector_stores.files.list(self.vector_store.id).data
        if len(curr_files) > 0:
            print(f"WARNING: Vector store {self.vector_store.id} already contains payload files")
            return

        for file_name in os.listdir(self.payload_dir):
            file_info = re.match(fr'{self.tag_name}_(.*).json', file_name).group(1)
            new_file = self.client.vector_stores.files.upload_and_poll(
                vector_store_id=self.vector_store.id,
                file=open(os.path.join(self.payload_dir, file_name), "rb"),
            )
            self.client.vector_stores.files.update(
                vector_store_id=self.vector_store.id,
                file_id=new_file.id,
                attributes={
                    'type': file_info
                }
            )

    def _update_vector_store_files(self):
        """
        Update the harness and error files in the vector store with the latest definitions
        """

        for file in self.client.vector_stores.files.list(self.vector_store.id).data:
            if file.attributes['type'] in ['harness', 'errors']:
                print(f"Updating {file.attributes['type']} file in vector store {self.vector_store.id}")
                self.client.vector_stores.files.delete(
                    vector_store_id=self.vector_store.id,
                    file_id=file.id,
                )
                new_file = self.client.vector_stores.files.upload_and_poll(
                    vector_store_id=self.vector_store.id,
                    file=open(os.path.join(self.payload_dir, f'{self.tag_name}_{file.attributes['type']}.json'), "rb")
                )
                self.client.vector_stores.files.update(
                    vector_store_id=self.vector_store.id,
                    file_id=new_file.id,
                    attributes={
                        'type': file.attributes['type']
                    }
                )

    def _was_error_fixed(self, target_error, all_errors):
        """
        Checks if the target error is in the error clusters and returns the cluster name if it is
        """
        for cluster, errors in all_errors.items():
            for error in errors:
                if error['id'] == target_error['id']:
                    return False
        return True

    def iterate_proof(self, max_attempts=1):
        results = []
        curr_errors = extract_errors_and_payload(self.harness_name, self.tag_name)
        self._create_vector_store_files()
        iterations = 0
        if self.test_mode:
            # Make a copy of the original version of the harness to restore later so we can still checkout git branches
            test_backup = self._backup_harness(backup_suffix='test_backup')

        try:
            for cluster, errors in curr_errors.items():
                advice = get_advice_for_cluster(cluster, self.harness_name)
                target_error = errors[0]  # Assuming we want to analyze the first error in the cluster
                last_response_id = None
                harness_backup = None
                harness_failed = False
                # Loop that attempts to fix a single error in the cluster
                # while not self._was_error_fixed(target_error, curr_errors) and iterations < max_attempts:

                # In a realistic scenario, we would want to only check if 1 error at a time is solved
                # But for this test because we know a precondition that solves all errors exists, we want to force there to be no remaining errors
                while len(curr_errors) > 0 and iterations < max_attempts:

                    # If the target error was not fixed, restore the previous version of the harness
                    if harness_backup is not None:
                        # Restore the harness file to the previous state
                        self._restore_harness(harness_backup)

                    # Send request to LLM for analysis and store results
                    llm_response = self.analyze_errors(target_error, advice, harness_failed, prev_response=last_response_id)
                    last_response_id = llm_response.pop('id')
                    results.append(llm_response)

                    # Implement suggested harness changes and re-run the harness
                    try:
                        harness_backup = self._update_harness(llm_response['response']['new_preconditions'])
                    except Exception as e:
                        print(f"Failed to update harness with new preconditions: {e}")
                        harness_failed = True


                    try:
                        curr_errors = extract_errors_and_payload(self.harness_name, self.tag_name)
                    except Exception as e:
                        print("Suggested precondition caused error in harness file while running make. Reverting changes.")
                        harness_failed = True
                    # Need a special case for if the harness itself has an error

                    iterations += 1
                
                # If the error was resolved, update the harness file in the vector store
                if iterations < max_attempts:
                    # *** I'll figure this out later once we better understand how we want to handle multiple errors at once ***
                    #self._update_vector_store_files()
                    pass
                else:
                    all_fixed = False

                # Remove the backup file
                if harness_backup is not None:
                    os.remove(harness_backup)
                    
        except Exception as e:
            print(f"Exception during proof iteration: {str(e)}")
            return -1, results
        
        finally:
            # Always make sure we try to clean up the uploaded files
            if self.test_mode:
                self._cleanup_testing(test_backup)
        
        return iterations + 1 if self._was_error_fixed(target_error, curr_errors) else 0, results

    def analyze_errors(self, error, advice, harness_failed, prev_response=None):
        SYSTEM_PROMPT = f"""
            You are a helpful AI assistant that is assisting in the development of unit proofs. \
            A unit proof defines an input model of a target function so that the function can be verified with bounded model checking. \
            Our goal is to use preconditions (defined with __CPROVER_assume) to constrain the input model such that no error or violation is reported during verification. \

            We will provide the results of by running the harness, along with the current definition of the harness. \
            In addition, we will provide the definition for any function models that mimic the behavior of complex functions using __CPROVER_assume statements. \
            You can suggest preconditions be added to a function model if you believe an error is the result of a variable returned from that function model. \
            
            We will also provide the definition of all functions reached during the execution of the harness. \
            If a function has a definition of "Unknown", this means the function is undefined. When an undefined function is called, it will return a random value for it's return type. \
            If you believe this random return value is the cause of an error, indicate that the undefined function will require a function model in your response. \

            Your objective is to provide a precondition or list of preconditions that can be added to the existing harness to resolve the given error. \
            You are to follow the steps provided to understand the source of the error and provide your response in the specified JSON format. \
            """
        
            # user_prompt = f"""
            # Provide a minimal set of preconditions that can be added to the current harness definition to resolve the following error:\
            # {error['line']} of {error['function']}: {error['msg']}

            # To assist with debugging, we have provided the values of each variable passed to the target function before the error occured.\
            # The variables are organized based on the context in which they were initialized:\
            # {json.dumps(error['harness_vars'])}

            # Before attempting to resolve the error, answer the following questions to identify the source of the error\
            # and the specific variable(s) that are responsible.
            # {'\n'.join([f'{i + 1}. {step}' for i, step in enumerate(debugging_steps)])}

            # Your response should adhere to the specified JSON format, stating which function in the harness file requires the precondition and the line number where it should be inserted.\
            # Remember this should be a minimal set of preconditions.
            # """
        user_prompt = f"""
            The most recent execution of the harness resulted in the following error with id {error['id']}: "{error['line']} of {error['function']}: {error['msg']}". \
            First, find the current definition of the "harness" function and note which variables have existing preconditions, along with the line numbers for each preconditon. N \
            Then, find the definition of the function where the error occurred. \
            Finally, retrieve the variable values for each modeled variable when error 6d5a48d7-24b1-491f-967b-fb657d11349c occurred. \
            Use this information to answer the following questions and determine the variable responsible for the error: \
            {'\n'.join([f'{i + 1}. {step}' for i, step in enumerate(advice)])}

            Based on this analysis, determine the smallest possible set of preconditions that can be added to the harness to resolve the error. \
            
            Then, translate these preconditions to a valid line of code based on the following criteria:
            1. Your precondition is formatted as __CPROVER_assume(<insert precondition here>). \
            2. Your precondition ONLY uses variables that are defined in the harness. \
            3. Your chosen line number in the function is placed directly after the lines where all variables in the precondition are initialized. \
            4. Your chosen line number is inserted is within the line numbers of the harness function, and is placed before the call to {self.harness_name}. \
            5. It does not repeat any logic found in existing __CPROVER_assume statements in the harness. \
            
            Finally, indicate where the precondition should be inserted by providing the function where it should be inserted and the lines of code that should come directly before and after the new precondition. \

            Provide your response in the specified schema format.
        """ if prev_response is None else f"""
            The previously suggested precondition {'did not resolve the error' if not harness_failed else 'caused an error in the harness. Please ensure your precondition uses valid C syntax and **only** uses variables in the scope of the function where it is inserted'}. \
            First, determine why your previous preconditions were not sufficient to resolve the error. \
            Then, repeat your analysis and provide a new set of preconditions.
        """

        try:
            response = self.client.responses.parse(
                model='gpt-4.1', # Most recent tests were done on 4.1 I think
                instructions=SYSTEM_PROMPT,
                input=[{'role': 'user', 'content': user_prompt}],
                text_format=NewPreconditions,
                previous_response_id=prev_response,
                tool_choice="required",
                tools=[{
                    "type": "file_search",
                    "vector_store_ids": [self.vector_store.id]
                }],
                temperature=1.0, # Sometimes constraints on preconditions are randomly ignored, so hopefully this will help fix it
                include=["file_search_call.results"] 
            )
            print(json.dumps(json.loads(response.output_text), indent=4))
            # output = {"system_prompt": SYSTEM_PROMPT, "user_prompt": user_prompt, "response": response.output_text}
            # with open(f'./responses/{self.tag_name}_response.json', 'w') as f:
            #     json.dump(output, f, indent=4)

            return { 'id': response.id, 'response': json.loads(response.output_text), 'usage': response.usage}
        except Exception as e:
            print(f"Exception while making OpenAI API call: {str(e)}")
        
    def _backup_harness(self, backup_suffix='temp'):
        """
        Create an unmodified copy of the harness file that we can restore
        """
        backup_path = os.path.join(os.path.dirname(self.harness_path), f'{self.harness_name}_harness_{backup_suffix}.c')
        shutil.copy(self.harness_path, backup_path)
        return backup_path

    def _update_harness(self, preconditions):

        backup_path = self._backup_harness()

        with open(self.harness_path, 'r') as f:
            harness_lines = f.readlines()
        
        # By default, use the previous_line_of_code field to insert statements for better consistancy
        # If we can't find a match then rely on the provided line number

        try:
            for precondition in preconditions:
                # First try to match previous_line_of_code
                in_function = False
                found_insertion_point = False
                if "(function model)" in precondition['function']['name']:
                    precondition['function']['name'] = precondition['function']['name'].replace("(function model)", "").strip()
                for line_num, line in enumerate(harness_lines):
                    if not in_function and re.match(fr'\s*[a-zA-Z0-9_]+\s+{precondition['function']['name']}\(.*\)', line): # Find the initial function call
                        in_function = True

                    if not in_function:
                        continue
                    
                    if precondition['previous_line_of_code'] in line:
                        harness_lines.insert(line_num + 1, precondition['precondition_as_code'] + '\n')
                        found_insertion_point = True
                        break
                    elif precondition['next_line_of_code'] in line and precondition['next_line_of_code'] != "":
                        harness_lines.insert(line_num, precondition['precondition_as_code'] + '\n')
                        found_insertion_point = True
                        break
        except IndexError as e:
            print(f"Error inserting precondition: {e}")
            raise e

        if not found_insertion_point:
            raise ValueError(f"Could not find line matching LLM-provided precondition")

        with open(self.harness_path, 'w') as f:
            f.writelines(harness_lines)

        return backup_path

    def _restore_harness(self, backup_path):
        if not os.path.exists(backup_path):
            print(f"Backup file {backup_path} does not exist. Cannot restore harness.")
            return

        shutil.copy(backup_path, self.harness_path)
        print(f"Restored harness from {backup_path} to {self.harness_path}")
        os.remove(backup_path)
    
    def _cleanup_testing(self, test_backup):
        """
        Deletes the vector store and all files associated with the tag name
        Then moves the updated harness into a different file and restores the original harness file from the backup
        """
        file_ids = self.client.vector_stores.files.list(self.vector_store.id)
        for file in file_ids:
            print(f"Deleting file {file.id} from vector store {self.vector_store.id}")
            self.client.files.delete(file_id=file.id)

        self.client.vector_stores.delete(self.vector_store.id)
        print(f"Deleted vector store {self.vector_store.id} for tag {self.tag_name}")

        results_path = './results'
        shutil.move(self.harness_path, os.path.join(results_path, f'{self.tag_name}_harness.c'))
        print("Saved a copy of the final harness to the results directory")
        os.rename(test_backup, self.harness_path)
        print("Restored original harness file")