import os
import json
import re
import shutil
import traceback
from openai import OpenAI
from advice import get_advice_for_cluster
from parser import extract_errors_and_payload
from error_classes import CoverageError, PreconditionError, InsertError
from output_models import ModelOutput




class LLMProofWriter:
    """Proof writer using LLMs to generate CBMC proofs"""

    # Ideally, this ordering should address all instances of earlier errors "obscuring" later errors
    # For example, some deref_arr_oob errors can be obscured by null pointer errors and won't show up until those are fixed
    CLUSTER_ORDER = [
            'deref_null',
            'memcpy_src',
            'memcpy_dest',
            'memcpy_overlap',
            'arithmetic_overflow',
            'deref_arr_oob',
            'deref_obj_oob',
            'misc'
        ]

    SYSTEM_PROMPT = """
        You are a helpful AI assistant that is assisting in the development of unit proofs. \
        A unit proof defines an input model of a target function so that the function can be verified with bounded model checking. \
        Our goal is to use preconditions (defined with __CPROVER_assume) to constrain the input model such that no error or violation is reported during verification. \

        We will provide the results of running the harness, including all errors generated, along with the current definition of the harness. \
        In addition, we will provide the definition for any function models used in the harness. Function models are functions that mimic the behavior of more complex functions using __CPROVER_assume statements. \
        If you believe an error is the result of a variable returned from a function model, you should add your precondition to the function model instead of to the harness function. \
        
        We will also provide the definition of all functions reached during the execution of the harness. \
        If a function has a definition of "Unknown", this means the function is undefined. When an undefined function is called, it will return a random value for it's return type. \
        If you believe this random return value is the cause of an error, indicate that the undefined function will require a function model in your response. \

        Your objective is to provide a precondition or list of preconditions that can be added to the existing harness to resolve the given error. \
        You are to follow the steps provided to understand the source of the error and provide your response in the specified JSON format. \
        
        *** IMPORTANT ***
        You should aim to keep your preconditions as logically simple as possible and rely on simple comparisons. Highly complex preconditions are rarely necessary. \
        If your suggested precondition fails to resolve an error, it is generally better to consider other variables that could be contributing to the error, rather than adding more specific and complex constraints to a single variable.
        """

    def __init__(self, openai_api_key, harness_path, test_mode=False):
        self.full_harness_path = harness_path
        self.harness_dir = os.path.dirname(harness_path)
        self.harness_name = os.path.basename(harness_path).replace('_harness.c', '')
        if self.harness_name == '_rbuf_add':
            self.harness_name = '_rbuf_add2'

        self.payload_dir = os.path.join("payloads_v2", self.harness_name)
        self.client = OpenAI(api_key=openai_api_key)
        self.test_mode = test_mode
        self.vector_store = self._create_vector_store()

        

    def _create_vector_store(self):
        """
        Checks if a vector store already exists and initializes it with the relevant files if it does not
        """

        for store in self.client.vector_stores.list():
            if store.name == self.harness_name:
                print(f"Found existing vector store with ID {store.id}")
                if self.test_mode:
                    print(f"Cleaning up old vector store {store.id} for testing")
                    self.vector_store = store
                    self._cleanup_vector_store()
                else:
                    return store

        print(f"Initializing vector store for {self.harness_name}")
        vector_store = self.client.vector_stores.create(
            name=self.harness_name,
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
            file_info = re.match(fr'{self.harness_name}_(.*).json', file_name).group(1)
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

    def _update_harness_in_vector_store(self):
        """
        Update the harness files in the vector store with the latest definitions
        """

        for file in self.client.vector_stores.files.list(self.vector_store.id).data:
            if file.attributes['type'] == 'harness':
                print(f"Updating {file.attributes['type']} file in vector store {self.vector_store.id}")
                self.client.vector_stores.files.delete(
                    vector_store_id=self.vector_store.id,
                    file_id=file.id,
                )
                self.client.files.delete(file_id=file.id)
                new_file = self.client.vector_stores.files.upload_and_poll(
                    vector_store_id=self.vector_store.id,
                    file=open(os.path.join(self.payload_dir, f'{self.harness_name}_{file.attributes['type']}.json'), "rb")
                )
                self.client.vector_stores.files.update(
                    vector_store_id=self.vector_store.id,
                    file_id=new_file.id,
                    attributes={
                        'type': file.attributes['type']
                    }
                )

    def _err_to_str(self, error_obj):
        return f"{error_obj['msg']} @ {error_obj['function']} {error_obj['line']}"

    def iterate_proof(self, max_attempts=1):
        # NEED TO:
        # - Re-work the "error in harness" logic
        # - Adjust how it iterates through errors and clusters to account for more errors being added in later clusters
        #   and for errors in the same cluster being removed
        curr_errors = extract_errors_and_payload(self.harness_name, self.harness_dir)
        init_error_mapping = dict() # This only needed to help with identifying the "also resolved errors"
        results_report = {
            'initial_errors': dict(),
            'processed_errors': {
                'success': dict(),
                'failure': dict()
            },
            'preconditions_added': [],
        }
        for cluster, errors in curr_errors.items():
            results_report['initial_errors'][cluster] = []
            for err_id, err in errors.items():
                results_report['initial_errors'][cluster].append(self._err_to_str(err))
                init_error_mapping[err_id] = self._err_to_str(err)
        results_report['initial_errors']['total'] = len(init_error_mapping)

        self._create_vector_store_files()
        
        added_precon_lines = [] # We need to keep track of the lines we add because adding lines to the harness shifts the line numbers of errors, and thus changes their IDs
        try:
            for curr_cluster in LLMProofWriter.CLUSTER_ORDER:
                if curr_cluster not in curr_errors:  
                    continue
                advice = get_advice_for_cluster(curr_cluster, self.harness_name)

                # With the way this works currently, I think that any new errors that happen to be added to the same cluster will be ignored
                while curr_cluster in curr_errors and len(curr_errors[curr_cluster]) > 0:
                    # *** IMPORTANT ***
                    # We are currently take a niave approach, which means we:
                    # - Only check if the error we are targeting was fixed and ignore if any others were fixed/added in the process
                    # - Assume the error can be fixed in a single set of preconditions (as opposed to something like _rbuf_add, where one error requires several distinct preconditions)

                    target_error_id = None
                    for err in curr_errors[curr_cluster]:
                        if err not in results_report['processed_errors']['failure']:
                            target_error_id = err
                            break
                    
                    if target_error_id is None:
                        break
                    
                    elif target_error_id in results_report['processed_errors']['success']:
                        print(f"WARNING: Error {target_error_id} was already processed and somehow reappeared")

                    target_error = curr_errors[curr_cluster][target_error_id] # Recognizable string for this particular error
                    last_response_id = None
                    cause_of_failure = None
                    harness_backup = None
                    curr_error_report = {
                        'function': target_error['function'],
                        'line': target_error['line'],
                        'msg': target_error['msg'],
                        'attempts': -1,
                        'added_precons': None,
                        'indirectly_resolved': [],
                        'tokens': {
                            'input': 0,
                            'output': 0, 
                            'cached': 0
                        },
                        'responses': []
                    }
                    iterations = 0
                    print(f"Attempting to resolve error {self._err_to_str(target_error)}")

                    while curr_cluster in curr_errors and target_error_id in curr_errors[curr_cluster] and iterations < max_attempts:

                        # TBD: If the provided precondition does not break the harness but also does not fix the error, do we want to keep the suggestion and provide the updated harness to the LLM?
                        # Current implementation assumes we do not keep harness changes that do not fix the error, but this could be changed
                        if harness_backup is not None:
                            self._restore_harness(harness_backup)
                            curr_error_report['responses'][-1]['reason_for_failure'] = "Suggested precondition did not fix error"

                        # By only passing in the error using the target_error_id, we can pass in the updated variable values after failure
                        # With that said the LLM doesn't currently use them
                        llm_response = self.request_llm_analysis(curr_errors[curr_cluster][target_error_id], advice, cause_of_failure=cause_of_failure, prev_response=last_response_id)
                        last_response_id = llm_response.pop('id')
                        curr_error_report['responses'].append(llm_response['response'])
                        curr_error_report['tokens']['input'] += llm_response['usage'].input_tokens
                        curr_error_report['tokens']['output'] += llm_response['usage'].output_tokens
                        if iterations > 0:
                            curr_error_report['tokens']['cached'] = llm_response['usage'].input_tokens_details.cached_tokens


                        # Implement suggested harness changes and re-run the harness
                        try:
                            harness_backup, new_precons, new_precon_lines = self._update_harness(llm_response['response']['new_preconditions'])
                        except Exception as e:
                            if isinstance(e, InsertError):
                                print(f"Failed to update harness with new preconditions: {e}")

                                # These "cause_of_failure" objs will have a reason that is used to update the prompt, and data to help the llm debug
                                cause_of_failure = { 'reason': "harness_update_error", 'error': e }
                                curr_error_report['responses'][-1]['reason_for_failure'] = "Failed to insert the suggested precondition into harness"
                                continue
                            else:
                                print("ERROR in harness update: ", traceback.format_exc())
                                raise e

                        try:
                            curr_errors = extract_errors_and_payload(self.harness_name, self.harness_dir, check_for_coverage=target_error, new_precon_lines=(new_precon_lines + added_precon_lines))
                            cause_of_failure = None
                        except Exception as e:
                            if isinstance(e, SyntaxError):
                                print(f"Suggested precondition caused error while running make due to invalid syntax: {e}.")
                                cause_of_failure = { "reason": "syntax_error", 'error': e }
                                curr_error_report['responses'][-1]['reason_for_failure'] = "Suggested precondition caused a syntax error in harness"
                                                           
                            elif isinstance(e, CoverageError):
                                print("Suggested precondition prevented coverage of the line with the target error.")
                                cause_of_failure = { "reason": "coverage_error", "error": e }
                                curr_error_report['responses'][-1]['reason_for_failure'] = "Suggested precondition prevented coverage of the line where error occured"
                            elif isinstance(e, PreconditionError):
                                print(f"Suggested precondition introduced new errors to harness")
                                cause_of_failure = {"reason": "precondition_error", "error": e }
                                curr_error_report['responses'][-1]['reason_for_failure'] = "Suggested precondition contained CBMC errors"
                            else:
                                if harness_backup is not None:
                                    os.remove(harness_backup)
                                raise e
                            print("Reverting changes to harness file")
        
                        iterations += 1
                    
                    # If the error was resolved, update the harness file in the vector store
                    if curr_cluster not in curr_errors or target_error_id not in curr_errors[curr_cluster]:
                        self._update_harness_in_vector_store()
                        curr_error_report['attempts'] = iterations
                        curr_error_report['added_precons'] = new_precons
                        results_report['preconditions_added'].extend(new_precons)

                        # Update the lines where these were added
                        for line in new_precon_lines:
                            for i in range(len(added_precon_lines)):
                                if added_precon_lines[i] >= line:
                                    added_precon_lines[i] += 1
                        added_precon_lines.extend(new_precon_lines) # Keep track of the lines we added so we can update the error IDs
                        
                        init_error_mapping.pop(target_error_id, None) # Remove the error from the initial error mapping
                        remaining_errors = set([err_id for cluster in curr_errors.values() for err_id in cluster.keys()])
                        init_errors = set(init_error_mapping.keys())
                        also_resolved = init_errors - remaining_errors

                        for err_id in also_resolved:
                            error_str = init_error_mapping.pop(err_id)
                            curr_error_report['indirectly_resolved'].append(error_str)
                            if err_id in results_report['processed_errors']['failure']:
                                results_report['processed_errors']['failure'][err_id]['resolved_by'] = self._err_to_str(target_error)

                        if len(remaining_errors - init_errors) > 0:
                            print(f"WARNING: new errors introduced by precondition:\n")
                            for cluster in curr_errors:
                                for err in curr_errors[cluster]:
                                    if err not in init_errors:
                                        print(f"  {self._err_to_str(curr_errors[cluster][err])}")

                        results_report['processed_errors']['success'][target_error_id] = curr_error_report

                        print(f"Successfully resolved error {self._err_to_str(target_error)}\n")

                    else:
                        print(f"Failed to resolve error {self._err_to_str(target_error)}")

                        if not 'reason_for_failure' in curr_error_report['responses'][-1]:
                            curr_error_report['responses'][-1]['reason_for_failure'] = "Suggested precondition did not fix error"

                        curr_errors[curr_cluster].pop(target_error_id, None) # Remove the error from the current errors
                        results_report['processed_errors']['failure'][target_error_id] = curr_error_report

                        if harness_backup is not None:
                            self._restore_harness(harness_backup)
                            harness_backup = None

                    # Remove the backup file
                    if harness_backup is not None:
                        os.remove(harness_backup)
                    
        except Exception as e:
            print(f"Exception during proof iteration: ", traceback.format_exc())
            raise e
        
        self._cleanup_vector_store()
        return results_report

    def request_llm_analysis(self, error, advice, cause_of_failure=None, prev_response=None):
        
        if prev_response is None:
            user_prompt = f"""The most recent execution of the harness resulted in the following error: \
                {error['msg']}\n
                {'\n'.join([f'in {func}, Line: {line}' for func, line in error['stack']])}

                The value of each variable passed into the target function before the error occurred, grouped by the scope they were initalized in, is provided below: \
                {json.dumps(error['harness_vars'], indent=4)}

                Complete the following steps to determine an appropriate precondition that will resolve the error. \
                First, find the current definition of the "harness" function and note which variables have existing preconditions, along with the line numbers for each preconditon. \
                Next, find the definition of the function where the error occurred. \
                Finally, list out the variable values for each modeled variable when error occured. \
                Use this information to answer the following questions and determine the variable responsible for the error: \
                {'\n'.join([f'{i + 1}. {step}' for i, step in enumerate(advice)])}

                Based on this analysis, determine the smallest possible set of preconditions that can be added to the harness to resolve the error. \
                
                Then, translate these preconditions to a valid line of code based on the following criteria: \
                1. Your precondition is formatted as __CPROVER_assume(<precondition>). \
                2. Your precondition ONLY uses variables that are defined in the harness. \
                3. Your chosen line number in the function is placed directly after the lines where all variables in the precondition are initialized. \
                4. Your chosen line number is inserted is within the line numbers of the harness function, and is placed before the call to {self.harness_name}. \
                5. It does not repeat any logic found in existing __CPROVER_assume statements in the harness. \
                
                Finally, indicate where the precondition should be inserted by providing the function where it should be inserted and the lines of code that should come directly before and after the new precondition. \

                Provide your response in the specified schema format.
            """ 
        else:
            if cause_of_failure is None:
                # If the error just wasn't resolved by the new precondition
                user_prompt = f"""
                    The previously suggested precondition did not resolve the provided error. \
                    The value of each variable during the previous run, grouped by the scope they were defined in, is provided below: \
                    {json.dumps(error['harness_vars'], indent=4)}

                    First, use these values to answer the following questions and evaluate each of your your previously suggested preconditions. \
                    1. Were the variables constrained by the precondition actually relevant to the error? \
                    2. If the variables were correct, were the constraints on these variables SIMPLE and SUFFICIENT? \
                    3. If the variables were correct and the constraints were simple and sufficient, did you place the precondition in the correct place in the harness? \
                    4. If all of the above are true, are there other variables that could be contributing to the error? \
                    5. If all of the above are true, should you keep this precondition in the harness? If so, include it in your next response. \
                                    
                    Then, use the provided variable values to repeat your analysis and provide an improved set of preconditions.
                """
            elif cause_of_failure['reason'] == 'harness_update_error':
                # If we couldn't find the place where we were supposed to insert the precondition into the harness
                user_prompt = f"""
                    When attempting to insert the new preconditions into the harness, we could not find a match for the previous line of code you provided: {cause_of_failure['error'].prev_line}. \
                    First, confirm that this line of code is actually exists in the harness file and the syntax matches exactly. \
                    If the syntax was mismatched, please provide a fixed version with exactly matching syntax. \
                    If the syntax is correct, double-check the function where that line of code is defined. \
                    If you did not provide the correct function in your response, please adjust accordingly. \
                """
            elif cause_of_failure['reason'] == 'syntax_error':
                # If make failed to run, which is most likely due to a syntax error
                user_prompt = """
                    The previously suggested precondition caused a syntax error in the harness. \
                    Review your previously suggested preconditions and check if the following requirements are met: \
                    1. The precondition only uses valid C syntax \
                    2. The precondition only uses variables in scope of your chosen function (local or global variables). \
                    3. For each variable used in your pre-condition, was it defined before the line where the precondition was inserted? \
                    
                    Based on these steps, determine why your previous preconditions caused a syntax error in the harness. \
                    If you believe the precondition is still logically valid, provide a revised version of the precondition that fixes the syntax error. \
                    
                    Otherwise, repeat your analysis and provide an improved set of preconditions.
                """
            elif cause_of_failure['reason'] == 'coverage_error':
                # If the target error line is no longer covered
                user_prompt = """
                    Harness execution no longer reaches the line where the error occurred. \
                    First, check for any conditional statements in the target function that could prevent coverage of the failing line. \
                    
                    Then, determine why your previous precondition may have prevented coverage of the failing line. \

                    Finally, create a new precondition that still resolves the error without reducing line coverage in the target function.
                """
            elif cause_of_failure['reason'] == 'precondition_error':
                user_prompt = f"""
                    The previously suggested precondition introduced the following new CBMC errors to the harness: \
                    {cause_of_failure['error'].new_errors}
                    
                    First, consider the variables you used in the precondition and determine why they caused new errors in the harness. \
                    Then, evaluate which of these variables are absolutely necessary to resolve the original error, and restrict your next response to only those variables. \
                    Finally, provide a revision of your precondition or suggest additional new preconditions that will prevent any memory errors from occuring within the definitions of your preconditions. \
                """
        try:
            response = self.client.responses.parse(
                model='gpt-4.1', # Most recent tests were done on 4.1 I think
                instructions=LLMProofWriter.SYSTEM_PROMPT,
                input=[{'role': 'user', 'content': user_prompt}],
                text_format=ModelOutput,
                previous_response_id=prev_response,
                tool_choice="required",
                tools=[{
                    "type": "file_search",
                    "vector_store_ids": [self.vector_store.id]
                }],
                temperature=1.0, # Sometimes constraints on preconditions are randomly ignored, so hopefully this will help fix it
                include=["file_search_call.results"] 
            )
            # print(f"Newly suggested preconditions:\n{'\n'.join([precon["precondition_as_code"] for precon in json.loads(response.output_text)["new_preconditions"]])}")
            print(f"Newly suggested preconditions:\n{json.dumps(json.loads(response.output_text)["new_preconditions"], indent=4)}")
            return { 'id': response.id, 'response': json.loads(response.output_text), 'usage': response.usage}
        except Exception as e:
            print(f"Exception while making OpenAI API call: {str(e)}")
        
    def _backup_harness(self, backup_suffix='temp'):
        """
        Create an unmodified copy of the harness file that we can restore
        """
        backup_path = os.path.join(self.harness_dir, f'{self.harness_name}_harness_{backup_suffix}.c')
        shutil.copy(self.full_harness_path, backup_path)
        return backup_path

    def _update_harness(self, preconditions):

        backup_path = self._backup_harness()
        new_precons = []
        new_precon_lines = []

        with open(self.full_harness_path, 'r') as f:
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
                    if not in_function and re.match(fr'\s*[a-zA-Z0-9_ \*]+\s+{precondition['function']['name']}\(', line): # Find the initial function call
                        in_function = True

                    if not in_function:
                        continue
                    
                    # Remove all whitespace bc it can sometimes cause a false mismatch                    
                    prev_line = precondition['previous_line_of_code'].replace(' ', '').strip()
                    next_line = precondition['next_line_of_code'].replace(' ', '').strip()
                    line = line.replace(' ', '').strip()

                    if prev_line in line:
                        harness_lines.insert(line_num + 1, precondition['precondition_as_code'] + '\n')
                        new_precons.append(f"{precondition['precondition_as_code']} @ {precondition['function']['name']}:{line_num + 1}")
                        new_precon_lines.append(line_num + 2) # Do + 2 because line numbers are 1-indexed
                        found_insertion_point = True
                        break
                    elif next_line != "" and next_line in line:
                        harness_lines.insert(line_num, precondition['precondition_as_code'] + '\n')
                        new_precons.append(f"{precondition['precondition_as_code']} @ {precondition['function']['name']}:{line_num}")
                        new_precon_lines.append(line_num + 1)
                        found_insertion_point = True
                        break
                if not found_insertion_point:
                    raise InsertError("Could not find line matching LLM-provided precondition", prev_line=precondition['previous_line_of_code'], next_line=precondition['next_line_of_code'])        
                
        except IndexError as e:
            print(f"Error inserting precondition: {e}")
            raise e

        with open(self.full_harness_path, 'w') as f:
            f.writelines(harness_lines)

        return backup_path, new_precons, new_precon_lines

    def _restore_harness(self, backup_path):
        if not os.path.exists(backup_path):
            print(f"Backup file {backup_path} does not exist. Cannot restore harness.")
            return

        shutil.copy(backup_path, self.full_harness_path)
        print(f"Restored harness from {backup_path} to {self.full_harness_path}")
        os.remove(backup_path)
    
    def _cleanup_vector_store(self):
        """
        Deletes the vector store and all files associated with the tag name
        Then moves the updated harness into a different file and restores the original harness file from the backup
        """
        if self.vector_store.id not in [store.id for store in self.client.vector_stores.list()]:
            return

        file_ids = self.client.vector_stores.files.list(self.vector_store.id)
        for file in file_ids:
            print(f"Deleting file {file.id} from vector store {self.vector_store.id}")
            self.client.files.delete(file_id=file.id)

        self.client.vector_stores.delete(self.vector_store.id)
        print(f"Deleted vector store {self.vector_store.id} for {self.harness_name}")
