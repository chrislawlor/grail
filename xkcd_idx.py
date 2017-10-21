import sys
import time

import requests

ELASTIC_URL = "http://elastic.locate.social:9200/xkcd/doc/{}"
XKCD_URL_TEMPLATE = "https://xkcd.com/{}/info.0.json"


def index_xkcd_id(i):
    url = XKCD_URL_TEMPLATE.format(i)
    resp = requests.get(url)
    if resp.status_code == 200:
        doc_url = ELASTIC_URL.format(i)
        es_resp = requests.put(doc_url, json=resp.json())
        if es_resp.status_code == 200:
            print(f"Indexed XKCD ID {i}")


if __name__ == '__main__':
    start_id = int(sys.argv[1])
    max_id = int(sys.argv[2])

    for i in range(start_id, max_id+1):
        index_xkcd_id(i)
        time.sleep(0.1)
