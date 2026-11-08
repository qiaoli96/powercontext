"""Exercise the exported Code nodes and real HTTP bridge without a Dify instance."""

import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen

import yaml


ROOT = Path(__file__).resolve().parent


def run_turn(graph, address, token, query):
    values = {"sys": {"query": query}, "env": {"GAME_API_URL": address, "GAME_API_TOKEN": token}}

    def expand(text):
        return re.sub(r"\{\{#([^.]+)\.([^#]+)#\}\}", lambda m: values[m[1]][m[2]], text)

    for node in graph["nodes"]:
        data = node["data"]
        if data["type"] == "code":
            namespace = {}
            exec(compile(data["code"], f"DSL:{node['id']}", "exec"), namespace)
            kwargs = {
                var["variable"]: values[var["value_selector"][0]][var["value_selector"][1]]
                for var in data["variables"]
            }
            values[node["id"]] = namespace["main"](**kwargs)
        elif data["type"] == "http-request":
            headers = dict(line.split(":", 1) for line in expand(data["headers"]).splitlines())
            request = Request(expand(data["url"]), expand(data["body"]["data"][0]["value"]).encode(), headers)
            with urlopen(request, timeout=35) as response:
                values[node["id"]] = {"body": response.read().decode(), "status_code": response.status}
        elif data["type"] == "answer":
            return expand(data["answer"])
    raise AssertionError("Missing Dify answer node")


def start(db, secret):
    process = subprocess.Popen(
        [sys.executable, "-u", str(ROOT / "memory_server.py"), "--port", "0", "--db", str(db)],
        env={**os.environ, "GAME_API_TOKEN": secret}, stdout=subprocess.PIPE, text=True,
    )
    line = process.stdout.readline()
    match = re.search(r"listening on 127\.0\.0\.1:(\d+)", line)
    if not match:
        process.kill()
        process.communicate()
        raise RuntimeError(f"Memory server failed to start: {line}")
    return process, f"http://127.0.0.1:{match[1]}"


def stop(process):
    process.terminate()
    stdout, _ = process.communicate(timeout=10)
    print(stdout, end="")


def main():
    graph = yaml.safe_load((ROOT / "lost-and-found.yml").read_text(encoding="utf-8"))["workflow"]["graph"]
    secret = secrets.token_hex(24)
    with tempfile.TemporaryDirectory(prefix="dify-lost-found-") as directory:
        db = Path(directory) / "memory.db"
        process, address = start(db, secret)
        try:
            assert "存档码" in run_turn(graph, address, secret, "你好")
            assert "先答应" in run_turn(graph, address, secret, "A7K9 调查")
            assert "抄进登记簿" in run_turn(graph, address, secret, "A7K9 承诺")
            print("PASS: invalid input, gated clue, recorded promise")
        finally:
            stop(process)

        process, address = start(db, secret)
        try:
            assert "认出了你的存档" in run_turn(graph, address, secret, "A7K9 查看")
            assert "翻出你的旧承诺" in run_turn(graph, address, secret, "A7K9 调查")
            assert "没有失主的名字" in run_turn(graph, address, secret, "A7K9 回答红伞")
            assert "归还成功" in run_turn(graph, address, secret, "A7K9 回答蓝伞")
            assert "已经归还蓝伞" in run_turn(graph, address, secret, "A7K9 查看")
            assert "先答应" in run_turn(graph, address, secret, "B8L2 调查")
            print("PASS: process restart, remembered promise, clue, wrong/correct answers, save isolation")
        finally:
            stop(process)
    print("Verified exported Code nodes and PowerContext HTTP bridge. Dify import/runtime not exercised.")


if __name__ == "__main__":
    main()
