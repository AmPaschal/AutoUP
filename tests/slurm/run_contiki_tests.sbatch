#!/bin/bash -l
# FILENAME: run_contiki_tests.sbatch

#SBATCH --account=davisjam
#SBATCH --nodes=1
#SBATCH --ntasks=80
#SBATCH --partition=cpu
#SBATCH --mem=160G
#SBATCH --qos=standby
#SBATCH --time=24:00:00
#SBATCH --job-name=contiki_tests
#SBATCH --output=/home/rcalvome/joboutput/contiki_tests.%j.out
#SBATCH --error=/home/rcalvome/joboutput/contiki_tests.%j.err

set -euo pipefail

echo "Running on node: $(hostname)"
echo "Job start: $(date)"
echo ""

cd $SCRATCH/AutoUP
source .venv/bin/activate

python tests/run_tests.py tests/datasets/contiki_stratified_sampled.csv \
  -p $SCRATCH/contiki-ng/cbmc \
  -m all \
  -b $SCRATCH/contiki-ng \
  -j 10

echo ""
echo "Job end: $(date)"
