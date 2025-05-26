import subprocess
import sys
import os
import csv
from dotenv import load_dotenv
from llm import LLMProofWriter
load_dotenv()

"""
Runs the 18 test cases we have
"""


def test_parser():
    args = {
        "_on_rd_init_1": ["_on_rd_init", "_on_rd_init_precon_1"],
        "_on_rd_init_2": ["_on_rd_init", "_on_rd_init_precon_2"],
        "_on_rd_init_3": ["_on_rd_init", "_on_rd_init_precon_3"],
        "_on_rd_init_4": ["_on_rd_init", "_on_rd_init_precon_4"],
        "gcoap_dns_server_proxy_get_1": ["gcoap_dns_server_proxy_get", "gcoap_dns_server_proxy_get_precon_1"],
        "gcoap_dns_server_proxy_get_2": ["gcoap_dns_server_proxy_get", "gcoap_dns_server_proxy_get_precon_2"],
        "_gcoap_forward_proxy_copy_options_1": ["_gcoap_forward_proxy_copy_options", "_gcoap_forward_proxy_copy_options_precon_1"],
        "_gcoap_forward_proxy_copy_options_2": ["_gcoap_forward_proxy_copy_options", "_gcoap_forward_proxy_copy_options_precon_2"],
        "_iphc_ipv6_encode_1": ["_iphc_ipv6_encode", "_iphc_ipv6_encode_precon_1"],
        "_iphc_ipv6_encode_2": ["_iphc_ipv6_encode", "_iphc_ipv6_encode_precon_2"],
        "dns_msg_parse_reply_1": ["dns_msg_parse_reply", "dns_msg_parse_reply_precon_1"],
        "dns_msg_parse_reply_2": ["dns_msg_parse_reply", "dns_msg_parse_reply_precon_2"],
        "_rbuf_add_1": ["_rbuf_add2", "_rbuf_add_precon_1"],
        "_rbuf_add_2": ["_rbuf_add2", "_rbuf_add_precon_2"],
        "_rbuf_add_3": ["_rbuf_add2", "_rbuf_add_precon_3"],
        "_rbuf_add_4": ["_rbuf_add2", "_rbuf_add_precon_4"],
        "_rbuf_add_5": ["_rbuf_add2", "_rbuf_add_precon_5"],
        "_rbuf_add_6": ["_rbuf_add2", "_rbuf_add_precon_6"]
    }

    for case, args in args.items():
        print(f"\n===== Running {case} =====")
        result = subprocess.run([sys.executable, f"main.py"] + args, cwd=os.getcwd(), check=False)
        
        if result.returncode != 0:
            print(f"Error running {case}, return code: {result.returncode}")
            # Uncomment next line if you want to stop on first error
            # sys.exit(result.returncode)
        else:
            print(f"===== Completed {case} Successfully =====\n")

def test_workflow():

    preconditions = {
        '_on_rd_init_precon_1': "__CPROVER_assume(hdr != NULL);",
        '_on_rd_init_precon_2': "__CPROVER_assume(payload_offset <= pkt_size);",
        '_on_rd_init_precon_3': "__CPROVER_assume(pkt != NULL);",
        '_on_rd_init_precon_4': "__CPROVER_assume(_result_buf != NULL);",
        'gcoap_dns_server_proxy_get_precon_1': "__CPROVER_assume(str != NULL);",
        'gcoap_dns_server_proxy_get_precon_2': "__CPROVER_assume(src_null_byte < CONFIG_GCOAP_DNS_SERVER_URI_LEN);",
        '_gcoap_forward_proxy_copy_options_precon_1': "__CPROVER_assume(client_pkt->payload_len <= pkt->payload_len - 1);",
        '_gcoap_forward_proxy_copy_options_precon_2': "__CPROVER_assume(size <= pkt->payload_len);",
        '_iphc_ipv6_encode_precon_1': "__CPROVER_assume(len >= 41);",
        '_iphc_ipv6_encode_precon_2': "__CPROVER_assume(data != NULL);",
        'dns_msg_parse_reply_precon_1': "__CPROVER_assume(family == AF_UNSPEC || family == AF_INET || family == AF_INET6);",
        'dns_msg_parse_reply_precon_2': "__CPROVER_assume(len >= sizeof(dns_hdr_t));",
        '_rbuf_add_precon_1': "__CPROVER_assume(offset != 0 || sixlowpan_frag_1_is(pkt->data) || sixlowpan_sfr_rfrag_is(data));",
        '_rbuf_add_precon_2': "__CPROVER_assume(offset_diff >= 0);",
        '_rbuf_add_precon_3': "__CPROVER_assume(datagram_size <= entry_size);",
        '_rbuf_add_precon_4': "__CPROVER_assume(offset < 1000);",
        '_rbuf_add_precon_5': "__CPROVER_assume(size > MAX(sizeof(sixlowpan_frag_t), MAX(sizeof(sixlowpan_frag_n_t), sizeof(sixlowpan_sfr_rfrag_t))));",
        '_rbuf_add_precon_6': "__CPROVER_assume(entry.pkt->data != NULL);"
    }

    fields = [
        'Tag',
        'Removed Precondition',
        'Testing Round',
        'Total Tokens Used',
        'Success',
        'Attempt #1 Response',
        'A#1 Input Tokens',
        'A#1 Output Tokens',
        'Attempt #2 Response',
        'A#2 Input Tokens',
        'A#2 Cached Tokens',
        'A#2 Output Tokens',
        'Attempt #3 Response',
        'A#3 Input Tokens',
        'A#3 Cached Tokens',
        'A#3 Output Tokens',
    ]

    NUM_ROUNDS = 1
    openai_api_key = os.getenv("OPENAI_API_KEY", None)
    if not openai_api_key:
        raise EnvironmentError("No OpenAI API key found")

    results_file = os.path.join('./results', 'test_results.csv')
    with open(results_file, mode='w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()

        for tag, precon in preconditions.items():
            print(f"\n===== Running test for tag: {tag} =====")
            results = {
                'Tag': tag,
                'Removed Precondition': precon
            }
            for round_num in range(1, NUM_ROUNDS + 1):
                results["Testing Round"] = round_num
                try:
                    total_tokens = 0
                    proof_writer = LLMProofWriter(openai_api_key, tag, test_mode=True)
                    success, llm_responses = proof_writer.iterate_proof(max_attempts=3)
                    results['Success'] = success

                    for i, response in enumerate(llm_responses):
                        results[f'Attempt #{i + 1} Response'] = '\n '.join([f'{precon['function']} @ Line {precon['line']}: {precon['precondition']}' for precon in response['response']['preconditions']])
                        results[f'A#{i + 1} Input Tokens'] = response['usage'].input_tokens
                        results[f'A#{i + 1} Output Tokens'] = response['usage'].output_tokens
                        if i > 0:
                            results[f'A#{i + 1} Cached Tokens'] = response['usage'].input_tokens_details.cached_tokens
                        total_tokens += response['usage'].total_tokens
                    results['Total Tokens Used'] = total_tokens
                except:
                    results['Success'] = 'Error'
                writer.writerow(results)

if __name__ == '__main__':
    # Can either test the parser or the full workflow based on a command line arg
    if len(sys.argv) >= 2:
        mode = sys.argv[1].lower()
        if mode != 'parser' and mode != 'workflow':
            raise ValueError("Testing target must be either parser or workflow")
    else:
        # Test workflow by default
        mode = 'workflow'
    
    if mode == 'workflow':
        test_workflow()
    else:
        test_parser()