import re
import requests
from bs4 import BeautifulSoup, Comment

url = "https://www.basketball-reference.com/players/j/jamesle01.html"
h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
r = requests.get(url, headers=h, timeout=25)
html = r.text

# All table ids in the raw HTML (including inside comments)
ids = sorted(set(re.findall(r'<table[^>]*\bid="([^"]+)"', html)))
print("table ids in raw HTML:", ids)

# Parse, merging commented-out tables back in
soup = BeautifulSoup(html, "html.parser")
comments = soup.find_all(string=lambda t: isinstance(t, Comment))
merged = 0
for c in comments:
    if "<table" in c:
        frag = BeautifulSoup(c, "html.parser")
        for tbl in frag.find_all("table"):
            merged += 1
print("commented tables merged:", merged)

# Which id holds season rows with a 'gs' data-stat?
for tid in ("per_game_stats", "totals_stats", "per_game", "totals"):
    t = soup.find("table", id=tid)
    print(tid, "in-DOM:", t is not None)
