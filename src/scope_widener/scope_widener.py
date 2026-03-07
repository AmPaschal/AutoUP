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
from typing import List, Optional, Set

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

    def extract_functions_without_body(self, goto_file: str) -> List[str]:
        """
        Extract the names of all reachable, non-internal functions in the
        GOTO binary that do **not** have a body.

        This re-uses the same goto-instrument commands as
        ``StubGenerator.extract_functions_without_body_and_returning_pointer``
        but returns *all* bodyless functions, not just pointer-returning ones.

        Args:
            goto_file: Absolute path to the ``.goto`` binary.

        Returns:
            A list of function name strings.
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

        # --- intersect: bodyless AND reachable ---
        result = [fn for fn in no_body_funcs if fn in reachable]
        logger.info(f"Bodyless + reachable functions: {len(result)}")
        return result

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

    def locate_function_source(self, function_name: str) -> Optional[str]:
        """
        Find the source file that contains the *definition* of
        ``function_name`` using ``cscope``.

        The implementation is intentionally isolated here so it can be
        swapped for an alternative (ctags, compile_commands.json, etc.)
        without touching the rest of the module.

        Args:
            function_name: The C function name to look up.

        Returns:
            Absolute path to the ``.c`` source file, or ``None`` if not
            found.
        """
        return self._locate_via_cscope(function_name)

    def _locate_via_cscope(self, function_name: str) -> Optional[str]:
        """
        Use ``cscope -dL -1 <symbol>`` to find the definition of a
        function.

        cscope output format (space-separated):
            <file> <function_context> <line> <text>

        We pick the first ``.c`` result whose function-context column
        matches ``function_name`` (i.e. it is a definition, not just a
        reference inside another function).
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

        for line in stdout.splitlines():
            parts = line.split(None, 3)  # file, context, line, text
            if len(parts) < 3:
                continue
            file_path = parts[0]
            # Only consider .c files (skip headers)
            if not file_path.endswith(".c"):
                continue
            # Resolve to absolute path relative to root_dir
            abs_path = os.path.normpath(os.path.join(self.root_dir, file_path))
            if os.path.isfile(abs_path):
                logger.info(
                    f"Located '{function_name}' in {abs_path}"
                )
                return abs_path

        logger.warning(
            f"Could not locate a .c definition for '{function_name}'."
        )
        return None

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

            for func_name in bodyless_funcs:
                src_path = self.locate_function_source(func_name)
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
