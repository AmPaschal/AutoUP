# Harness Generator with LLM

## Overview
This project provides a **Python-based tool** that leverages a Large Language Model (LLM) to automatically generate `harness.c` files for software verification tasks.  
Given a function name and its file path, the script connects to the LLM, analyzes the function’s structure, and produces a corresponding `harness.c` file to simplify verification and testing.

---

## Features
- Connects to an LLM via the `OPENAI_API_KEY`
- Takes a function name and source file path as input
- Automatically generates `harness.c` files
- Organizes harnesses in function-specific directories
- Supports reproducible experiments for verification workflows

---

## 🛠️ Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/<your-username>/<your-repo>.git
   cd <your-repo>

2. **Switch to the chioma-prototype branch**
    ```bash
    git checkout Chioma-prototype

3. **Navigate to the project folder**
    ```bash
    cd harness_generator

3. **Configure your environment**
    copy .env.example to .env
    OPENAI_API_KEY=your_api_key_here

4. **Run the Script**
    python extract_function.py

## Further Questions
    Stuck on any step, you can reach out via email: muofunanyajennifer1@gmail.com or slack: @Muofunanya Chioma