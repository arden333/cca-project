import numpy as np

results = {
    "streamcluster_1": [301.80, 294.20, 300.90],
    "freqmine_1": [266.60, 258.60, 258.90],
    "canneal_1": [293.00, 296.40, 293.70],
    "blackscholes_1": [72.80, 73.30, 72.80],
    "vips_1": [96.20, 92.10, 91.70],
    "barnes_1": [66.70, 68.30, 68.40],
    "radix_1": [18.50, 14.40, 15.00],
    "total_1": [938.18, 930.25, 932.15],
    "total_meas_1": [63, 63, 63],
    "total_miss_1": [0, 0, 0],
    "streamcluster_2": [310.70, 309.30, 308.10],
    "freqmine_2": [277.60, 279.00, 272.80],
    "canneal_2": [299.40, 300.40, 292.60],
    "blackscholes_2": [77.40, 81.80, 79.90],
    "vips_2": [74.60, 70.50, 71.90],
    "barnes_2": [117.90, 118.90, 118.00],
    "radix_2": [32.90, 34.40, 28.90],
    "total_2": [1002.47, 1013.55, 997.61],
    "total_meas_2": [67, 68, 67],
    "total_miss_2": [0, 0, 0],
}

for k in results.keys():
    mean = np.mean(results[k])
    var = np.var(results[k])
    print(f"{k}: mean: {mean}, var: {var}")
