"""Pure dialogue rules. The DSL embeds these functions in Dify Code nodes."""

import json
import re


USAGE = "每轮输入「存档码 指令」，例如 `A7K9 查看`。存档码为 4–32 位英文字母或数字。"
MENU = "可用指令：查看、承诺、调查、回答蓝伞、回答红伞。"


def parse_input(query):
    match = re.fullmatch(r"([A-Za-z0-9]{4,32})\s+(.{1,60})", query.strip())
    code, action = (match[1].upper(), match[2].strip()) if match else ("", "")
    return {"body": json.dumps({"op": "read", "code": code}), "save_code": code, "action": action}


def choose(stage, action):
    if stage == 0:
        if action == "承诺":
            return 1, "管理员把你的承诺抄进登记簿：**先调查来历，绝不冒领**。下次回来输入「调查」，我会把柜台后的记录交给你。"
        return 0, "今晚柜台上有一红一蓝两把伞。管理员压住登记簿说：先答应「承诺」，我才把调查记录交给你。"
    if stage == 1:
        if action == "调查":
            return 2, "管理员翻出你的旧承诺，递给你一张纸条：**失主说，雨伞与晴天的天空同色。** 请选择「回答蓝伞」或「回答红伞」。"
        return 1, "管理员认出了你的存档，记得你答应先调查来历。输入「调查」，就能翻开登记簿。"
    if stage == 2:
        if action == "回答蓝伞":
            return 3, "蓝伞的把手刻着失主的名字。归还成功，你获得了「雨夜守约人」徽章。"
        if action == "回答红伞":
            return 2, "红伞的把手没有失主的名字。管理员请你再想想：晴天的天空是什么颜色？"
        return 2, "线索仍在：失主说雨伞与晴天的天空同色。请选择「回答蓝伞」或「回答红伞」。"
    return 3, "登记簿记着你已经归还蓝伞，并获得「雨夜守约人」徽章。换一个新存档码可以重新调查。"


def plan_turn(body, save_code, action):
    request = {"op": "check", "code": save_code}
    if not save_code:
        return {"body": json.dumps(request), "reply": USAGE}
    state = json.loads(body)
    if not state.get("ok"):
        return {"body": json.dumps(request), "reply": "登记簿暂不可用，请稍后重试。"}
    stage = state["stage"]
    next_stage, reply = choose(stage, action)
    if next_stage != stage:
        request.update(op="advance", expected=stage, stage=next_stage)
    return {"body": json.dumps(request), "reply": f"{reply}\n\n{MENU}"}


def finish_turn(body, reply):
    if not json.loads(body).get("ok"):
        return {"reply": "登记簿没有保存本轮进度。请重试，或先用「查看」确认当前存档。"}
    return {"reply": reply}
