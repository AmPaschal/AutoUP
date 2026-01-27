import pandas as pd
import numpy as np

# Load CSV
df = pd.read_csv("root_functions.csv")

# samples
n = 100

# indices uniformly distributed
indices = np.linspace(0, len(df) - 1, n, dtype=int)

# Selection
muestra = df.iloc[indices]

# Save result
muestra.to_csv("zephyr-testcases-sys-sampled.csv", index=False)
