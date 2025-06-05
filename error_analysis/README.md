# AutoUP Automatic Proof Iteration

This repo contains the components for a workflow that use ChatGPT 4o iterate on a harness until it is able to resolve an error.

At the moment, the system can run one of 18 test cases that are named by their git tag:
- _on_rd_init_precon_1
- _on_rd_init_precon_2
- _on_rd_init_precon_3
- _on_rd_init_precon_4
- gcoap_dns_server_proxy_get_precon_1
- gcoap_dns_server_proxy_get_precon_2
- _gcoap_forward_proxy_copy_options_precon_1
- _gcoap_forward_proxy_copy_options_precon_2
- dns_msg_parse_reply_precon_1
- dns_msg_parse_reply_precon_2
- _rbuf_add_precon_1
- _rbuf_add_precon_2
- _rbuf_add_precon_3
- _rbuf_add_precon_4
- _rbuf_add_precon_5
- _rbuf_add_precon_6

Each tag corresponds to a harness that has a single precondition removed, which the LLM will attempt to re-create.

You MUST have your OpenAI API key stored as an environment variable called OPENAI_API_KEY in order to use this system.

To run through main:
`python3 main.py <tag name>`

To run the test suite:
`python3 run_test_suite.py [tags] [--short] [--parser_only]`
If no tags are specified, then tests for all tags are run.