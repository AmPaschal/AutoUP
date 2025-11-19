""" Summary report generator """

# System
import json
import os

# Utils
import pandas as pd


METRICS_FOLDER = "metrics/2025-11-03_23-51"


def print_report(df: pd.DataFrame, file_count: int):
    """ Prints the summary report in the terminal"""
    successful_tasks = len(df[(df["type"] == "task_result") & (df["success"].eq(True))])
    failed_tasks = len(df[(df["type"] == "task_result") & (df["success"].eq(False))])
    total_tasks = len(df[(df["type"] == "task_result") & (df["success"].notna())])
    total_attempts = len(df[(df["type"] == "task_attempt")])
    print("# Summary report")
    print("Harness count:", file_count)
    print("## Resolution:")
    print(f"* Total: {successful_tasks}/{total_tasks}")
    print(f"* Rate: {successful_tasks/total_tasks}")
    print("## Attempts:")
    print("* Total: ", total_attempts)
    print("* Average per error: ", total_attempts / total_tasks)
    print("## Fails:")
    print(f"* Total: {failed_tasks}/{total_tasks}")
    print(f"* Rate: {failed_tasks/total_tasks}")
    print("## Tool Calls:")
    print("* Total: ", df["llm_data.function_call_count"].sum())
    print("* Average per attempt: ", df["llm_data.function_call_count"].mean())
    print("## Total tokens:")
    print("* Total: ", df["llm_data.token_usage.total_tokens"].sum())
    print("* Average per attempt", df["llm_data.token_usage.total_tokens"].mean())

def create_dataframe_from_metrics() -> pd.DataFrame:
    """Create a Dataframe with the information of the metrics"""
    df = pd.DataFrame()
    jsonl_files = os.listdir(METRICS_FOLDER)
    for file in jsonl_files:
        file_path = os.path.join(METRICS_FOLDER, file)
        with open(file_path, "r", encoding="utf-8") as f:
            json_list = [json.loads(line) for line in f]
        df = pd.concat([df, pd.json_normalize(json_list)], ignore_index=True)
    return df

def main():
    """Entry point"""
    df = create_dataframe_from_metrics()
    print_report(df, len(os.listdir(METRICS_FOLDER)))


if __name__ == "__main__":
    main()
