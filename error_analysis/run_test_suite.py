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
from llm import LLMProofWriter
import shutil
from parser import extract_errors_and_payload
from generate_html_report import generate_html_report
load_dotenv()

"""
Runs the 18 test cases we have
"""

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
            httpd.server_close()    # Close the socket
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

# def test_parser():
#     args = {
#         "_on_rd_init_1": ["_on_rd_init", "_on_rd_init_precon_1"],
#         "_on_rd_init_2": ["_on_rd_init", "_on_rd_init_precon_2"],
#         "_on_rd_init_3": ["_on_rd_init", "_on_rd_init_precon_3"],
#         "_on_rd_init_4": ["_on_rd_init", "_on_rd_init_precon_4"],
#         "gcoap_dns_server_proxy_get_1": ["gcoap_dns_server_proxy_get", "gcoap_dns_server_proxy_get_precon_1"],
#         "gcoap_dns_server_proxy_get_2": ["gcoap_dns_server_proxy_get", "gcoap_dns_server_proxy_get_precon_2"],
#         "_gcoap_forward_proxy_copy_options_1": ["_gcoap_forward_proxy_copy_options", "_gcoap_forward_proxy_copy_options_precon_1"],
#         "_gcoap_forward_proxy_copy_options_2": ["_gcoap_forward_proxy_copy_options", "_gcoap_forward_proxy_copy_options_precon_2"],
#         "_iphc_ipv6_encode_1": ["_iphc_ipv6_encode", "_iphc_ipv6_encode_precon_1"],
#         "_iphc_ipv6_encode_2": ["_iphc_ipv6_encode", "_iphc_ipv6_encode_precon_2"],
#         "dns_msg_parse_reply_1": ["dns_msg_parse_reply", "dns_msg_parse_reply_precon_1"],
#         "dns_msg_parse_reply_2": ["dns_msg_parse_reply", "dns_msg_parse_reply_precon_2"],
#         "_rbuf_add_1": ["_rbuf_add2", "_rbuf_add_precon_1"],
#         "_rbuf_add_2": ["_rbuf_add2", "_rbuf_add_precon_2"],
#         "_rbuf_add_3": ["_rbuf_add2", "_rbuf_add_precon_3"],
#         "_rbuf_add_4": ["_rbuf_add2", "_rbuf_add_precon_4"],
#         "_rbuf_add_5": ["_rbuf_add2", "_rbuf_add_precon_5"],
#         "_rbuf_add_6": ["_rbuf_add2", "_rbuf_add_precon_6"]
#     }

#     for case, args in args.items():
#         print(f"\n===== Running {case} =====")

#         try:
#             extract_errors_and_payload(args[0], args[1])
        
#         except Exception as e:
#             print(f"Error running {case}: {e}")
#             # Uncomment next line if you want to stop on first error
#             # sys.exit(result.returncode)
#         else:
#             print(f"===== Completed {case} Successfully =====\n")

def _remove_preconditions_and_make_backup(settings, report):
    if not os.path.exists('./backups'):
        os.makedirs('./backups')

    # Make a backup copy of the original harness
    print("Backing up original harness...")
    backup_path = os.path.join('./backups', os.path.basename(settings['harness']))
    shutil.copy(settings['harness'], backup_path)

    with open(settings['harness'], 'r') as f:
        harness_lines = f.readlines()

    removed_precons = []
    offset = 0
    for line in settings['preconditions_lines_to_remove']:
        if line == 'TBD':
            continue
        line_index = line - offset - 1
        precon = harness_lines.pop(line_index)
        removed_precons.append(precon.strip())
        if '__CPROVER_assume' not in precon:
            print("WARNING: Removed non-precondition line from harness")
        offset += 1
    
    with open(settings['harness'], 'w') as f:
        f.writelines(harness_lines)

    print(f"Removed {len(settings['preconditions_lines_to_remove'])} preconditions from {os.path.basename(settings['harness'])}:\n{'\n'.join(removed_precons)}")
    report['Preconditions Removed'] = removed_precons
    return backup_path

def _restore_backup(backup_path, settings):
    # First save a copy of the final harness
    results_path = './results'
    if not os.path.exists(results_path):
        os.makedirs(results_path)

    shutil.copy(settings['harness'], os.path.join(results_path, os.path.basename(settings['harness'])))
    print("Saved a copy of the final harness to the results directory")

    if not os.path.exists(backup_path):
        raise FileNotFoundError("No backups found to restore from")

    shutil.copy(backup_path, settings['harness'])
    os.remove(backup_path)
    print("Backup harness restored successfully.")

