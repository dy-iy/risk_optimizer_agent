from pathlib import Path
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
INPUT_CSV = DATA_DIR / "raw_news.csv"
OUTPUT_CSV = DATA_DIR / "raw_1000_news.csv"
N = 1000

input_csv = Path(INPUT_CSV)
output_csv = Path(OUTPUT_CSV)

try:
    df = pd.read_csv(input_csv, encoding="utf-8-sig")
except Exception:
    df = pd.read_csv(input_csv)

out_df = df.head(N).copy()
output_csv.parent.mkdir(parents=True, exist_ok=True)
out_df.to_csv(output_csv, index=False, encoding="utf-8-sig")

print(f"总行数: {len(df)}")
print(f"提取行数: {len(out_df)}")
print(f"输出文件: {output_csv}")
