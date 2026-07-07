import requests
from bs4 import BeautifulSoup

url = "https://www.basketball-reference.com/players/j/jamesle01.html"
h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
try:
    r = requests.get(url, headers=h, timeout=25)
    print("status", r.status_code, "len", len(r.text))
    soup = BeautifulSoup(r.text, "html.parser")
    for tid in ("totals", "per_game"):
        t = soup.find("table", id=tid)
        print(tid, "found:", t is not None)
        if t:
            tbody = t.find("tbody")
            for tr in tbody.find_all("tr", class_=lambda c: c != "thead"):
                th = tr.find("th", {"data-stat": "season"})
                if not th or not th.a:
                    continue
                sid = th.a.text.strip()
                if sid not in ("2017-18", "2018-19", "2019-20"):
                    continue
                team_td = tr.find("td", {"data-stat": "team_id"})
                gs_td = tr.find("td", {"data-stat": "gs"})
                print("   ", sid, "team=", team_td.get_text(strip=True) if team_td else None,
                      "gs=", gs_td.get_text(strip=True) if gs_td else None)
            break
except Exception as e:
    print("ERR", repr(e))
