import json
import os
import shlex
from logging import Logger


def find_compile_commands_files(root_dir: str, logger: Logger) -> list[str]:
    candidates = [
        os.path.join(root_dir, "compile_commands.json"),
        os.path.join(root_dir, "build", "compile_commands.json"),
    ]
    existing = [path for path in candidates if os.path.exists(path)]
    if existing:
        logger.info(
            "Found compile_commands.json candidate(s): %s",
            ", ".join(existing),
        )
    else:
        logger.info(
            "No compile_commands.json found in %s or %s.",
            candidates[0],
            candidates[1],
        )
    return existing


def load_compile_entries(path: str, logger: Logger) -> list[dict]:
    try:
        with open(path, "r", encoding="utf-8") as file:
            entries = json.load(file)
    except Exception as exc:
        logger.warning("Failed to load compile_commands.json from %s: %s", path, exc)
        return []

    if not isinstance(entries, list):
        logger.warning("compile_commands.json at %s does not contain a JSON array.", path)
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def resolve_compile_command_file(entry: dict) -> str | None:
    file_path = entry.get("file")
    directory = entry.get("directory")
    if not file_path or not directory:
        return None

    if not os.path.isabs(file_path):
        file_path = os.path.join(directory, file_path)
    return os.path.realpath(file_path)


def find_first_matching_compile_entry(
    root_dir: str,
    target_file_path: str,
    logger: Logger,
) -> dict | None:
    target_real_path = os.path.realpath(target_file_path)
    compile_db_files = find_compile_commands_files(root_dir, logger)

    for compile_db_path in compile_db_files:
        entries = load_compile_entries(compile_db_path, logger)
        for entry in entries:
            resolved_file = resolve_compile_command_file(entry)
            if resolved_file == target_real_path:
                logger.info(
                    "Using first matching compile_commands.json entry from %s for %s.",
                    compile_db_path,
                    target_file_path,
                )
                return entry
        logger.info(
            "No matching compile_commands.json entry for %s in %s.",
            target_file_path,
            compile_db_path,
        )

    logger.info(
        "No matching compile_commands.json entry found for %s.",
        target_file_path,
    )
    return None


def rewrite_compile_seed_path(path_value: str, directory: str, root_dir: str) -> str:
    if os.path.isabs(path_value):
        resolved_path = os.path.realpath(path_value)
    else:
        resolved_path = os.path.realpath(os.path.join(directory, path_value))

    root_real_path = os.path.realpath(root_dir)
    if resolved_path == root_real_path or resolved_path.startswith(root_real_path + os.sep):
        relative_path = os.path.relpath(resolved_path, root_real_path)
        if relative_path == ".":
            return "$(ROOT)"
        return f"$(ROOT)/{relative_path}"
    return resolved_path


def extract_compile_command_seed(entry: dict, root_dir: str) -> tuple[list[str], list[str]]:
    raw_arguments = entry.get("arguments")
    if isinstance(raw_arguments, list):
        command_tokens = [str(token) for token in raw_arguments]
    else:
        raw_command = entry.get("command")
        if not raw_command:
            return [], []
        command_tokens = shlex.split(raw_command)

    directory = entry.get("directory", root_dir)
    h_def_flags: list[str] = []
    h_inc_flags: list[str] = []
    seen_h_def: set[tuple[str, ...]] = set()
    seen_h_inc: set[tuple[str, ...]] = set()
    index = 0

    def add_flag(parts: list[str], destination: list[str], seen: set[tuple[str, ...]]) -> None:
        key = tuple(parts)
        if key in seen:
            return
        seen.add(key)
        destination.extend(parts)

    while index < len(command_tokens):
        token = command_tokens[index]

        if token in ("-D", "-U"):
            if index + 1 < len(command_tokens):
                add_flag([token, command_tokens[index + 1]], h_def_flags, seen_h_def)
                index += 2
                continue
        elif token.startswith("-D") or token.startswith("-U"):
            add_flag([token], h_def_flags, seen_h_def)
            index += 1
            continue

        if token in ("-I", "-isystem", "-iquote", "-include"):
            if index + 1 < len(command_tokens):
                rewritten_path = rewrite_compile_seed_path(
                    command_tokens[index + 1],
                    directory,
                    root_dir,
                )
                add_flag([token, rewritten_path], h_inc_flags, seen_h_inc)
                index += 2
                continue
        elif token.startswith("-I") and token != "-I":
            rewritten_path = rewrite_compile_seed_path(token[2:], directory, root_dir)
            add_flag([f"-I{rewritten_path}"], h_inc_flags, seen_h_inc)
            index += 1
            continue

        index += 1

    return h_def_flags, h_inc_flags


def macro_names_from_flags(flags: list[str]) -> set[str]:
    macro_names: set[str] = set()
    index = 0
    while index < len(flags):
        token = flags[index]
        macro_value = None
        if token in ("-D", "-U"):
            if index + 1 < len(flags):
                macro_value = flags[index + 1]
                index += 2
            else:
                index += 1
        elif token.startswith("-D") or token.startswith("-U"):
            macro_value = token[2:]
            index += 1
        else:
            index += 1

        if not macro_value:
            continue

        macro_name = macro_value.split("=", 1)[0].strip()
        if macro_name:
            macro_names.add(macro_name)

    return macro_names
