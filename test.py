""" Running Tests"""

# System
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess
from datetime import datetime
import os

# Utils
import requests

# Constants
PATH = "/home/rcalvome/Documents/AutoUp/framework/RIOT/cbmc/harness_gen_tests_2"
ROOT_DIR = "/home/rcalvome/Documents/AutoUp/framework/RIOT"
WEBHOOK_URL = "https://hooks.slack.com/triggers/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"

MAX_PROCESSES = 4


def build_cscope_database():
    """Build the cscope database"""
    with subprocess.Popen(
        ["cscope", "-Rbqk"],
        cwd=ROOT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as proc:
        try:
            print("Database created")
        except subprocess.TimeoutExpired:
            proc.kill()
            print("Timeout expired creating database")

def get_target_file_by_cscope(sample: str) -> str:
    """Get the path to the file where the function is implemented"""
    with subprocess.Popen(
        ["cscope", "-dL", "-1", sample],
        cwd=ROOT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as proc:
        try:
            stdout, _stderr = proc.communicate()
            path = ""
            for line in stdout.splitlines():
                path = line.split()[0]
                if not path.startswith("cbmc"):
                    break
            return path
        except subprocess.TimeoutExpired:
            proc.kill()
            print("Timeout expired query function implementation")
            return ""


def run_sample(sample, timestamp: str):
    """Run single test sample"""
    folder_path = os.path.join(PATH, sample)
    target_file_path = get_target_file_by_cscope(sample)
    os.makedirs(f"logs/{timestamp}", exist_ok=True)
    os.makedirs(f"metrics/{timestamp}", exist_ok=True)
    cmd = [
        "python", "src/run.py", "debugger",
        f"--root_dir={ROOT_DIR}",
        f"--target_function_name={sample}",
        f"--harness_path={folder_path}",
        f"--target_file_path={target_file_path}",
        f"--log_file=logs/{timestamp}/{sample}.log",
        f"--metrics_file=metrics/{timestamp}/{sample}.jsonl",
    ]
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as proc:
        try:
            stdout, stderr = proc.communicate()
            if proc.returncode != 0:
                print(f"Error in {sample}:\n{stderr}")
            return stdout
        except subprocess.TimeoutExpired:
            proc.kill()
            return f"Timeout expired for {sample}"


def main():
    """Entry point"""
    build_cscope_database()
    folders = [
        d for d in os.listdir(PATH)
        if os.path.isdir(os.path.join(PATH, d))
    ]
    folders = folders[1:2]
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    with ThreadPoolExecutor(max_workers=MAX_PROCESSES) as executor:
        futures = {
            executor.submit(
                run_sample,
                sample,
                timestamp,
            ): sample for sample in folders
        }
        for future in as_completed(futures):
            result = future.result()
            print(result)
    requests.post(
        WEBHOOK_URL,
        json={
            "message": "Execution of debugger finished!",
        },
        timeout=10,
    )


if __name__ == "__main__":
    main()
