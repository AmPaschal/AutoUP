import sqlite3
import sys
import os
import regex as re

from filelock import FileLock, Timeout
from pathlib import Path
from tree_sitter_language_pack import get_binding, get_language, get_parser
from logger import setup_logger


logger = setup_logger(__name__)

DB_FILE = "repo_index.db"


class TreeSitterParser:
    def __init__(self, root_dir, target_func, target_file_path):
        self.root_dir = root_dir

        self.parser = get_parser("c")
        self.conn = sqlite3.connect(os.path.join(self.root_dir, DB_FILE))
        self.cursor = self.conn.cursor()
        # self.repo_name = self.get_repo_name(target_file_path)
        self.repo_name = Path(self.root_dir).name
        self.table_name = self.repo_name + "_definitions"

        self.conn.commit()
        self.target_func = target_func

        lock_path = os.path.join(self.root_dir, ".treesitter.lock")
        lock = FileLock(lock_path, timeout=300)

        try:
            with lock:
                if not self.check_repo_is_indexed():
                    self.cursor.execute(f"""
                    CREATE TABLE IF NOT EXISTS {self.table_name} (
                        id INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        file TEXT NOT NULL,
                        start_byte INT,
                        end_byte INT
                    )
                    """)
                    logger.info(f"Indexing all symbol definitions for the {self.repo_name} repo")
                    self.index_repo()
                else:
                    logger.info(f"Existing indexes found for the {self.repo_name} repo")

                self.target_defs = self.get_target_defs(target_file_path)
        except Timeout:
            logger.info("[*] Another process is building the cscope database; skipping.")

    def get_repo_name(self, target_file_path):
        # Extract the repo name
        target_path = Path(target_file_path).expanduser().resolve()
        home_dir = Path.home().resolve()
        return str(target_path.relative_to(home_dir).parts[0])

    def check_repo_is_indexed(self):
        self.cursor.execute(
            """
            SELECT count(*)
            FROM sqlite_master
            WHERE type='table' AND name=?
        """,
            (self.table_name,),
        )
        return self.cursor.fetchone()[0] == 1

    def walk_repo(self, repo_path, extensions=None):
        """
        Recursively walk a repo and yield all file paths matching given extensions.

        :param repo_path: Root path of the repository
        :param extensions: Set of file extensions to include (e.g., {'.c', '.cpp', '.h'})
        :yield: Full path to each matching file
        """
        extensions = set(extensions) if extensions else None
        print(repo_path)
        for root, dirs, files in os.walk(repo_path):
            for file in files:
                if extensions:
                    if not any(file.endswith(ext) for ext in extensions):
                        continue
                yield os.path.join(root, file)

    def walk_defs(self, code, filename, node, defs):
        if node.type == "function_definition":
            # Get the function name
            func_decl_node = node.child_by_field_name("declarator")
            while func_decl_node.type == "pointer_declarator":  # Need to "unwrap" pointers in function defs
                func_decl_node = func_decl_node.child_by_field_name("declarator")

            func_name_node = func_decl_node.child_by_field_name("declarator") or func_decl_node

            if (
                func_name_node and func_name_node.type == "identifier"
            ):  # There seems to be some false positives on function definitions, so add this as a failsafe
                name = code[func_name_node.start_byte : func_name_node.end_byte].decode("utf8", errors="replace")
                defs.append((name, filename, node.start_byte, node.end_byte))

        elif node.type == "type_definition":
            decl_node = node.child_by_field_name("declarator")
            if decl_node.type == "function_declarator":
                func_name = decl_node.child_by_field_name("declarator")
                if func_name.type == "parenthesized_declarator":  # We still have more unwrapping to do
                    func_name = func_name.named_children[0]  # This should be the pointer declarator which isn't named
                    if func_name.type == "pointer_declarator":
                        func_name = func_name.child_by_field_name("declarator")
                        assert func_name.type == "type_identifier"
                name = code[func_name.start_byte : func_name.end_byte].decode("utf8", errors="replace")
            else:
                name = code[decl_node.start_byte : decl_node.end_byte].decode("utf8", errors="replace")

            defs.append((name, filename, node.start_byte, node.end_byte))

        # There are instances of struct declarations that are typedefed elsewhere, we want to track those without accidentally catching typedefs or structs within structs
        elif node.type == "struct_specifier" and node.parent.type not in ["type_definition", "field_declaration", "declaration"]:
            struct_fields = node.child_by_field_name("body")
            if struct_fields and struct_fields.type == "field_declaration_list":
                decl_node = node.child_by_field_name("name")
                if decl_node:  # Ignore unnamed structs
                    name = code[decl_node.start_byte : decl_node.end_byte].decode("utf8", errors="replace")
                    defs.append((name, filename, node.start_byte, node.end_byte))

        for child in node.children:
            self.walk_defs(code, filename, child, defs)

    def index_repo(self):
        for filepath in self.walk_repo(self.root_dir, extensions={".c", ".h"}):
            with open(filepath, "rb") as f:
                code = f.read()
            tree = self.parser.parse(code)
            root = tree.root_node
            defs = []
            self.walk_defs(code, filepath, root, defs)
            self.cursor.executemany(f"INSERT INTO {self.table_name} (name, file, start_byte, end_byte) VALUES (?, ?, ?, ?)", defs)
            self.conn.commit()

    def extract_definition(self, source_file, start, end):
        with open(source_file, "rb") as f:
            data = f.read()
        return data[start:end].decode("utf8", errors="replace")

    def query_def(self, name):
        self.cursor.execute(
            f"""
        SELECT file, start_byte, end_byte
        FROM {self.table_name} 
        WHERE name = ?
        LIMIT 1
        """,
            (name,),
        )

        return self.cursor.fetchone()

    def query_full_defs(self, symbols):
        indexes = dict()

        # May need to adjust if there is more than 1 match
        for query in symbols:
            row = self.query_def(query)

            if row:
                file, start_byte, end_byte = row
                full_def = self.extract_definition(file, start_byte, end_byte)

                typedef_regex = re.match(r"typedef (?:struct )?([a-zA-Z_0-9]+) ([a-zA-Z_0-9]+)", full_def)
                if typedef_regex:
                    # print(f"regex matched for {query}")
                    # print(typedef_regex.groups())
                    original_def = typedef_regex.group(1)
                    # print(original_def)
                    self.cursor.execute(
                        f"""
                    SELECT file, start_byte, end_byte
                    FROM {self.table_name}
                    WHERE name = ?
                    LIMIT 1
                    """,
                        (original_def,),
                    )
                    row = self.cursor.fetchone()
                    if row:  # if we found an original definition
                        file, start_byte, end_byte = row
                        full_def += "\n" + self.extract_definition(file, start_byte, end_byte)
                    else:
                        print(f"Failed to find original definition for type {original_def}")

                indexes[query] = full_def
        return indexes

    def tree_walk(self, node, symbols, code, in_target):
        if node.type == "function_definition":
            # Get the function name
            func_decl_node = node.child_by_field_name("declarator")
            func_name_node = func_decl_node.child_by_field_name("declarator") or func_decl_node
            name = code[func_name_node.start_byte : func_name_node.end_byte]
            if name == self.target_func:
                in_target = True
                # functions.append({"name": name, "file": filepath, "start": node.start_byte, "end": node.end_byte})
        elif in_target:
            if node.type == "call_expression":
                func_node = node.child_by_field_name("function")
                if func_node is not None:
                    func_name = code[func_node.start_byte : func_node.end_byte]
                    symbols.add(func_name)
            elif node.type == "parameter_declaration":
                type_node = node.child_by_field_name("type")
                if type_node is not None:
                    type_name = code[type_node.start_byte : type_node.end_byte]
                    symbols.add(type_name)
            elif node.type == "declaration":
                type_node = node.child_by_field_name("type")
                if type_node is not None and type_node.type != "primitive_type":
                    type_name = code[type_node.start_byte : type_node.end_byte].decode("utf8", errors="replace")
                    symbols.add(type_name)

        for child in node.children:
            self.tree_walk(child, symbols, code, in_target)

    def get_target_defs(self, target_func_file):
        """
        Extracts all relevant symbol defs for the target file
        """
        with open(target_func_file, "rb") as f:
            code = f.read()
        tree = self.parser.parse(code)
        root = tree.root_node

        # functions = []
        # func_calls = set()
        # types = set()
        symbols = set()

        self.tree_walk(root, symbols, code, False)

        return self.query_full_defs(symbols)

    def get_def(self, name):
        print(f"Querying for symbol {name}")
        if name in self.target_defs:
            return self.target_defs[name]
        else:
            symbol_def = self.query_def(name)
            if symbol_def:
                file, start_byte, end_byte = symbol_def
                return self.extract_definition(file, start_byte, end_byte)
            else:
                return f"Unable to find a valid definition for {name}"
