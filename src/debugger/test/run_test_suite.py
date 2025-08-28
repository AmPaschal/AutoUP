import http.server
import socketserver
import webbrowser
import os
import json
import argparse
import time
import signal
import traceback
import sys
from dotenv import load_dotenv
from src.debugger.debugger import LLMProofDebugger
import shutil
from src.debugger.test.generate_html_report import generate_html_report

load_dotenv()


def launch_results_server(results, port):
    if port == -1:
        port = int(input("Input a valid port for test results server: "))

    # Change the working directory to the one containing your HTML file
    os.chdir("./results")

    # Start an HTTP server in that directory
    Handler = http.server.SimpleHTTPRequestHandler

    class ReusableTCPServer(socketserver.TCPServer):
        allow_reuse_address = True  # allow immediate reuse after exit

    with ReusableTCPServer(("", port), Handler) as httpd:
        print(f"Serving at http://localhost:{port}")
        # Open the default web browser to the file
        webbrowser.open(f"http://localhost:{port}")

        def shutdown_server(signum, frame):
            print("\nShutting down server...")
            httpd.shutdown()
            httpd.server_close()  # Close the socket
            sys.exit(0)

        # Graceful shutdown on Ctrl+C or termination
        signal.signal(signal.SIGINT, shutdown_server)
        signal.signal(signal.SIGTERM, shutdown_server)

        try:
            while True:
                httpd.handle_request()  # handles one request, or times out
        except KeyboardInterrupt:
            shutdown_server(signal.SIGINT, None)
        finally:
            httpd.server_close()
            print("Server closed.")


def _remove_preconditions_and_make_backup(settings, report):
    """
    Removes the specified lines from the harness and creates a backup that can be restored after testing
    """

    if not os.path.exists("./backups"):
        os.makedirs("./backups")

    # Make a backup copy of the original harness
    print("Backing up original harness...")
    backup_path = os.path.join("./backups", os.path.basename(settings["harness"]))
    shutil.copy(settings["harness"], backup_path)

    with open(settings["harness"], "r") as f:
        harness_lines = f.readlines()

    removed_precons = []
    offset = 0
    for line in settings["preconditions_lines_to_remove"]:
        if line == "TBD":
            continue
        line_index = line - offset - 1
        precon = harness_lines.pop(line_index)
        removed_precons.append(precon.strip())
        if "__CPROVER_assume" not in precon:
            print("WARNING: Removed non-precondition line from harness")
        offset += 1

    with open(settings["harness"], "w") as f:
        f.writelines(harness_lines)

    print(
        f"Removed {len(settings['preconditions_lines_to_remove'])} preconditions from {os.path.basename(settings['harness'])}:\n{'\n'.join(removed_precons)}"
    )
    report["Preconditions Removed"] = removed_precons
    return backup_path


def _restore_backup(backup_path, settings):
    # First save a copy of the final LLM-modified harness
    results_path = "./results"
    if not os.path.exists(results_path):
        os.makedirs(results_path)

    shutil.copy(settings["harness"], os.path.join(results_path, os.path.basename(settings["harness"])))
    print("Saved a copy of the final harness to the results directory")

    if not os.path.exists(backup_path):
        raise FileNotFoundError("No backups found to restore from")

    # Then restore the original
    shutil.copy(backup_path, settings["harness"])
    os.remove(backup_path)
    print("Backup harness restored successfully.")


