import os
import json
import textwrap
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key)


def clean_harness_code(raw_code):
    cleaned = raw_code
    if "```" in cleaned:
        cleaned = cleaned.split("```", 1)[-1]
    if "#include" in cleaned:
        cleaned = cleaned[cleaned.index("#include"):]
    if "```" in cleaned:
        cleaned = cleaned.split("```")[0]
    return textwrap.dedent(cleaned).strip()


def extract_function_code(file_path, function_name):
    if not os.path.exists(file_path):
        print(f"[ERROR] File not found: {file_path}")
        return None

    with open(file_path, 'r', encoding="utf-8", errors="ignore") as file:
        lines = file.readlines()

    start_index = None
    brace_count = 0
    inside_function = False
    waiting_for_brace = False
    function_lines = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Detect function start - could be single or multi-line before opening brace
        if not inside_function and function_name in stripped and "(" in stripped:
            # Append first line of function signature
            function_lines.append(line)

            # Check if opening brace is here
            if "{" in stripped:
                inside_function = True
                brace_count += stripped.count("{") - stripped.count("}")
            else:
                waiting_for_brace = True
            continue

        # If we're still collecting function signature until we find "{"
        if waiting_for_brace:
            function_lines.append(line)
            if "{" in stripped:
                inside_function = True
                waiting_for_brace = False
                brace_count += stripped.count("{") - stripped.count("}")
            continue

        # If inside the function body, collect lines and track braces
        if inside_function:
            function_lines.append(line)
            brace_count += stripped.count("{") - stripped.count("}")
            if brace_count == 0:
                break

    if function_lines and inside_function:
        return "".join(function_lines)
    else:
        print(f"[ERROR] Function '{function_name}' not found in {file_path}")
        return None


with open("functions.json", "r") as f:
    functions_data = json.load(f)

for item in functions_data:
    file_path = item['file_path']
    function_name = item['function_name']

    function_code = extract_function_code(file_path, function_name)

    if not function_code:
        continue

    prompt = f"""
    You are a C programming assistant. Your task is to generate a CBMC harness for the following C function:

    {function_code}

    Follow these steps strictly:
    1. Define a harness called `void proof_harness()`.
    2. Declare all function parameters inside `proof_harness` exactly as they appear in the function signature.
    3. For any pointer to a struct (e.g., `struct my_struct *ptr`), allocate memory with `malloc(sizeof(struct my_struct))` and use `__CPROVER_assume(ptr != NULL);`.
    4. For pointers to primitive types (e.g., `uint8_t *data`), do the following:
        - Create a size variable (e.g., `uint16_t len`) to hold the allocation size.
        - Allocate memory using `malloc(sizeof(type) * len)` (e.g., `malloc(sizeof(uint8_t) * len)`).
        - Use `__CPROVER_assume(len == related_size_param)` if the pointer and size parameter are related (e.g., through `memcpy`, array access, etc.).
    5. For any pointer used in the function without a NULL check, add a precondition using `__CPROVER_assume(ptr != NULL);`. 
    6. Finally, call the function in the harness using the declared and initialized arguments.

    Only return valid C code. No explanation. No markdown formatting.
    """

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a helpful AI that writes C test harnesses for formal verification"},
            {"role": "user", "content": prompt}
        ]
    )

    harness_code = clean_harness_code(response.choices[0].message.content)

    harness_file_name = f"harness_{function_name}.c"
    with open(harness_file_name, "w") as f:
        f.write(harness_code)

    print(f"Harness for '{function_name}' saved to {harness_file_name}")


# import os
# import regex as re
# import json
# import textwrap
# from openai import OpenAI
# from dotenv import load_dotenv

# load_dotenv()
# api_key = os.getenv("OPENAI_API_KEY")
# client = OpenAI(api_key=api_key)

# def clean_harness_code(raw_code):
#     cleaned = raw_code
#     if "```" in cleaned:
#         cleaned = cleaned.split("```", 1)[-1]
#     if "#include" in cleaned:
#         cleaned = cleaned[cleaned.index("#include"):]
#     if "```" in cleaned:
#         cleaned = cleaned.split("```")[0]
#     return textwrap.dedent(cleaned).strip()

# def extract_function_code(file_path, function_name):
#     if not os.path.exists(file_path):
#         print(f"[ERROR] File not found: {file_path}")
#         return None

#     with open(file_path, 'r', encoding="utf-8", errors="ignore") as file:
#         content = file.read()

#     # This pattern allows for multiline params and captures nested braces
#     pattern = rf"""
#     (^                                   # Start of the line (function definition)
#     (?:[\w\s\*\(\)]+?\s+)?               # Optional qualifiers and return type
#     \b{re.escape(function_name)}\s*      # Exact function name
#     \((?:[^()]*|\([^()]*\))*\)           # Match parameter list, supports nested parentheses
#     \s*\{{                               # Opening brace of the function body
#     (?:[^{{}}]*|\{{(?:[^{{}}]*|\{{[^{{}}]*\}})*\}})*  # Function body with nested braces
#     \}})                                 # Closing brace of function
#     """

#     match = re.search(pattern, content, re.MULTILINE | re.DOTALL | re.VERBOSE)

#     if match:
#         return match.group(1)
#     else:
#         print(f"[ERROR] Function '{function_name}' not found in {file_path}")
#         return None

# with open("functions.json", "r") as f:
#     functions_data = json.load(f)

# for item in functions_data:
#     file_path = item['file_path']
#     function_name = item['function_name']

#     function_code = extract_function_code(file_path, function_name)

#     if not function_code:
#         continue

#     prompt = f"""
#     You are a C programming assistant. Your task is to generate a CBMC harness for the following C function:

#     {function_code}

#     Follow these steps strictly:
#     1. Define a harness called `void proof_harness()`.
#     2. Declare all function parameters inside `proof_harness` exactly as they appear in the function signature.
#     3. For any pointer to a struct (e.g., `struct my_struct *ptr`), allocate memory with `malloc(sizeof(struct my_struct))` and use `__CPROVER_assume(ptr != NULL);`.
#     4. For pointers to primitive types (e.g., `uint8_t *data`), do the following:
#         - Create a size variable (e.g., `uint16_t len`) to hold the allocation size.
#         - Allocate memory using `malloc(sizeof(type) * len)` (e.g., `malloc(sizeof(uint8_t) * len)`).
#         - Use `__CPROVER_assume(len == related_size_param)` if the pointer and size parameter are related (e.g., through `memcpy`, array access, etc.).
#     5. For any pointer used in the function without a NULL check, add a precondition using `__CPROVER_assume(ptr != NULL);`.
#     6. Finally, call the function in the harness using the declared and initialized arguments.

#     Only return valid C code. No explanation. No markdown formatting.
#     """

#     response = client.chat.completions.create(
#         model="gpt-4",
#         messages=[
#             {"role": "system", "content": "You are a helpful AI that writes C test harnesses for formal verification"},
#             {"role": "user", "content": prompt}
#         ]
#     )

#     harness_code = clean_harness_code(response.choices[0].message.content)

#     harness_file_name = f"harness_{function_name}.c"
#     with open(harness_file_name, "w") as f:
#         f.write(harness_code)
# print(f"Harness for '{function_name}' saved to {harness_file_name}")
