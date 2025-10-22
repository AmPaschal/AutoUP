""" Running Tests"""

# System
import subprocess
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

# Utils
import requests

PATH = "/home/rcalvome/Documents/AutoUp/framework/RIOT/cbmc/harness_gen_tests_2"
ROOT_DIR = "/home/rcalvome/Documents/AutoUp/framework/RIOT"
WEBHOOK_URL = "https://hooks.slack.com/triggers/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"

MAX_PROCESSES = 8


def run_sample(sample):
    """Run single test sample"""
    folder_path = os.path.join(PATH, sample)
    cmd = [
        "python", "src/run.py", "debugger",
        f"--root_dir={ROOT_DIR}",
        f"--target_function_name={sample}",
        f"--harness_path={folder_path}"
    ]
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=600)
            if proc.returncode != 0:
                print(f"Error in {sample}:\n{stderr}")
            return stdout
        except subprocess.TimeoutExpired:
            proc.kill()
            return f"Timeout expired for {sample}"


def main():
    """Entry point"""
    folders = [d for d in os.listdir(PATH) if os.path.isdir(os.path.join(PATH, d))]
    # folders = [
    #     "/home/rcalvome/Documents/AutoUp/framework/RIOT/cbmc/harness_gen_tests_2/gnrc_rpl_recv_DAO",
    #     # "/home/rcalvome/Documents/AutoUp/framework/RIOT/cbmc/harness_gen_tests_2/gnrc_sixlowpan_iphc_recv",
    #     # "/home/rcalvome/Documents/AutoUp/framework/RIOT/cbmc/harness_gen_tests_2/_iphc_ipv6_encode",
    #     # "/home/rcalvome/Documents/AutoUp/framework/RIOT/cbmc/harness_gen_tests_2/_iphc_encode",
    #     # "/home/rcalvome/Documents/AutoUp/framework/RIOT/cbmc/harness_gen_tests_2/_parse_options",
    #     # "/home/rcalvome/Documents/AutoUp/framework/RIOT/cbmc/harness_gen_tests_2/clif_decode_link",
    # ]

    with ThreadPoolExecutor(max_workers=MAX_PROCESSES) as executor:
        futures = {executor.submit(run_sample, sample) : sample for sample in folders}
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
