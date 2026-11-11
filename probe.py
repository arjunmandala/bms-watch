#!/usr/bin/env python3
"""Probe which routes to BMS ALUC showtimes work from this network. Usage: probe.py LABEL [proxy]"""
import sys, json, time, urllib.parse
from curl_cffi import requests as r
label = sys.argv[1]
proxy = sys.argv[2] if len(sys.argv) > 2 else None
D = "20261111"
api = f"https://in.bookmyshow.com/api/v2/mobile/showtimes/byvenue?venueCode=ALUC&dateCode={D}"
direct = {
 "api_byvenue": api,
 "web_venue": f"https://in.bookmyshow.com/cinemas/hyderabad/allu-cinemas-kokapet/buytickets/ALUC/{D}",
 "web_event": f"https://in.bookmyshow.com/buytickets/ramayana-hyderabad/movie-hyd-ET00523251-MT/{D}",
 "api_by_event": f"https://in.bookmyshow.com/api/movies-data/showtimes-by-event?appCode=MOBAND2&appVersion=14304&language=en&eventCode=ET00523251&regionCode=HYD&subRegion=HYD&lat=17.385044&lon=78.486671&query=&dateCode={D}",
 "nonin_host": f"https://bookmyshow.com/api/v2/mobile/showtimes/byvenue?venueCode=ALUC&dateCode={D}",
}
relays = {} if proxy else {
 "jina": "https://r.jina.ai/" + api,
 "allorigins": "https://api.allorigins.win/raw?url=" + urllib.parse.quote(api, safe=""),
 "corsproxy_io": "https://corsproxy.io/?url=" + urllib.parse.quote(api, safe=""),
 "codetabs": "https://api.codetabs.com/v1/proxy?quest=" + urllib.parse.quote(api, safe=""),
 "district_movie": "https://www.district.in/movies/ramayana-movie-tickets-in-hyderabad-MV176276",
}
res = {}
prox = {"http": proxy, "https": proxy} if proxy else None
for name, u in {**direct, **relays}.items():
    imps = ["chrome", "safari_ios"] if name in direct else ["chrome"]
    for imp in imps:
        k = f"{name}/{imp}"
        try:
            t0 = time.time()
            hdr = {"x-no-cache": "true"} if name == "jina" else None
            x = r.get(u, impersonate=imp, timeout=30, proxies=prox, headers=hdr)
            t = x.text or ""
            res[k] = {"status": x.status_code, "len": len(t), "ramayana": "Ramayana" in t or "RAMAYANA" in t,
                      "aluc": "ALUC" in t, "hindi_3pm": ("03:00 PM" in t and "Hindi" in t), "secs": round(time.time()-t0, 1),
                      "head": t[:120].replace("\n", " ") if x.status_code != 200 else ""}
        except Exception as e:
            res[k] = {"error": f"{type(e).__name__}: {str(e)[:150]}"}
        print(label, k, json.dumps(res[k]), flush=True)
try:
    ip = r.get("https://api.ipify.org?format=json", proxies=prox, timeout=20).text
except Exception as e:
    ip = str(e)[:80]
print("PROBE_SUMMARY", label, ip, json.dumps(res))
