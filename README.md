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
```

### Modes
| Mode | Description |
|------|--------------|
| `harness` | Generates harness and Makefile. |
| `function-stubs` | Generates function stubs. |
| `coverage` | Executes coverage debugger. |
| `debugger` | Runs proof debugger. |
| `all` | Runs `harness`, `function-stubs`, `coverage`, and `debugger` sequentially. |

---

## 📘 Example
```bash
python src/run.py
harness
--target_function_name my_function
--root_dir ./project 
--harness_path ./proof/harness
--target_file_path ./src/my_module.c   
```

---