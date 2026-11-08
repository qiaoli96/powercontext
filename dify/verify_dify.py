"""Verify the published Dify app via its official blocking Chat API."""

import json
import os
import secrets
from urllib.request import Request, urlopen


def main():
    token = os.environ.get("DIFY_APP_API_KEY")
    if not token:
        raise SystemExit("Set DIFY_APP_API_KEY for the published Chatflow.")
    address = os.environ.get("DIFY_API_URL", "http://localhost/v1").rstrip("/")
    run = secrets.token_hex(8)
    save_a, save_b = "A" + run.upper(), "B" + run.upper()
    user = f"lost-found-verification-{run}"

    def send(code, command, expected, conversation=""):
        body = {"inputs": {}, "query": f"{code} {command}", "response_mode": "blocking",
                "conversation_id": conversation, "user": user}
        request = Request(f"{address}/chat-messages", json.dumps(body).encode(), {
            "Authorization": f"Bearer {token}", "Content-Type": "application/json",
        })
        with urlopen(request, timeout=90) as response:
            data = json.load(response)
        assert expected in data["answer"], data["answer"]
        assert data["conversation_id"]
        print(json.dumps({"command": command, "conversation_id": data["conversation_id"],
                          "answer": data["answer"]}, ensure_ascii=False), flush=True)
        return data["conversation_id"]

    first = send(save_a, "承诺", "抄进登记簿")
    second = send(save_a, "查看", "认出了你的存档")
    assert first != second
    send(save_a, "调查", "翻出你的旧承诺", second)
    send(save_a, "回答红伞", "没有失主的名字", second)
    send(save_a, "回答蓝伞", "归还成功", second)
    send(save_b, "调查", "先答应")
    print("PASS: Dify API, separate conversations, remembered promise, puzzle and save isolation.")


if __name__ == "__main__":
    main()
