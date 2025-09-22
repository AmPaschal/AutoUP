# AutoUP Automatic Proof Writer

This repo contains the components for a workflow that uses ChatGPT 4.1 iterate to automatically generate sound and complete unit proofs

You MUST have your OpenAI API key stored as an environment variable (either through the command line or from a .env file) called OPENAI_API_KEY in order to use this system.

These tests all assume you have pulled our version of the RIOT and contiki repos to the same directory as `AutoUP/`, and that you have checked out the `AutoUP-multi-precon-test` branch for RIOT and the `AutoUP-testing` branch for contiki.

# Repo Use

There are two main ways currently to interact with the repo:

## run.py

**Note:** This option is not recommended, as it is very barebones compared to run_test_suite.py and is only implemented for the sake of completeness.
It does not currently have a good way to display the results of a run, so it is not well suited to testing, but is the only way to freely target harnesses using command line args.

When using `run.py`, you must currently specify which module you want to run (either makefile or debugger).

```bash
python src/run.py <mode> [arguments]
````

### Makefile mode

Generates a makefile for the harness at the specified path targetting the specified function. Currently at a very early MVP stage, and assumes a harness file already exists inside harness_path.

```bash
python src/run.py makefile <function_name> <harness_path> <target_func_path>
```

Example:
```bash
python src/run.py makefile _on_rd_init ./RIOT/cbmc/proofs/_on_rd_init/Makefile ./RIOT/sys/net/application_layer/cord/lc/cord_lc.c
```

### Debugger Mode

Runs the existing harness at the specified path, and iteratively resolves all modelling errors.

```bash
python src/run.py debugger <harness_path>
```

Example:
```bash
python script.py debugger ../RIOT/cbmc/proofs/_on_rd_init/_on_rd_init_harness.c
```


## run_test_suite.py

Each module has a `<module>/tests/run_test_suite.py`, which is the massively preferred method for running each module. It enables test mode in each agent, which should save backups of any modified files and restores them at the end of tests.

`run_test_suite.py` for makefile mode is currently very barebones (basically the same as `run.py`), but `run_test_suite.py` for debugger mode is a robust testing suite that debugs a suite of harnesses and compiles the results into a detailed HTML report. 

For **debugger** mode, use `run_test_suite.py --help` to see the command line args needed. The tests are specified in the `configs/` subdir, with seperate config files for each repo. Each file contains a list of harnesses to test and specifies the lines of the harness to be removed before testing (which should all be lines with CPROVER_ASSUME statements). Feel free to add new harnesses to these files.

For **makefile** mode, `run_test_suite.py` is currently essentially a copy of `run.py` that will be implemented properly later.

# Modules

The general structure of this repo can be broken up into several "modules", each responsible for one section of the proof writing process. Currently, only two modules are implemented.

## Makefile

Status: MVP

Generates a Makefile for a harness at the input path by iteratively running Make and implementing LLM-suggested updates until the command returns successfully. Currently has a very minimal implementation, basically just the bare minimum functionality of adding values to a set of Makefile fields. There is currently a hardcoded 10 attempt limit for the LLM to be able to create a successful makefile.

## Debugger

Status: Mostly complete

Runs make at the specified path, and uses the output results from CBMC and CBMC viewer to iteratively parse and resolve all modelling errors using GPT 4.1. The LLM is given an error from the error report, along with a variable trace and all relevant harness/function definitions, and then is asked to return a set of preconditions that would resolve the error. These preconditions are added and the harness is re-run, with the error being resolved successfully if it no longer appears in the error report. For each error, the LLM is given a default of 3 attempts to resolve the error before giving up and moving to the next error. If harness execution or update fails due to some kind of error, it only counts as a 'half attempt' (0.5) to give the LLM more times to get feedback on its suggested precondition.


# Further Questions

If you have any further questions, ping me on slack @Taylor Le Lievre or shoot me an email tlelievr@purdue.edu