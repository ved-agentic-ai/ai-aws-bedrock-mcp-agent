import urllib.request
import traceback

try:
    print("Connecting to http://127.0.0.1:3000/ ...")
    res = urllib.request.urlopen('http://127.0.0.1:3000/')
    print("Status:", res.status)
    print("Content length:", len(res.read()))
except Exception as e:
    print("EXCEPTION:", type(e), e)
    traceback.print_exc()
