"""
Scope Widener Module

Iteratively widens the compilation scope of a CBMC proof harness by
discovering functions without bodies in the GOTO binary, locating their
source files, and adding them to the Makefile's LINK variable.

The process repeats up to a configurable bound `k`:
  - k=1: only the target file (no widening)
  - k=2: also include files that define functions called from the target
  - k=3: include files for the next layer of callees, etc.
"""

import json
import os
import re
from typing import Dict, List, Optional, Set, Tuple

from logger import setup_logger

logger = setup_logger(__name__)


class ScopeWidener:
    """
    Widens the set of source files compiled into the CBMC verification unit.

    This is a utility class (not an AIAgent) that operates programmatically
    without LLM interaction.  It receives the necessary infrastructure
    (container, paths, command executor) via the constructor.
    """

    def __init__(self, agent):
        """
        Args:
            agent: An AIAgent instance that provides execute_command,
                   run_make, update_makefile, get_makefile, harness_dir,
                   root_dir, target_function, and project_container.
        """
        self.agent = agent
        self.harness_dir = agent.harness_dir
        self.root_dir = agent.root_dir
        self.target_function = agent.target_function

    # ------------------------------------------------------------------
    # 1. Extract bodyless functions from the GOTO binary
    # ------------------------------------------------------------------

    def extract_functions_without_body(
        self, goto_file: str
    ) -> List[Dict[str, str]]:
        """
        Extract all reachable, non-internal functions in the GOTO binary
        that do **not** have a body.

        For each function the GOTO symbol table is consulted to retrieve
        the *declaration location* (typically a header file).  This is
        later used to disambiguate cscope results when multiple ``.c``
        files define a function with the same name.

        Args:
            goto_file: Absolute path to the ``.goto`` binary.

        Returns:
            A list of dicts::

                [{"name": "func", "declaration_file": "/abs/path.h"}, ...]

            ``declaration_file`` may be an empty string if the location
            could not be determined.
        """
        # --- list all goto functions ---
        goto_functions_result = self.agent.execute_command(
            f"goto-instrument --list-goto-functions {goto_file} --json-ui",
            workdir=self.harness_dir,
            timeout=60,
        )
        if goto_functions_result.get("exit_code", -1) != 0:
            logger.error("Failed to list functions in GOTO binary.")
            return []

        try:
            goto_functions = json.loads(goto_functions_result["stdout"])
        except (json.JSONDecodeError, KeyError) as exc:
            logger.error(f"Could not parse goto-functions JSON: {exc}")
            return []

        if len(goto_functions) != 3 or "functions" not in goto_functions[2]:
            logger.error("Unexpected format of goto-functions output.")
            return []

        goto_functions_list = goto_functions[2]["functions"]
        logger.info(f"Total functions in GOTO binary: {len(goto_functions_list)}")

        # --- collect functions without bodies (non-internal) ---
        no_body_funcs: Set[str] = {
            func.get("name", "")
            for func in goto_functions_list
            if not func.get("isBodyAvailable", True)
            and not func.get("isInternal", True)
        }

        if not no_body_funcs:
            logger.info("All reachable functions have bodies.")
            return []

        logger.info(f"Functions without bodies (total): {len(no_body_funcs)}")

        # --- get reachable call graph ---
        cg_result = self.agent.execute_command(
            f"goto-instrument --reachable-call-graph {goto_file}",
            workdir=self.harness_dir,
            timeout=60,
        )
        if cg_result.get("exit_code", -1) != 0:
            logger.error("Failed to get reachable call graph.")
            return []

        reachable = self._parse_reachable_functions(cg_result.get("stdout", ""))

        # --- get symbol table for declaration locations ---
        decl_locations = self._get_declaration_locations(goto_file, no_body_funcs)

        # --- intersect: bodyless AND reachable, attach declaration hint ---
        result: List[Dict[str, str]] = []
        for fn in no_body_funcs:
            if fn in reachable:
                decl_info = decl_locations.get(fn, {})
                result.append({
                    "name": fn,
                    "declaration_file": decl_info.get("file", "") if isinstance(decl_info, dict) else "",
                    "declaration_line": decl_info.get("line", "") if isinstance(decl_info, dict) else "",
                })
        logger.info(f"Bodyless + reachable functions: {len(result)}")
        return result

    def _get_declaration_locations(
        self, goto_file: str, function_names: Set[str]
    ) -> Dict[str, Dict[str, str]]:
        """
        Query the GOTO symbol table and return a mapping from function
        name to its declaration info::

            {"func": {"file": "/abs/path.h", "line": "42"}}

        The *file* is typically a header and *line* the line where the
        function is declared.
        """
        sym_result = self.agent.execute_command(
            f"goto-instrument --show-symbol-table {goto_file} --json-ui",
            workdir=self.harness_dir,
            timeout=60,
        )
        if sym_result.get("exit_code", -1) != 0:
            logger.warning("Could not read symbol table for declaration hints.")
            return {}

        try:
            sym_data = json.loads(sym_result["stdout"])
        except (json.JSONDecodeError, KeyError):
            return {}

        if len(sym_data) != 3 or "symbolTable" not in sym_data[2]:
            return {}

        sym_table = sym_data[2]["symbolTable"]
        locations: Dict[str, Dict[str, str]] = {}

        for fn in function_names:
            sym = sym_table.get(fn)
            if not sym:
                continue
            loc = sym.get("location", {})
            named = loc.get("namedSub", {})
            file_rel = named.get("file", {}).get("id", "")
            wd = named.get("working_directory", {}).get("id", "")
            line_id = named.get("line", {}).get("id", "")
            if file_rel and wd:
                locations[fn] = {
                    "file": os.path.normpath(os.path.join(wd, file_rel)),
                    "line": line_id,
                }

        return locations

    @staticmethod
    def _parse_reachable_functions(call_graph_output: str) -> Set[str]:
        """Parse ``goto-instrument --reachable-call-graph`` stdout."""
        funcs: Set[str] = set()
        for line in call_graph_output.splitlines():
            line = line.strip()
            if "->" in line:
                parts = [p.strip() for p in line.split("->")]
                if len(parts) == 2:
                    funcs.add(parts[0])
                    funcs.add(parts[1])
        return funcs

    # ------------------------------------------------------------------
    # 2. Locate a function's source file
    # ------------------------------------------------------------------

    def locate_function_source(
        self,
        function_name: str,
        declaration_hint: str = "",
        declaration_line: str = "",
    ) -> Optional[str]:
        """
        Find the source file that contains the *definition* of
        ``function_name`` using ``cscope``.

        When *declaration_hint* is provided (typically the header where
        the function was declared), candidates whose function signature
        does not match the declaration are rejected.  Among the
        remaining candidates, the one closest in the directory tree to
        the declaration header is preferred.

        Args:
            function_name:    The C function name to look up.
            declaration_hint: Absolute path to the header that declared
                              the function (optional).
            declaration_line: Line number (as string) of the declaration
                              in the header (optional).

        Returns:
            Absolute path to the ``.c`` source file, or ``None`` if not
            found.
        """
        return self._locate_via_cscope(
            function_name, declaration_hint, declaration_line
        )

    # ---- helpers for path-proximity ranking ----------------------------

    @staticmethod
    def _common_prefix_length(path_a: str, path_b: str) -> int:
        """Return the number of common leading path components."""
        parts_a = os.path.normpath(path_a).split(os.sep)
        parts_b = os.path.normpath(path_b).split(os.sep)
        common = 0
        for a, b in zip(parts_a, parts_b):
            if a == b:
                common += 1
            else:
                break
        return common

    def _rank_candidates(
        self,
        candidates: List[str],
        declaration_hint: str,
    ) -> List[str]:
        """
        Sort *candidates* by decreasing path proximity to
        *declaration_hint*.  If no hint is given the original order is
        preserved.
        """
        if not declaration_hint:
            return candidates
        hint_dir = os.path.dirname(os.path.realpath(declaration_hint))
        return sorted(
            candidates,
            key=lambda c: self._common_prefix_length(
                os.path.dirname(os.path.realpath(c)), hint_dir
            ),
            reverse=True,
        )

    # ---- helpers for signature comparison ------------------------------

    def _read_function_signature(
        self, file_path: str, func_name: str, start_line: int
    ) -> str:
        """
        Read a few lines from *file_path* starting at *start_line* and
        return the function signature text (everything up to the opening
        ``{`` or ``;``).

        Uses ``sed`` inside the container so the file doesn't need to be
        on the host filesystem.
        """
        # Read up to 10 lines starting from start_line to capture
        # multi-line signatures.
        end_line = start_line + 10
        result = self.agent.execute_command(
            f"sed -n '{start_line},{end_line}p' {file_path}",
            workdir=self.root_dir,
            timeout=10,
        )
        if result.get("exit_code", -1) != 0:
            return ""

        raw = result.get("stdout", "")

        # Collect lines until we hit '{' or ';' (end of signature)
        sig_lines: List[str] = []
        found_func = False
        for line in raw.splitlines():
            stripped = line.strip()
            if not found_func:
                if func_name in stripped:
                    found_func = True
                else:
                    continue
            sig_lines.append(stripped)
            if "{" in stripped or ";" in stripped:
                break

        signature = " ".join(sig_lines)
        # Remove the body-opening brace or semicolon
        signature = signature.split("{")[0].split(";")[0].strip()
        return signature

    @staticmethod
    def _extract_param_types(signature: str) -> List[str]:
        """
        Given a C function signature string, extract a normalised list
        of parameter type strings (without parameter names).

        Example::

            >>> ScopeWidener._extract_param_types(
            ...     'struct net_buf *net_buf_alloc_fixed'
            ...     '(struct net_buf_pool *pool, k_timeout_t timeout)'
            ... )
            ['struct net_buf_pool *', 'k_timeout_t']
        """
        # Find the parameter list between the outermost parentheses
        paren_start = signature.find("(")
        paren_end = signature.rfind(")")
        if paren_start == -1 or paren_end == -1 or paren_end <= paren_start:
            return []

        params_str = signature[paren_start + 1 : paren_end].strip()
        if not params_str or params_str == "void":
            return []

        # Split by commas (this doesn't handle nested parens/templates
        # but that's rare in C and fine for our purposes)
        raw_params = [p.strip() for p in params_str.split(",")]

        types: List[str] = []
        for param in raw_params:
            # Normalise whitespace
            param = re.sub(r"\s+", " ", param).strip()
            if not param:
                continue

            # If it ends with a pointer symbol followed by a name,
            # keep the pointer with the type.
            # Strategy: strip the last token if it looks like a plain
            # identifier (the parameter name).  Pointer stars belong
            # to the type.
            tokens = param.split()
            if len(tokens) >= 2:
                last = tokens[-1]
                # If the last token is a plain C identifier
                # (not a *, not a keyword like 'int'), it is the name.
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", last) and last not in {
                    "int", "char", "void", "float", "double", "long",
                    "short", "unsigned", "signed", "const", "volatile",
                    "struct", "union", "enum",
                }:
                    param = " ".join(tokens[:-1])
            types.append(re.sub(r"\s+", " ", param).strip())

        return types

    @staticmethod
    def _signatures_compatible(
        expected_types: List[str], candidate_types: List[str]
    ) -> bool:
        """
        Return ``True`` if two parameter-type lists are compatible.

        Checks that:
        1. The number of parameters is the same.
        2. Each corresponding pair of types matches after normalisation
           (simple string equality after collapsing whitespace).
        """
        if len(expected_types) != len(candidate_types):
            return False
        for exp, cand in zip(expected_types, candidate_types):
            exp_norm = re.sub(r"\s+", " ", exp).strip()
            cand_norm = re.sub(r"\s+", " ", cand).strip()
            if exp_norm != cand_norm:
                return False
        return True

    # -------------------------------------------------------------------

    def _locate_via_cscope(
        self,
        function_name: str,
        declaration_hint: str = "",
        declaration_line: str = "",
    ) -> Optional[str]:
        """
        Use ``cscope -dL -1 <symbol>`` to find the definition of a
        function.

        cscope output format (space-separated)::

            <file> <function_context> <line> <text>

        All matching ``.c`` files are collected.  If a *declaration_hint*
        is available, candidates whose function signature does not match
        the declaration are rejected.  The remaining candidates are
        ranked by path proximity to the declaration header.
        """
        result = self.agent.execute_command(
            f"cscope -dL -1 {function_name}",
            workdir=self.root_dir,
            timeout=30,
        )
        if result.get("exit_code", -1) != 0:
            logger.warning(
                f"cscope lookup failed for '{function_name}': "
                f"{result.get('stderr', '')}"
            )
            return None

        stdout = result.get("stdout", "").strip()
        if not stdout:
            logger.warning(f"cscope returned no results for '{function_name}'.")
            return None

        # Collect all candidate .c files with their line numbers
        candidates: List[Tuple[str, int]] = []  # (abs_path, line_number)
        seen_paths: Set[str] = set()

        for line in stdout.splitlines():
            parts = line.split(None, 3)  # file, context, line, text
            if len(parts) < 3:
                continue
            file_path = parts[0]
            if not file_path.endswith(".c"):
                continue
            try:
                line_num = int(parts[2])
            except ValueError:
                line_num = 0
            abs_path = os.path.normpath(
                os.path.join(self.root_dir, file_path)
            )
            if os.path.isfile(abs_path) and abs_path not in seen_paths:
                seen_paths.add(abs_path)
                candidates.append((abs_path, line_num))

        if not candidates:
            logger.warning(
                f"Could not locate a .c definition for '{function_name}'."
            )
            return None

        # --- Signature-based filtering (when we have a declaration) ----
        if declaration_hint and declaration_line and len(candidates) > 1:
            decl_sig = self._read_function_signature(
                declaration_hint, function_name,
                int(declaration_line) if declaration_line.isdigit() else 0,
            )
            expected_params = self._extract_param_types(decl_sig)

            if expected_params:  # only filter if we got a valid parse
                compatible: List[Tuple[str, int]] = []
                for cand_path, cand_line in candidates:
                    cand_sig = self._read_function_signature(
                        cand_path, function_name, cand_line,
                    )
                    cand_params = self._extract_param_types(cand_sig)

                    if self._signatures_compatible(
                        expected_params, cand_params
                    ):
                        compatible.append((cand_path, cand_line))
                        logger.debug(
                            f"  ✓ {cand_path}: signature matches "
                            f"declaration"
                        )
                    else:
                        logger.info(
                            f"  ✗ Rejected '{function_name}' in "
                            f"{cand_path}: signature mismatch "
                            f"(expected {expected_params}, "
                            f"got {cand_params})"
                        )

                if compatible:
                    candidates = compatible
                else:
                    logger.warning(
                        f"No candidates for '{function_name}' matched "
                        f"the declaration signature; falling back to "
                        f"path-proximity ranking only."
                    )

        # --- Path-proximity ranking ------------------------------------
        candidate_paths = [c[0] for c in candidates]
        ranked = self._rank_candidates(candidate_paths, declaration_hint)
        chosen = ranked[0]

        if len(candidate_paths) > 1:
            logger.info(
                f"Multiple .c files define '{function_name}'; "
                f"chose {chosen} (hint: {declaration_hint or 'none'})"
            )
        else:
            logger.info(f"Located '{function_name}' in {chosen}")

        return chosen

    # ------------------------------------------------------------------
    # 3. Update the LINK variable in the Makefile
    # ------------------------------------------------------------------

    def add_source_files_to_makefile(self, new_source_files: List[str]) -> None:
        """
        Append *new_source_files* to the ``LINK`` variable in the
        current Makefile.

        Each path is converted to a ``$(ROOT)/…`` relative reference so
        the Makefile stays portable.

        Args:
            new_source_files: List of absolute paths to ``.c`` files.
        """
        makefile_content = self.agent.get_makefile()

        # Build $(ROOT)-relative paths
        root = os.path.realpath(self.root_dir)
        new_entries: List[str] = []
        for src in new_source_files:
            real_src = os.path.realpath(src)
            try:
                rel = os.path.relpath(real_src, root)
            except ValueError:
                # On Windows different drives can cause this; unlikely here
                rel = real_src
            new_entries.append(f"$(ROOT)/{rel}")

        # Parse existing LINK value (may span multiple lines with '\')
        # We'll find the LINK line(s) and append to them.
        link_pattern = re.compile(r"^(LINK\s*=.*)$", re.MULTILINE)
        match = link_pattern.search(makefile_content)

        if not match:
            # No LINK variable found — append one at the end
            link_line = "LINK = " + " \\\n      ".join(new_entries)
            makefile_content += "\n" + link_line + "\n"
        else:
            # Find the full extent of the LINK value (possibly multi-line with \)
            link_start = match.start()
            pos = match.end()
            line_end = pos  # default if the while-loop body never runs
            while pos < len(makefile_content):
                # Check if the current line ends with a backslash (continuation)
                line_end = makefile_content.find("\n", pos)
                if line_end == -1:
                    line_end = len(makefile_content)
                current_line = makefile_content[pos:line_end]
                if current_line.rstrip().endswith("\\"):
                    pos = line_end + 1
                else:
                    break
            link_end = line_end if line_end < len(makefile_content) else len(makefile_content)

            existing_link = makefile_content[link_start:link_end]

            # Append new entries
            additions = " \\\n      ".join(new_entries)
            # Ensure the last existing line ends with a backslash
            if existing_link.rstrip().endswith("\\"):
                updated_link = existing_link + "\n      " + additions
            else:
                updated_link = existing_link.rstrip() + " \\\n      " + additions

            makefile_content = (
                makefile_content[:link_start]
                + updated_link
                + makefile_content[link_end:]
            )

        self.agent.update_makefile(makefile_content)
        logger.info(
            f"Updated LINK in Makefile with {len(new_entries)} new file(s): "
            + ", ".join(new_entries)
        )

    # ------------------------------------------------------------------
    # 4. Get currently linked source files from the Makefile
    # ------------------------------------------------------------------

    def get_linked_source_files(self) -> Set[str]:
        """
        Parse the current Makefile and return the set of absolute source
        file paths already listed in the ``LINK`` variable.
        """
        makefile_content = self.agent.get_makefile()
        root = os.path.realpath(self.root_dir)

        linked: Set[str] = set()

        # Collect the full LINK value (may span continuation lines)
        link_match = re.search(r"^LINK\s*=\s*(.*)", makefile_content, re.MULTILINE)
        if not link_match:
            return linked

        # Gather all continuation lines
        value_lines = [link_match.group(1)]
        remaining = makefile_content[link_match.end():]
        for line in remaining.split("\n"):
            # Check if previous line continued
            if value_lines[-1].rstrip().endswith("\\"):
                value_lines.append(line)
            else:
                break

        full_value = " ".join(l.rstrip("\\").strip() for l in value_lines)

        # Split on whitespace to get individual file entries
        for token in full_value.split():
            # Resolve $(ROOT) and $(MAKE_INCLUDE_PATH) references
            resolved = token.replace("$(ROOT)", root)
            resolved = resolved.replace(
                "$(MAKE_INCLUDE_PATH)",
                os.path.dirname(self.harness_dir),
            )
            resolved = os.path.normpath(resolved)
            if resolved.endswith(".c"):
                linked.add(os.path.realpath(resolved))

        return linked

    # ------------------------------------------------------------------
    # 5. Main entry point — iterative scope widening
    # ------------------------------------------------------------------

    def widen_scope(self, scope_bound: int) -> bool:
        """
        Iteratively widen the compilation scope up to *scope_bound*.

        Starting from level 1 (the target file only), each iteration:
        1. Compiles the current Makefile
        2. Finds bodyless reachable functions in the GOTO binary
        3. Locates their source files via cscope
        4. Adds new source files to the Makefile's ``LINK``
        5. Uses ``MakefileGenerator`` to fix compilation errors

        Stops early if no new source files are discovered.

        Args:
            scope_bound: Maximum depth of scope widening (1 = no widening).

        Returns:
            ``True`` if compilation succeeds after widening, ``False``
            otherwise.
        """
        if scope_bound <= 1:
            logger.info("Scope bound is 1; no widening needed.")
            return True

        # Import here to avoid circular imports
        from makefile_generator.makefile_generator import MakefileGenerator

        current_level = 1

        while current_level < scope_bound:
            current_level += 1
            logger.info(
                f"=== Scope widening: level {current_level}/{scope_bound} ==="
            )

            # 1. Compile to produce the GOTO binary
            make_results = self.agent.run_make(compile_only=True)
            if make_results.get("exit_code", -1) != 0:
                logger.error(
                    "Compilation failed before scope widening at "
                    f"level {current_level}. Attempting to fix with "
                    "MakefileGenerator..."
                )
                mg = MakefileGenerator(
                    args=self.agent.args,
                    project_container=self.agent.project_container,
                )
                if not mg.generate():
                    logger.error(
                        "MakefileGenerator could not fix compilation. "
                        "Aborting scope widening."
                    )
                    return False

            # 2. Find bodyless functions
            goto_file = os.path.join(
                self.harness_dir,
                "build",
                f"{self.target_function}.goto",
            )
            if not os.path.exists(goto_file):
                logger.error(f"GOTO file not found: {goto_file}")
                return False

            bodyless_funcs = self.extract_functions_without_body(goto_file)
            if not bodyless_funcs:
                logger.info(
                    "No bodyless functions found — scope widening "
                    "complete."
                )
                return True

            # 3. Locate source files for each bodyless function
            already_linked = self.get_linked_source_files()
            new_files: List[str] = []

            for func_info in bodyless_funcs:
                func_name = func_info["name"]
                decl_hint = func_info.get("declaration_file", "")
                decl_line = func_info.get("declaration_line", "")
                src_path = self.locate_function_source(
                    func_name,
                    declaration_hint=decl_hint,
                    declaration_line=decl_line,
                )
                if src_path is None:
                    continue
                real_src = os.path.realpath(src_path)
                if real_src in already_linked:
                    logger.debug(
                        f"'{func_name}' source {real_src} already linked."
                    )
                    continue
                if real_src not in [os.path.realpath(f) for f in new_files]:
                    new_files.append(src_path)

            if not new_files:
                logger.info(
                    "No new source files discovered — scope widening "
                    "complete."
                )
                return True

            logger.info(
                f"Adding {len(new_files)} new file(s) to LINK: "
                + ", ".join(new_files)
            )

            # 4. Update Makefile
            self.add_source_files_to_makefile(new_files)

            # 5. Fix compilation with MakefileGenerator
            mg = MakefileGenerator(
                args=self.agent.args,
                project_container=self.agent.project_container,
            )
            if not mg.generate():
                logger.error(
                    f"MakefileGenerator could not fix compilation at "
                    f"scope level {current_level}. Aborting widening."
                )
                return False

            logger.info(
                f"Scope widening level {current_level} succeeded."
            )

        logger.info("Scope widening completed successfully.")
        return True
