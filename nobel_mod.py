import pandas as pd

df = pd.read_csv("nobel-prize-laureates.csv", encoding = "utf-8")
df2 = df[["awardYear", "category", "name"]]
df2.to_csv("nobel-prize-laureates.csv", index=False, encoding = "utf-8")