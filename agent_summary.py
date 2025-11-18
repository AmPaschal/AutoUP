""" Agent Summary """

# System
import json
import os

# Utils
import pandas as pd

METRICS_FOLDER = "metrics/2025-11-18_00-00"
AGENT_REPORT_FILE_PATH = "metrics/2025-11-18_00-00/agent_report.csv"


def generate_csv_file(df: pd.DataFrame) -> None:
    """ Creates a CSV file """
    df.to_csv(AGENT_REPORT_FILE_PATH)

def create_dataframe_from_agent_metrics() -> pd.DataFrame:
    """Load data and creates the dataframe"""
    df = pd.DataFrame()
    jsonl_files = os.listdir(METRICS_FOLDER)
    jsonl_files = [file for file in jsonl_files if file.endswith(".jsonl")]
    for file in jsonl_files:
        file_path = os.path.join(METRICS_FOLDER, file)
        with open(file_path, "r", encoding="utf-8") as f:
            json_list = [json.loads(line) for line in f]
        json_list = [metric["data"] for metric in json_list if metric["type"] == "agent_result"]
        for metric in json_list:
            metric["file"] = file
        df = pd.concat([df, pd.json_normalize(json_list)], ignore_index=True)
        df = df.set_index("file")
        df = df.groupby(df.index).agg('first')
    return df


def main():
    """Entry point"""
    df = create_dataframe_from_agent_metrics()
    generate_csv_file(df)
    


if __name__ == "__main__":
    main()