def test_workflow(harnesses=[], repo="RIOT", testing_rounds=1):
    with open(f"./configs/{repo}_test_config.json", "r") as f:
        config = json.load(f)

    openai_api_key = os.getenv("OPENAI_API_KEY", None)
    if not openai_api_key:
        raise EnvironmentError("No OpenAI API key found")

    test_report = {
        "Summary": {
            "Harnesses": {"Success": 0, "Failed": 0},
            "Errors": {
                "Attempt #1": 0,
                "Attempt #2": 0,
                "Attempt #3": 0,
                "Indirectly Resolved": 0,
                "Unresolved": 0,
            },
        },
        "Total Token Usage": {"Input": 0, "Output": 0, "Cached": 0},
        "Harnesses": [],
    }

    for harness, settings in config.items():
        if len(harnesses) > 0 and harness not in harnesses:
            continue

        print(f"\n===== Running test for harness: {harness} =====")
        results = {
            "Harness": harness,
            "Preconditions Removed": [],  # -> code block
            "Preconditions Added": [],  # -> code block with A#1, A#2, etc
            "Success": False,
            "% Errors Resolved": -1,
            "Initial # of Errors": -1,
            "Initial Errors": [],
            "Summary": {
                "Attempt #1": 0,
                "Attempt #2": 0,
                "Attempt #3": 0,
                "Indirectly Resolved": 0,
                "Unresolved": 0,
            },
            "Total Token Usage": {"Input": 0, "Output": 0, "Cached": 0},
            "Execution Time": -1,
            "Successful Errors": [],
            "Failed Errors": [],
        }

        backup_path = _remove_preconditions_and_make_backup(settings, results)

        try:
            abs_harness_path = os.path.abspath(settings["harness"])
            proof_writer = LLMProofDebugger(openai_api_key, abs_harness_path, test_mode=True)
            start = time.time()
            harness_report = proof_writer.iterate_proof(max_attempts=3)
            results["Execution Time"] = time.time() - start
            results["Initial # of Errors"] = harness_report["initial_errors"].pop("total")
            results["Initial Errors"] = harness_report["initial_errors"]
            results["Preconditions Added"] = harness_report["preconditions_added"]

            for error in list(harness_report["processed_errors"]["success"].values()) + list(harness_report["processed_errors"]["failure"].values()):
                error_report = {
                    "Error": error["msg"],
                    "Attempts": error["attempts"] if error["attempts"] != -1 else 3,
                    "Resolved": error["attempts"] != -1 or error.get("resolved_by", None) is not None,
                    "Preconditions Added": error["added_precons"],
                    "Indirectly Resolved": error["indirectly_resolved"],
                    "Token Usage": error["tokens"],
                    "Raw Responses": error["responses"],
                }

                # Update the summary metrics for the harness
                results["Total Token Usage"]["Input"] += error["tokens"]["input"]
                results["Total Token Usage"]["Output"] += error["tokens"]["output"]
                results["Total Token Usage"]["Cached"] += error["tokens"]["cached"]
                if error["attempts"] == -1:
                    results["Failed Errors"].append(error_report)
                    if error.get("resolved_by", None) is not None:
                        error_report["Resolved By"] = error["resolved_by"]
                    else:
                        results["Summary"]["Unresolved"] += 1

                else:
                    results["Summary"]["Indirectly Resolved"] += len(error["indirectly_resolved"])
                    results["Summary"][f"Attempt #{error['attempts']}"] += 1
                    results["Successful Errors"].append(error_report)

            results["Success"] = results["Summary"]["Unresolved"] == 0
            results["% Of Errors Resolved"] = round(
                (results["Initial # of Errors"] - results["Summary"]["Unresolved"]) / results["Initial # of Errors"] * 100, 2
            )

            # Then update the "global" result
            test_report["Harnesses"].append(results)
            if results["Success"]:
                test_report["Summary"]["Harnesses"]["Success"] += 1
            else:
                test_report["Summary"]["Harnesses"]["Failed"] += 1

            for key, count in results["Summary"].items():
                test_report["Summary"]["Errors"][key] += count

            for (
                key,
                count,
            ) in results["Total Token Usage"].items():
                test_report["Total Token Usage"][key] += count

        except Exception as e:
            print(f"Error during while processing {harness}: {e}")
            proof_writer._cleanup_vector_store()
            test_report["Summary"]["Harnesses"]["Failed"] += 1
            test_report["Harnesses"].append(
                {"Harness": harness, "Status": "Error", "Error": f"Harness execution failed: {str(e)}", "Traceback": traceback.format_exc()}
            )
        finally:
            _restore_backup(backup_path, settings)

    if not os.path.exists("./results"):
        os.makedirs("./results")

    with open("./results/test_report.json", "w") as f:
        json.dump(test_report, f, indent=4)

    generate_html_report(test_report)
    return test_report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test runner configuration")
    parser.add_argument("--repo", choices=["contiki", "RIOT"], required=True, help="Specify which harness repo to test (contiki or RIOT).")
    parser.add_argument(
        "--harnesses",
        nargs="*",
        default=[],
        help="List of harnesses to use in the test run. Invalid harnesses will be skipped. If no harnesses are provided, all test harnesses will be run.",
    )
    parser.add_argument("--parser_only", action="store_true", help="Only run the parser component of the test suite")
    parser.add_argument(
        "--rounds",
        type=int,
        default=1,
        help="Number of times to replicate each test case. Default is 1.",
    )
    parser.add_argument(
        "--render_results", type=bool, default=True, help="Automatically launches an HTTP server to render the test results. Default: True"
    )
    parser.add_argument(
        "--results_port",
        type=int,
        default=8000,
        help="Port for the HTTP server rendering test results. If no value is provided user is prompted for input before server launches",
    )

    args = parser.parse_args()

    # if args.parser_only:
    #     test_parser()
    # else:
    results = test_workflow(harnesses=args.harnesses, repo=args.repo, testing_rounds=args.rounds)
    if results is not None and args.render_results:
        launch_results_server(results, port=args.results_port)