def test_workflow(harnesses=[], testing_rounds=1):

    with open('./test_config.json', 'r') as f:
        config = json.load(f)

    openai_api_key = os.getenv("OPENAI_API_KEY", None)
    if not openai_api_key:
        raise EnvironmentError("No OpenAI API key found")

    test_report = {
        'Summary': {
            'Harnesses': {
                "Success": 0,
                "Failed": 0
            },
            'Errors': {
                'Attempt #1': 0,
                'Attempt #2': 0,
                'Attempt #3': 0,
                'Indirect': 0,
                'Failed': 0
            }

        },
        'Total Token Usage': {
            'Input': 0,
            'Output': 0,
            'Cached': 0
        },
        'Harnesses': []
    }

    
    for harness, settings in config.items():
        if len(harnesses) > 0 and harness.replace('_plus_func_models', '') not in harnesses:
            continue

        print(f"\n===== Running test for harness: {harness} =====")
        results = {
            'Harness': harness,
            'Preconditions Removed': [], # -> code block
            'Preconditions Added': [], # -> code block with A#1, A#2, etc
            'Success': False,
            '% Errors Resolved': -1,
            'Initial # of Errors': -1,
            'Initial Errors': [],
            'Summary': {
                'Attempt #1': 0,
                'Attempt #2': 0,
                'Attempt #3': 0,
                'Indirect': 0,
                'Failed': 0
            },
            'Total Token Usage': {
                'Input': 0,
                'Output': 0,
                'Cached': 0
            },
            'Execution Time': -1,
            'Processed Errors': [
                # Error: str
                # Attempts: int
                # Resolved?: bool
                # Added precondition(s): str -> code block
                # Token usage: int
                # Also resolved (other errors): str
                # Responses: list[str] -> code block
            ]
        }


        backup_path = _remove_preconditions_and_make_backup(settings, results)

        try:
            proof_writer = LLMProofWriter(openai_api_key, settings['harness'], test_mode=True)
            start = time.time()
            harness_report = proof_writer.iterate_proof(max_attempts=3)
            results['Execution Time'] = time.time() - start
            results['Initial # of Errors'] = harness_report['initial_errors'].pop('total')
            results['Initial Errors'] = harness_report['initial_errors']
            results['Unresolved Errors'] = harness_report['manual_review']
            results['Preconditions Added'] = harness_report['preconditions_added']
            results['Success'] = len(harness_report['manual_review']) == 0
            for error in harness_report['processed_errors']:
                error_report = {
                    "Error": proof_writer._err_to_str(error),
                    "Attempts": error['attempts'] if error['attempts'] != -1 else 3,
                    "Resolved": error['attempts'] != -1,
                    "Preconditions Added": error['added_precons'],
                    "Indirectly Resolved": error['indirectly_resolved'],
                    "Token Usage": error['tokens'],
                    'Raw Responses': error['responses']
                }

                # Update the summary metrics for the harness
                results['Total Token Usage']['Input'] += error['tokens']['input']
                results['Total Token Usage']['Output'] += error['tokens']['output']
                results['Total Token Usage']['Cached'] += error['tokens']['cached']
                if error['attempts'] == -1:
                    results['Summary']['Failed'] += 1
                else:
                    results['Summary']['Indirect'] += len(error['indirectly_resolved'])
                    results['Summary'][f'Attempt #{error['attempts']}'] += 1
                results['Processed Errors'].append(error_report)
            results['Success Rate'] = round((results['Initial # of Errors'] - results['Summary']['Failed']) / results['Initial # of Errors'] * 100, 2)

            # Then update the "global" result
            test_report['Harnesses'].append(results)
            if results['Success']:
                test_report['Summary']['Harnesses']['Success'] += 1
            else:
                test_report['Summary']['Harnesses']['Failed'] += 1

            for key, count in results['Summary'].items():
                test_report['Summary']['Errors'][key] += count
            
            for key, count, in results['Total Token Usage'].items():
                test_report['Total Token Usage'][key] += count
            


        except Exception as e:
            print(f"Error during while processing {harness}: {e}")
            proof_writer._cleanup_vector_store()
            test_report['Summary']['Harnesses']['Failed'] += 1
            test_report['Harnesses'].append({
                'Harness': harness,
                'Status': 'Error',
                'Error': f"Harness execution failed: {str(e)}",
                'Traceback': traceback.format_exc()
            })
        finally:
            _restore_backup(backup_path, settings)
    
    if not os.path.exists('./results'):
        os.makedirs('./results')

    with open('./results/test_report.json', 'w') as f:
        json.dump(test_report, f, indent=4)
    
    generate_html_report(test_report)
    return test_report

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Test runner configuration")
    parser.add_argument(
        "--harnesses",
        nargs="*",
        default=[],
        help="List of harnesses to use in the test run. Invalid harnesses will be skipped. If no harnesses are provided, all test harnesses will be run.",
    )
    parser.add_argument(
        "--parser_only",
        action="store_true",
        help="Only run the parser component of the test suite"
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=1,
        help="Number of times to replicate each test case. Default is 1.",
    )
    parser.add_argument(
        "--render_results",
        type=bool,
        default=True,
        help="Automatically launches an HTTP server to render the test results. Default: True"
    )
    parser.add_argument(
        "--results_port",
        type=int,
        default=8000,
        help="Port for the HTTP server rendering test results. If no value is provided user is prompted for input before server launches"
    )

    args = parser.parse_args()

    # if args.parser_only:
    #     test_parser()
    # else:
    results = test_workflow(harnesses=args.harnesses, testing_rounds=args.rounds)
    if results is not None and args.render_results:
        launch_results_server(results, port=args.results_port)
