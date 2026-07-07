import requests
from bs4 import BeautifulSoup

url = "https://www.basketball-reference.com/players/j/jamesle01.html"
h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
soup = BeautifulSoup(requests.get(url, headers=h, timeout=25).text, "html.parser")
t = soup.find("table", id="per_game_stats")
tbody = t.find("tbody")
rows = tbody.find_all("tr")
print("total tbody rows:", len(rows))
for tr in rows[:4]:
    th = tr.find("th")
    print("\nTH data-stat=", th.get("data-stat") if th else None, "text=", repr(th.get_text(strip=True)) if th else None)
    cells = [(td.get("data-stat"), td.get_text(strip=True)) for td in tr.find_all(["td"])]
    print("  cells:", cells[:12])
