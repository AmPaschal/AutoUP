#!/bin/bash -l
# FILENAME: run_zephyr_tests.sbatch

#SBATCH --account=davisjam
#SBATCH --nodes=1
#SBATCH --ntasks=40
#SBATCH --partition=cpu
#SBATCH --mem=160G
#SBATCH --qos=standby
#SBATCH --time=04:00:00
#SBATCH --job-name=zephyr_tests
#SBATCH --output=/home/rcalvome/joboutput/zephyr_tests.%j.out
#SBATCH --error=/home/rcalvome/joboutput/zephyr_tests.%j.err

set -euo pipefail

echo "Running on node: $(hostname)"
echo "Job start: $(date)"
echo ""

cd $SCRATCH/AutoUP
source .venv/bin/activate

python tests/run_tests.py tests/datasets/zephyr_stratified_sampled.csv \
  -p $SCRATCH/zephyr/cbmc \
  -m all \
  -b $SCRATCH/zephyr \
  -j 10

echo ""
echo "Job end: $(date)"

curl -sS -X POST \
  -H "Content-Type: application/json" \
  -d '{"message":"Job Zephyr finished"}' \
  "https://hooks.slack.com/triggers/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"
