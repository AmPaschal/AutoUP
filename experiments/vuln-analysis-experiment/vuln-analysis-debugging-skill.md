---
name: cbmc-vulnerability-exposure-analysis
description: Use when analyzing CBMC vulnerability-exposure experiments against vulnerability metadata and you need to classify each target as exposed, near-case, not exposed, or inconclusive, then categorize the reason for non-exposure using a fixed investigation order.
---

# CBMC Vulnerability Exposure Analysis

Use this skill to analyze CBMC experiment outputs such as:

- `vulnerability-report.json`
- `validation_result.json`
- `viewer-result.json`
- `viewer-coverage.json`
- `viewer-loop.json`
- proof `index.html`
- harnesses, models, and `Makefile`s
- vulnerability metadata

## High-Level Status Categories

- `Exposed`
  Use when the reported vulnerability strictly matches the metadata target.

- `Near-case`
  Use when a reported vulnerability in vulnerability-report.json does not strictly match the metadata target, but is semantically the same bug path.
  Typical cases:
  - same precondition fixes both vulnerabilities
  - The vulnerability affects the same or closely related variable

- `Not exposed`
  Use when there is no strict match and no semantic near-case.

- `Inconclusive`
  Use when the verification did not complete within the time limit and no result artifacts were produced.

## Non-Exposure Reason Categories

If the result is `Not exposed`, categorize the reason as exactly one primary category.

- `Raw CBMC error`
  Use when an error related to the vulnerability type, sink and line is reported in the raw CBMC output (cbmc.xml) or the JSON (viewer-result.json) and HTML (index.html) results.

- `Verification scope gap`
  Use when the target function, sink, or required helper is not in scope.

- `Model fidelity gap`
  Use when the target sink is modeled, but the model removes the bug-relevant behavior.
  Examples:
  - replaces real `memcpy` with fresh `malloc`
  - omits bounds updates
  - wrapper is covered but sink body is missing

- `Sink not covered`
  Use when the target function is in scope but the target sink line or required path is not executed.

- `Validated precondition`
  Use when the harness contains a precondition that prevents exposure of the target bug but the precondition is not rated as exploitable in vulnerability-report.json.

- `Unwinding insufficient`
  Use when the target bug is not exposed because it depends on loop depth and there is concrete unwind evidence that the configured unwind is too small.

## Investigation Order

Always investigate in this order and stop when you find the first validated primary reason.

1. Check whether the strict target vulnerability was reported in `vulnerability-report.json`.
   If yes: `Exposed`.

2. If not, check whether a semantically similar vulnerability was reported.
   If yes: `Near-case`.
   Reason category: `Nearby but non-matching report`.

3. If not, check `viewer-result.json` and `index.html` for related CBMC errors.
   If nothing relevant appears:
   Reason category: `No reported target or near-case`.

4. Check whether the target sink and required functions are in verification scope.
   If not:
   Reason category: `Verification scope gap`.

5. Check whether the sink or required path is covered.
   If not:
   Reason category: `Sink not covered`.

6. If the sink is modeled, check whether the model preserves the bug-relevant semantics.
   If not:
   Reason category: `Model fidelity gap`.

7. Check whether CBMC reported a different issue on the same run that does not match the target bug mechanism.
   If yes:
   Reason category: `Different vulnerability reported`.

8. Check whether a harness precondition exists that should prevent the target bug.
   If yes, inspect `validation_result.json`.
   - If the precondition is bug-relevant and `violated: true`:
     this supports `Exposed` or `Near-case`, not `Not exposed`.
   - If the precondition is bug-relevant and blocks exposure:
     Reason category: `Blocking precondition`.
   - If validation shows it is a correct invariant:
     Reason category: `Precondition correctly non-violable`.

9. Check whether loop unwinding is the blocker.
   Only use this if there is concrete unwind evidence.
   If yes:
   Reason category: `Unwinding insufficient`.

10. Check whether metadata does not align with the current source revision.
    If yes:
    Reason category: `Metadata mismatch`.

11. If the artifacts are incomplete or truncated, use:
    Final status: `Inconclusive`
    Reason category: `Incomplete artifacts`.

## Output Format

For each target, report:

- `Status`: `Exposed`, `Near-case`, `Not exposed`, or `Inconclusive`
- `Primary reason category`
- `Reason`: one short sentence

If the target is `Not exposed`, always give one primary reason category from the list above.
