/**
 * This file resolves the function the user is targeting from the active editor.
 */
import * as vscode from "vscode";

/**
 * Function target resolved from the current editor position.
 */
export interface ResolvedFunction {
  functionName: string;
  filePath: string;
  line: number;
  column: number;
}

/**
 * Detect the function surrounding the current cursor location.
 *
 * Inputs:
 * - `editor`: Active text editor where the command was invoked.
 *
 * Returns:
 * - `Promise<ResolvedFunction | null>`: Resolved function metadata or `null`
 *   when no unambiguous function can be found.
 */
export async function detectFunctionAtCursor(
  editor: vscode.TextEditor,
): Promise<ResolvedFunction | null> {
  // Use the exact active cursor position, not the whole selection range.
  const position = editor.selection.active;
  // Ask the language tooling first because symbol providers are more reliable
  // than raw text parsing.
  const symbols =
    (await vscode.commands.executeCommand<vscode.DocumentSymbol[]>(
      "vscode.executeDocumentSymbolProvider",
      editor.document.uri,
    )) ?? [];

  // Prefer a structured symbol match when one exists.
  const matchingSymbol = findMatchingSymbol(symbols, position);
  if (matchingSymbol) {
    return {
      functionName: matchingSymbol.name,
      filePath: editor.document.uri.fsPath,
      line: position.line + 1,
      column: position.character + 1,
    };
  }

  // Fall back to a conservative text scan when no symbol provider answer exists.
  const fallback = detectFunctionByText(editor.document, position.line);
  if (!fallback) {
    return null;
  }

  return {
    functionName: fallback,
    filePath: editor.document.uri.fsPath,
    line: position.line + 1,
    column: position.character + 1,
  };
}

function findMatchingSymbol(
  symbols: vscode.DocumentSymbol[],
  position: vscode.Position,
): vscode.DocumentSymbol | null {
  /**
   * Recursively search document symbols for the function containing a position.
   *
   * Inputs:
   * - `symbols`: Symbol tree returned by the document symbol provider.
   * - `position`: Cursor location to match.
   *
   * Returns:
   * - `vscode.DocumentSymbol | null`: Matching symbol or `null`.
   */
  for (const symbol of symbols) {
    // Accept both function and method kinds because some language servers use
    // slightly different symbol classifications.
    if (
      (symbol.kind === vscode.SymbolKind.Function ||
        symbol.kind === vscode.SymbolKind.Method) &&
      symbol.range.contains(position)
    ) {
      return symbol;
    }
    // Search nested symbols so deeply nested function nodes are still found.
    const nested = findMatchingSymbol(symbol.children, position);
    if (nested) {
      return nested;
    }
  }
  return null;
}

function detectFunctionByText(document: vscode.TextDocument, line: number): string | null {
  /**
   * Fallback text-based function detection for simple C/C++ cases.
   *
   * Inputs:
   * - `document`: Source document to inspect.
   * - `line`: Zero-based line index near the user's cursor.
   *
   * Returns:
   * - `string | null`: Detected function name or `null`.
   */
  const pattern = /([A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*\{?/;
  // Scan upward through nearby lines because the cursor may be inside the body
  // rather than on the signature itself.
  for (let currentLine = line; currentLine >= 0 && currentLine >= line - 40; currentLine -= 1) {
    const text = document.lineAt(currentLine).text;
    const match = pattern.exec(text);
    if (!match) {
      continue;
    }
    const candidate = match[1];
    // Skip control-flow keywords that match the regex but are not functions.
    if (["if", "for", "while", "switch", "return"].includes(candidate)) {
      continue;
    }
    // Ignore ordinary statements such as assignments or function calls inside
    // a body because they are not function definitions.
    if (text.includes("=")) {
      continue;
    }
    // Treat lines ending in ';' as declarations or calls unless they also open
    // a body immediately, which a definition would do.
    if (text.includes(";") && !text.includes("{")) {
      continue;
    }
    // Accept inline opening braces immediately.
    if (text.includes("{")) {
      return candidate;
    }
    // Also accept a signature whose opening brace appears on the next
    // non-empty line.
    const nextNonEmptyLine = findNextNonEmptyLine(document, currentLine + 1);
    if (nextNonEmptyLine && nextNonEmptyLine.trim() === "{") {
      return candidate;
    }
  }
  return null;
}

function findNextNonEmptyLine(
  document: vscode.TextDocument,
  startLine: number,
): string | null {
  /**
   * Find the next non-empty line after a candidate signature.
   *
   * Inputs:
   * - `document`: Source document being scanned.
   * - `startLine`: Zero-based line index to begin searching from.
   *
   * Returns:
   * - `string | null`: The next non-empty line text or `null` when none exists.
   */
  for (let currentLine = startLine; currentLine < document.lineCount; currentLine += 1) {
    // Skip blank lines so brace-only signatures can still be matched.
    const text = document.lineAt(currentLine).text;
    if (text.trim().length === 0) {
      continue;
    }
    return text;
  }
  return null;
}
