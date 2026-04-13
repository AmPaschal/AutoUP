import os
import re


def get_makefile_list_var(makefile_content, var_name):
    """Extract a list of values from a multi-line makefile variable."""
    lines = makefile_content.splitlines()
    values = []
    inside_var = False

    for line in lines:
        stripped = line.strip()

        if not inside_var:
            if re.match(rf"^{var_name}\s*[\?\+]?=", stripped):
                inside_var = True
                part = re.split(r"[\?\+]?=", stripped, 1)[1].strip()
                if part:
                    if part.endswith("\\"):
                        part = part[:-1].strip()
                    values.extend(part.split())
                    if not stripped.endswith("\\"):
                        inside_var = False
            continue

        part = stripped
        is_continuation = part.endswith("\\")
        if is_continuation:
            part = part[:-1].strip()

        if part:
            values.extend(part.split())

        if not is_continuation:
            inside_var = False

    return values


def get_makefile_var(makefile_content, var_name):
    """Extract a single makefile variable value."""
    match = re.search(rf"^{var_name}\s*\??=\s*(.*)", makefile_content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


def resolve_root_dir(makefile_content, harness_dir, default_root=None):
    """Resolve the effective ROOT value for the harness makefile."""
    root_val = get_makefile_var(makefile_content, "ROOT") or default_root or harness_dir
    if not os.path.isabs(root_val):
        root_val = os.path.normpath(os.path.join(harness_dir, root_val))
    return root_val


def expand_vars(flags, root_path):
    """Expand ROOT variables inside compiler flags."""
    if not root_path:
        return list(flags)
    return [
        flag.replace("$(ROOT)", root_path).replace("${ROOT}", root_path)
        for flag in flags
    ]


def get_h_def_entries(makefile_content):
    return get_makefile_list_var(makefile_content, "H_DEF")


def get_h_inc_entries(makefile_content, harness_dir, default_root=None):
    root_val = resolve_root_dir(makefile_content, harness_dir, default_root=default_root)
    flags = get_makefile_list_var(makefile_content, "H_INC")
    return expand_vars(flags, root_val)


def build_analysis_args(makefile_content, harness_dir, default_root=None):
    return (
        get_h_def_entries(makefile_content)
        + get_h_inc_entries(makefile_content, harness_dir, default_root=default_root)
    )


def resolve_linked_source_files(makefile_content, harness_dir, default_root=None):
    """Resolve linked C sources from LINK entries for multi-file analysis."""
    root_val = resolve_root_dir(makefile_content, harness_dir, default_root=default_root)
    linked_files = []
    make_include_path = os.path.dirname(harness_dir)

    for entry in get_makefile_list_var(makefile_content, "LINK"):
        resolved = entry.replace("$(ROOT)", root_val).replace("${ROOT}", root_val)
        resolved = resolved.replace("$(MAKE_INCLUDE_PATH)", make_include_path)
        resolved = resolved.replace("${MAKE_INCLUDE_PATH}", make_include_path)
        resolved = os.path.normpath(resolved)
        if not resolved.endswith(".c"):
            continue
        if not os.path.isabs(resolved):
            resolved = os.path.normpath(os.path.join(harness_dir, resolved))
        if os.path.exists(resolved):
            linked_files.append(resolved)

    return linked_files
