import requests

url = "https://4khdhub.dad/love-through-a-prism-series-5331/"
headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

resp = requests.get(url, headers=headers, timeout=30)
print(f"Status: {resp.status_code}")

with open("debug_series.html", "w", encoding="utf-8") as f:
    f.write(resp.text)
print("Saved to debug_series.html")
