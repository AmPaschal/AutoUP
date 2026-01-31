#!/bin/bash -l
# FILENAME: run_freertos_tests.sbatch

#SBATCH --account=davisjam
#SBATCH --nodes=1
#SBATCH --ntasks=80
#SBATCH --partition=cpu
#SBATCH --mem=160G
#SBATCH --qos=standby
#SBATCH --time=24:00:00
#SBATCH --job-name=freertos_tests
#SBATCH --output=/home/rcalvome/joboutput/freertos_tests.%j.out
#SBATCH --error=/home/rcalvome/joboutput/freertos_tests.%j.err

set -euo pipefail

echo "Running on node: $(hostname)"
echo "Job start: $(date)"
echo ""

cd $SCRATCH/AutoUP
source .venv/bin/activate

python tests/run_tests.py tests/datasets/freertos_stratified_sampled.csv \
  -p $SCRATCH/FreeRTOS-Plus-TCP/cbmc \
  -m all \
  -b $SCRATCH/FreeRTOS-Plus-TCP \
  -j 10

echo ""
echo "Job end: $(date)"
