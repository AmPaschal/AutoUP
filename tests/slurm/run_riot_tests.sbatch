#!/bin/bash -l
# FILENAME: run_riot_tests.sbatch

#SBATCH --account=davisjam
#SBATCH --nodes=1
#SBATCH --ntasks=80
#SBATCH --partition=cpu
#SBATCH --mem=160G
#SBATCH --qos=standby
#SBATCH --time=24:00:00
#SBATCH --job-name=riot_tests
#SBATCH --output=/home/rcalvome/joboutput/riot_tests.%j.out
#SBATCH --error=/home/rcalvome/joboutput/riot_tests.%j.err

set -euo pipefail

echo "Running on node: $(hostname)"
echo "Job start: $(date)"
echo ""

cd $SCRATCH/AutoUP
source .venv/bin/activate

python tests/run_tests.py tests/datasets/riot_stratified_sampled.csv \
  -p $SCRATCH/RIOT/cbmc \
  -m all \
  -b $SCRATCH/RIOT \
  -j 10

echo ""
echo "Job end: $(date)"
