from openai import OpenAI
from abc import ABC, abstractmethod

class OpenAIAgent(ABC):
    """
    Shared features for any OpenAI agent that interacts with a vector store
    """

    def __init__(self, openai_api_key, agent_name, harness_name, harness_path, test_mode=False, chunking_strategy=None):
        self.client = OpenAI(api_key=openai_api_key)
        self.harness_name = harness_name
        self.harness_path = harness_path
        self.agent_name = agent_name
        self.store_name = f'{self.harness_name}-{agent_name}'
        self.test_mode = test_mode
        self.vector_store = self._create_vector_store(chunking_strategy)


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
    def _update_harness_in_vector_store(self):
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
