import subprocess
import sys
import os

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