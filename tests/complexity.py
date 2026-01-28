"""Compute per-function NLOC and cyclomatic complexity from a CSV list."""

# System
from pathlib import Path
import argparse

# Utils
import pandas as pd
import lizard


def main():
    """Entry point"""
    parser = argparse.ArgumentParser(
        description="Compute per-function NLOC and cyclomatic complexity from a CSV list."
    )
    parser.add_argument("--input-csv", required=True, help="Input CSV file.")
    parser.add_argument("--output-csv", required=True, help="Output CSV file.")
    args = parser.parse_args()

    df = pd.read_csv(args.input_csv)

    nloc_list = []
    ccn_list = []
    status_list = []

    for _, row in df.iterrows():
        file_path = Path(row["source_file"])
        func_name = str(row["function_name"]).strip()

        if not file_path.exists():
            nloc_list.append(None)
            ccn_list.append(None)
            status_list.append("file_not_found")
            continue

        try:
            analysis = lizard.analyze_file(str(file_path))
        except Exception:
            nloc_list.append(None)
            ccn_list.append(None)
            status_list.append("parse_error")
            continue

        funcs = analysis.function_list
        matches = [f for f in funcs if f.name == func_name]

        if len(matches) == 1:
            f = matches[0]
            nloc_list.append(int(f.nloc))
            ccn_list.append(int(f.cyclomatic_complexity))
            status_list.append("ok")
        elif len(matches) > 1:
            f = matches[0]
            nloc_list.append(int(f.nloc))
            ccn_list.append(int(f.cyclomatic_complexity))
            status_list.append("ambiguous_multiple_matches")
        else:
            nloc_list.append(None)
            ccn_list.append(None)
            status_list.append("function_not_found")

    df["nloc"] = nloc_list
    df["cyclomatic_complexity"] = ccn_list
    df["status"] = status_list

    df.to_csv(args.output_csv, index=False)


if __name__ == "__main__":
    main()
