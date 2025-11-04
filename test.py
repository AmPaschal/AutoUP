""" Running Tests"""

# System
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess
from datetime import datetime
import os

# Utils
import requests

PATH = "/home/rcalvome/Documents/AutoUp/framework/RIOT/cbmc/harness_gen_tests_2"
ROOT_DIR = "/home/rcalvome/Documents/AutoUp/framework/RIOT"
WEBHOOK_URL = "https://hooks.slack.com/triggers/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"

MAX_PROCESSES = 2


def run_sample(sample, timestamp: str):
    """Run single test sample"""
    folder_path = os.path.join(PATH, sample)
    target_file_path = os.path.join(folder_path, f"{sample}_harness.c")
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
    folders = [
        d for d in os.listdir(PATH)
        if os.path.isdir(os.path.join(PATH, d))
    ]
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
