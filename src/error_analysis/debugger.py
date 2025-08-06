import os
import json
import re
import shutil
import traceback
from error_analysis.advice import get_advice_for_cluster
from error_analysis.parser import extract_errors_and_payload
from error_analysis.error_classes import CoverageError, PreconditionError, InsertError
from error_analysis.output_models import ModelOutput
from error_analysis.error_report import ErrorReport
from agent import OpenAIAgent

class LLMProofDebugger(OpenAIAgent):
    """Proof writer using LLMs to generate CBMC proofs"""


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
        If your suggested precondition fails to resolve an error, it is generally better to consider other variables that could be a factor in causing the error, \
        rather than adding more specific and complex constraints to a single variable.
        """

    def __init__(self, openai_api_key, harness_path, test_mode=False):
        super().__init__(
            openai_api_key,
            agent_name="debugger",
            harness_name=os.path.basename(harness_path).replace('_harness.c', ''),
            harness_path=harness_path,
            test_mode=test_mode,
            chunking_strategy={
                'type': 'static',
                'static': {
                    'chunk_overlap_tokens': 0, #I believe that having this as a non-zero value can cause hallucinations about file contents
                    'max_chunk_size_tokens': 800 # Unsure if this matters
                } 
            }
        )

        if self.harness_name == '_rbuf_add':
            self.harness_name = '_rbuf_add2'

        self.payload_dir = os.path.join("payloads", self.harness_name)



    def _create_vector_store_files(self):
        curr_files = self.client.vector_stores.files.list(self.vector_store.id).data
        if len(curr_files) > 0:
            print(f"WARNING: Vector store {self.vector_store.id} already contains payload files")
            return

        payload_dir = os.path.join("payloads", self.harness_name)
        for file_name in os.listdir(payload_dir):
            file_info = re.match(fr'{self.harness_name}_(.*).json', file_name).group(1)
            new_file = self.client.vector_stores.files.upload_and_poll(
                vector_store_id=self.vector_store.id,
                file=open(os.path.join(payload_dir, file_name), "rb"),
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
                    file=open(os.path.join("payloads", self.harness_name, f'{self.harness_name}_{file.attributes['type']}.json'), "rb")
                )
                self.client.vector_stores.files.update(
                    vector_store_id=self.vector_store.id,
                    file_id=new_file.id,
                    attributes={
                        'type': file.attributes['type']
                    }
                )

    def iterate_proof(self, max_attempts=1):

        error_report = ErrorReport(
            extract_errors_and_payload(self.harness_name, self.harness_path)
        ) # The initial error report we will be maintaining

        self._create_vector_store_files()

        # We need to keep track of the lines we add because adding lines to the harness shifts the line numbers of any errors within the harness, and thus changes their IDs
        new_precon_line_nums = [] 
        try:
                cluster, target_error_id, target_error = error_report.get_next_error()
                while target_error is not None:
                    
                    if target_error_id in error_report.resolved_errs or target_error_id in error_report.failed_errs:
                        print(f"WARNING: Error {target_error_id} was already processed and somehow reappeared")

                    advice = get_advice_for_cluster(cluster, self.harness_name)

                    last_response_id = None # For API call
                    cause_of_failure = None # For tracking why last run failed to provide targetted prompts
                    harness_backup = None # For restoring previous harness definitions after a failed new preconditions
                    err_was_resolved = False # For exiting the loop

                    iterations = 0

                    print(f"Attempting to resolve error {target_error}")

                    while not err_was_resolved and iterations < max_attempts * 2:

                        if harness_backup is not None:
                            self._restore_harness(harness_backup)
                            if cause_of_failure is None: # If the harness didn't fail for a specific reason, it just simply didn't fix the error
                                target_error.responses[-1]['reason_for_failure'] = "Suggested precondition did not fix error"

                        llm_response = self.request_llm_analysis(target_error, advice, cause_of_failure=cause_of_failure, prev_response=last_response_id)

                        last_response_id = llm_response.pop('id')
                        
                        target_error.responses.append(llm_response['response'])
                        target_error.tokens['input'] += llm_response['usage'].input_tokens
                        target_error.tokens['output'] += llm_response['usage'].output_tokens

                        if iterations > 0:
                            target_error.tokens['cached'] = llm_response['usage'].input_tokens_details.cached_tokens

                        # Implement suggested harness changes and re-run the harness
                        try:
                            harness_backup, added_precons, added_precon_line_nums = self._update_harness(llm_response['response']['new_preconditions'])
                            
                        except Exception as e:
                            # In the case of a failure due to insertion errors, we only penalize a "half-attempt" to give the LLM more attempts to correct it's previous suggestion

                            if isinstance(e, InsertError):
                                print(f"Failed to update harness with new preconditions: {e}")

                                # These "cause_of_failure" objs will have a reason that is used to update the prompt, and data to help the llm debug
                                cause_of_failure = { 'reason': "harness_update_error", 'error': e }
                                target_error.responses[-1]['reason_for_failure'] = "Failed to insert the suggested precondition into harness"
                                iterations += 1
                                continue
                            else:
                                print("ERROR in harness update: ", traceback.format_exc())
                                raise e

                        try:
                            curr_errors = ErrorReport(
                                extract_errors_and_payload(
                                    self.harness_name, 
                                    self.harness_path, 
                                    check_for_coverage=target_error, 
                                    new_precon_lines=(added_precon_line_nums + new_precon_line_nums)
                                )
                            )
                            cause_of_failure = None
                        except Exception as e:

                            # In the case of a failure due to syntax/coverage errors, we only penalize a "half-attempt" to give the LLM more attempts to correct it's previous suggestion

                            if isinstance(e, SyntaxError):
                                print(f"Suggested precondition caused error while running make due to invalid syntax: {e}.")
                                cause_of_failure = { "reason": "syntax_error", 'error': e }
                                target_error.responses[-1]['reason_for_failure'] = "Suggested precondition caused a syntax error in harness"
                                iterations += 1
                                continue
                                                           
                            elif isinstance(e, CoverageError):
                                print("Suggested precondition prevented coverage of the line with the target error.")
                                cause_of_failure = { "reason": "coverage_error", "error": e }
                                target_error.responses[-1]['reason_for_failure'] = "Suggested precondition prevented coverage of the line where error occured"
                                iterations += 1
                                continue
                            elif isinstance(e, PreconditionError):
                                print("Suggested precondition introduced new errors to harness")
                                cause_of_failure = {"reason": "precondition_error", "error": e }
                                target_error.responses[-1]['reason_for_failure'] = "Suggested precondition contained CBMC errors"
                                iterations += 1
                                continue
                            else:
                                if harness_backup is not None:
                                    os.remove(harness_backup)
                                    print("Reverting changes to harness file")
                                raise e

                        err_was_resolved = target_error_id not in curr_errors 
                        if not err_was_resolved:
                            # Update the variable values of the target error from the latest run
                            error_report.get_err(target_error_id).update(curr_errors.get_err(target_error_id))


                        iterations += 2
                    
                    # If we exited the bc loop the error was resolved
                    if err_was_resolved:
                        
                        self._update_harness_in_vector_store() # Update the definition of the harness in the vector store

                        target_error.attempts = iterations // 2
                        target_error.added_precons = added_precons

                        # Update the line numbers where preconditions were added
                        for line in added_precon_line_nums:
                            for i in range(len(new_precon_line_nums)):
                                if new_precon_line_nums[i] >= line:
                                    new_precon_line_nums[i] += 1
                        new_precon_line_nums.extend(added_precon_line_nums) # Keep track of the lines we added so we can update the error IDs

                        error_report.update_all_errs(target_error_id, curr_errors)
                        print(f"Successfully resolved error {target_error}\n")

                    else:
                        print(f"Failed to resolve error {target_error}")

                        error_report.update_failed_err(target_error_id)
                        if cause_of_failure is None:
                            target_error.responses[-1]['reason_for_failure'] = "Suggested precondition did not fix error"

                        if harness_backup is not None:
                            self._restore_harness(harness_backup)
                            harness_backup = None

                    # Remove the backup file
                    if harness_backup is not None:
                        os.remove(harness_backup)

                    cluster, target_error_id, target_error = error_report.get_next_error()
                    
        except Exception as e:
            print("Exception during proof iteration: ", traceback.format_exc())
            raise e
        
        self._cleanup_vector_store()
        return error_report.generate_results_report()

    def request_llm_analysis(self, error, advice, cause_of_failure=None, prev_response=None):
        
        if prev_response is None:
            user_prompt = f"""The most recent execution of the harness resulted in the following error: \
                {error.msg}\n
                {'\n'.join([f'in {func}, Line: {line}' for func, line in error.stack])}

                The value of each variable passed into the target function before the error occurred, grouped by the scope they were initalized in, is provided below: \
                {json.dumps(error.vars, indent=4)}

                Complete the following steps to determine an appropriate precondition that will resolve the error. \
                First, find the current definition of the "harness" function and note which variables have existing preconditions, along with the line numbers for each preconditon. \
                Next, find the definition of the function where the error occurred. \
                Finally, list out the variable values for each modeled variable when error occured. \
                
                Then, provide a general analysis of the error and the variables involved.

                Finally, use this information to answer the following questions and determine the variable responsible for the error: \
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

                # TODO: Refer to variable values to help the LLM determine if it's previous precondition had the intended impact, and allow it to find different "paths" to the error 
                user_prompt = f"""
                    The previously suggested precondition did not resolve the provided error. \
                    The updated value of each variable during the previous run, grouped by the scope they were defined in, is provided below: \
                    {json.dumps(error.vars, indent=4)}

                    Your first objective is to evaluate the preconditions you suggested adding in your previous response, and determine whether they should still be included in the harness.

                    For each precondition you suggested in your last response, use the updated variable values provided to answer the following questions: \
                    1. What potential cause for the error was addressed by this precondition? \
                    2. Based on the updated variable values provided, did this precondition sufficiently constrain the variable(s) to address the potential cause of the error? \
                    3. If the variable(s) in the precondition were sufficiently constrained, is this precondition absolutely necessary to ensure there is no error? \
                    4. If the variable(s) in the precondition were NOT constrained properly, is this suggeted precondition SIMPLE while covering all use cases of that variable? \
                    5. If the error occured in a helper function to the main harness function, is the precondition placed in an appropriate location? \
                    6. Are there other DISTINCT variables that could also be contributing to the error that were not addressed by this precondition? \
                    
                    Based on your responses to these questions, determine if this precondition  should be kept without changes, kept with modified constraints, or discarded. \
                    If you determined a this precondition should be kept, then include it in the "new_preconditions" output field. \
                    If you determined a this precondition should be kept with modified constraints, then include the modified version of the precondition in the "new_preconditions" output field. \
                    
                    Your second objective is to repeat the analysis steps laid out in the initial prompt using the updated variable values, and explore other variables that may need preconditions to prevent the error.
                    Based on this analysis and your analysis of your previously suggested preconditions, provide an improved set of preconditions.
                """
            elif cause_of_failure['reason'] == 'harness_update_error':
                # If we couldn't find the place where we were supposed to insert the precondition into the harness
                user_prompt = f"""
                    When attempting to insert the new preconditions into the harness, we could not find a match for the previous line of code you provided: {cause_of_failure['error'].prev_line}. \
                    
                    Please use the following steps to understand the cause of the error and fix your precondition appropriately.
                    1. Confirm this line of code is actually exists in the harness file and the syntax matches exactly. \
                    2. If the syntax was mismatched, please provide the same precondition but with a previous line of code that exactly matches the syntax. \
                    3. If the syntax is correct, double-check that this previous line of code exists in the {cause_of_failure['error'].func} function. \
                    4. If you did not provide the correct function in your response, please provide the same precondition with the correct function that contains the previous line of code. \
                    
                    Follow this debugging process to revise your previous precondition.
                """
            elif cause_of_failure['reason'] == 'syntax_error':
                # If make failed to run, which is most likely due to a syntax error
                user_prompt = """
                    The previously suggested precondition caused a syntax error in the harness. \
                    
                    Review each of your previously suggested preconditions and check if the following requirements are met: \
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
                instructions=LLMProofDebugger.SYSTEM_PROMPT,
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
        backup_path = os.path.join(os.path.dirname(self.harness_path), f'{self.harness_name}_harness_{backup_suffix}.c')
        shutil.copy(self.harness_path, backup_path)
        return backup_path

    def _update_harness(self, preconditions):

        backup_path = self._backup_harness()
        new_precons = []
        new_precon_lines = []

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
                    if not in_function and re.match(fr'\s*[a-zA-Z0-9_ \*\s]+{precondition['function']['name']}\(', line): # Find the initial function call
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
                    raise InsertError("Could not find line matching LLM-provided precondition", prev_line=precondition['previous_line_of_code'], next_line=precondition['next_line_of_code'], func=precondition['function']['name'])        
                
        except IndexError as e:
            print(f"Error inserting precondition: {e}")
            raise e

        with open(self.harness_path, 'w') as f:
            f.writelines(harness_lines)

        return backup_path, new_precons, new_precon_lines

    def _restore_harness(self, backup_path):
        if not os.path.exists(backup_path):
            print(f"Backup file {backup_path} does not exist. Cannot restore harness.")
            return

        shutil.copy(backup_path, self.harness_path)
        print(f"Restored harness from {backup_path} to {self.harness_path}")
        os.remove(backup_path)

