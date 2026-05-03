import requests
import os

url = "https://docs.battlecode.cam/llms.txt"
re = requests.get(url)

all_urls = re.text.split("](")[1:]
all_urls = list(map(lambda x: x[:x.index(")")], all_urls))
good_urls = list(filter(lambda x: x.endswith(".md"), all_urls))

os.makedirs("docs", exist_ok=True)
for url in good_urls:
    file = url.split("/")[-1]
    filepath = os.path.join("docs", file)
    re = requests.get(url)
    with open(filepath, "w", encoding='utf8') as file:
        file.write(re.text)
