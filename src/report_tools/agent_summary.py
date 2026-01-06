""" Agent Summary """

# System
import argparse
import json
import os

# Utils
import pandas as pd


def generate_csv_file(df: pd.DataFrame, metrics_folder: str) -> None:
    """ Creates a CSV file """
    agent_report_file_path = os.path.join(metrics_folder, "agent_report.csv")
    df.to_csv(agent_report_file_path)

def create_dataframe_from_agent_metrics(metrics_folder: str) -> pd.DataFrame:
    """Load data and create the dataframe with preprocessing"""
    df = pd.DataFrame()
    jsonl_files = os.listdir(metrics_folder)
    jsonl_files = [file for file in jsonl_files if file.endswith(".jsonl")]

    for file in jsonl_files:
        file_path = os.path.join(metrics_folder, file)
        with open(file_path, "r", encoding="utf-8") as f:
            json_list = [json.loads(line) for line in f]

        # ---- Preprocess agent results ----
        for metric in json_list:
            if (
                metric.get("type") == "agent_result"
                and metric.get("agent_name") == "CoverageDebugger"
                and metric.get("data", {}).get("final_coverage") == 1
            ):
                metric["data"]["final_coverage"] = metric["data"].get("initial_coverage")

        # Extract agent_result data only
        json_list = [metric["data"] for metric in json_list if metric.get("type") == "agent_result"]

        for metric in json_list:
            metric["file"] = file

        df = pd.concat([df, pd.json_normalize(json_list)], ignore_index=True)

    df = df.set_index("file")
    df = df.groupby(df.index).agg("first")
    return df



def parse_args() -> argparse.Namespace:
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="Generate agent summary report from metrics files")
    parser.add_argument(
        "metrics_folder",
        type=str,
        help="Path to the folder containing metrics JSONL files"
    )
    return parser.parse_args()


def main():
    """Entry point"""
    args = parse_args()
    df = create_dataframe_from_agent_metrics(args.metrics_folder)
    generate_csv_file(df, args.metrics_folder)
    


if __name__ == "__main__":
    main()
