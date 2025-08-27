import sys
import os
from src.makefile.gen_makefile import LLMMakefileGenerator
from dotenv import load_dotenv
from pathlib import Path
load_dotenv()

if __name__ == "__main__":
    
    harness_name = sys.argv[1] # _on_rd_init
    harness_path = Path(sys.argv[2]) # ../RIOT/cbmc/proofs/_on_rd_init/Makefile
    target_func_path = Path(sys.argv[3]) # ../RIOT/sys/net/application_layer/cord/lc/cord_lc.c
    openai_api_key = os.getenv("OPENAI_API_KEY", None)
    if not openai_api_key:
        raise EnvironmentError("No OpenAI API key found")
    
    cwd = Path.cwd()

    generator = LLMMakefileGenerator(target_func=harness_name, harness_path=(cwd / harness_path).resolve(), target_file_path=(cwd / target_func_path).resolve(), openai_api_key=openai_api_key, test_mode=True)
    generator.generate_makefile()