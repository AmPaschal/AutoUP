# AutoUP - Automatic Unit Proof Writer

Automatic Unit Proof Writer

---

## 🚀 Installation

### Prerequisites
- **Docker** ≥ 28.0  
- **Python** ≥ 3.10 (3.12 recommended)  
- **OpenAI API Key** set as an environment variable:
  ```bash
  export OPENAI_API_KEY="your_api_key_here"
  ```

### Setup
```bash
# Clone repository
git clone https://github.com/username/AutoUP.git
cd AutoUP

# Install dependencies
pip install -r requirements.txt
```

---

## 🧠 Usage

To display all available options:
```bash
python src/run.py --help
```

### Command Syntax
```bash
python src/run.py
{harness,function-stubs,coverage,debugger,all}
--target_function_name <function_name>
--root_dir <project_root>
--harness_path <harness_dir>
--target_file_path <target_source>
--log_file <log_file_name>
--metrics_file <metrics_file_name>
[--scope_bound <depth_cap>]
[--scope_time_budget_minutes <minutes>]
```

### Modes
| Mode | Description |
|------|--------------|
| `harness` | Generates harness and Makefile. |
| `function-stubs` | Generates stubs for undefined functions that return function pointers. |
| `coverage` | Executes coverage debugger to fix coverage gaps. |
| `debugger` | Executes proof erro debugger to generate preconditions fixing CBMC errors. |
| `all` | Runs `harness`, `function-stubs`, `coverage`, and `debugger` sequentially. |

---

## 📘 Example
```bash
python src/run.py
<mode>
--target_function_name <function-to-generate-harness-for>
--root_dir </path/to/to/project/containing/target/function> 
--harness_path </path/to/the/harness/directory>
--target_file_path </path/to/the/source/file/containing/target/function>  
```

For example, here is the command to generate an initial proof harness and makefile for the _receive function in RIOT project.

```bash
python3 src/run.py
harness
--target_function_name _receive
--root_dir /home/pamusuo/research/cbmc-research/RIOT
--harness_path /home/pamusuo/research/cbmc-research/RIOT/cbmc/harness_gen_tests_3/_receive
--target_file_path  /home/pamusuo/research/cbmc-research/RIOT/sys/net/gnrc/transport_layer/tcp/gnrc_tcp_eventloop.c
--log_file logfile.txt
--metrics_file metrics.jsonl > output-coverage-receive.txt 2>&1
```

Optional scope widening controls:

- `--scope_bound <k>` sets the maximum widening depth.
- `--scope_time_budget_minutes <minutes>` widens incrementally while full verification wall-clock time stays within the budget.
- If both are set, widening stops at the earlier of the time budget or depth cap.
- If only the time budget is set, widening continues until the budget is exceeded or no more source files can be added.

To automatically fix coverage gaps or generate preconditions fixing errors, replace `harness` with `coverage` or `debugger` respectively.
To run all the agents sequentially, use the `all` mode.

---
