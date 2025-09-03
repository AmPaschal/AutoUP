from openai import OpenAI
from abc import ABC, abstractmethod
import random
import time
import logging
import traceback
from typing import Any, Callable, Type

class AIAgent(ABC):
    """
    Shared features for any OpenAI agent that interacts with a vector store
    """

    def __init__(self, agent_name, test_mode=False, chunking_strategy=None):
        self.agent_name = agent_name
        self.store_name = f'{agent_name}-store'
        self.test_mode = test_mode
        self._max_attempts = 5
        # self.vector_store = self._create_vector_store(chunking_strategy)


    def _create_vector_store(self, chunking_strategy):
        """
        Checks if a vector store already exists
        If it does already exist, it will clear all files inside and return the existing vector store
        """

        for store in self.client.vector_stores.list():
            if store.name == self.store_name:
                print(f"Found existing vector store with ID {store.id}")
                if self.test_mode:
                    print(f"Cleaning up old vector store {store.id} for testing")
                    self.vector_store = store
                    self._cleanup_vector_store()
                else:
                    return store

        print(f"Initializing vector store for {self.store_name}")
        vector_store = self.client.vector_stores.create(
            name=self.store_name,
            chunking_strategy=chunking_strategy # If chunking_strategy is None, it will use OpenAI's default chunking strategy (max_chunk_size_tokens = 800, chunk_overlap_tokens = 400)
        )
        
        return vector_store

    @abstractmethod
    def _upload_vector_store_files(self, file_path):
        """
        Actually upload the relevant files to the vector store
        This should be implemented in the subclass
        """
        pass
    
    @abstractmethod
    def _update_files_in_vector_store(self):
        """
        Update any files that may have changed locally in the vector store
        Not every agent will necessarily need to use this, so some subclasses may just leave this as an empty function
        """
        pass

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
        print(f"Deleted vector store {self.vector_store.id} for {self.store_name}")

    def _delay_for_retry(self, attempt_count: int) -> None:
        """Sleeps for a while based on the |attempt_count|."""
        # Exponentially increase from 5 to 80 seconds + some random to jitter.
        delay = 5 * 2**attempt_count + random.randint(1, 5)
        logging.warning('Retry in %d seconds...', delay)
        time.sleep(delay)

    def _is_retryable_error(self, err: Exception,
                            api_errors: list[Type[Exception]],
                            tb: traceback.StackSummary) -> bool:
        """Validates if |err| is worth retrying."""
        if any(isinstance(err, api_error) for api_error in api_errors):
            return True

        # A known case from vertex package, no content due to mismatch roles.
        if (isinstance(err, ValueError) and
            'Content roles do not match' in str(err) and tb[-1].filename.endswith(
                'vertexai/generative_models/_generative_models.py')):
            return True

        # A known case from vertex package, content blocked by safety filters.
        if (isinstance(err, ValueError) and
            'blocked by the safety filters' in str(err) and
            tb[-1].filename.endswith(
                'vertexai/generative_models/_generative_models.py')):
            return True

        return False

    def with_retry_on_error(self, func: Callable,
                            api_errs: list[Type[Exception]]) -> Any:
        """
        Retry when the function returns an expected error with exponential backoff.
        """
        for attempt in range(1, self._max_attempts + 1):
            try:
                return func()
            except Exception as err:
                logging.warning('LLM API Error when responding (attempt %d): %s',
                                attempt, err)
                tb = traceback.extract_tb(err.__traceback__)
                if (not self._is_retryable_error(err, api_errs, tb) or
                    attempt == self._max_attempts):
                    logging.warning(
                        'LLM API cannot fix error when responding (attempt %d) %s: %s',
                        attempt, err, traceback.format_exc())
                    raise err
                self._delay_for_retry(attempt_count=attempt)
        return None
