import requests
from bs4 import BeautifulSoup

url = "https://www.basketball-reference.com/players/j/jamesle01.html"
h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
soup = BeautifulSoup(requests.get(url, headers=h, timeout=25).text, "html.parser")
t = soup.find("table", id="per_game_stats")
tbody = t.find("tbody")
count = 0
for tr in tbody.find_all("tr"):
    th = tr.find("th", {"data-stat": "season"})
    if not th:
        continue
    sid = th.get_text(strip=True)
    if sid not in ("2017-18", "2018-19", "2019-20"):
        continue
    # dump data-stat -> value for team + gs candidates
    tds = {td.get("data-stat"): td.get_text(strip=True) for td in tr.find_all("td")}
    print(sid, "| team_name_abbr=", tds.get("team_name_abbr"), "team_id=", tds.get("team_id"),
          "| gs=", tds.get("games_started"), "games_started_alt=", tds.get("gs"))
    count += 1
print("rows matched:", count)
