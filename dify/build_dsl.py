"""Regenerate the importable Dify Chatflow from the dialogue rules."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent
GAME = (ROOT / "game.py").read_text(encoding="utf-8")


def code(title, function, variables, outputs):
    args = ", ".join(variables)
    return {
        "type": "code", "title": title, "code_language": "python3",
        "code": f"{GAME}\n\ndef main({args}):\n    return {function}({args})\n",
        "variables": [{"variable": key, "value_selector": value} for key, value in variables.items()],
        "outputs": {key: {"type": "string", "children": None} for key in outputs},
    }


def http(title, source):
    return {
        "type": "http-request", "title": title, "method": "post",
        "url": "{{#env.GAME_API_URL#}}/memory",
        "authorization": {"type": "no-auth", "config": None},
        "headers": "Authorization:Bearer {{#env.GAME_API_TOKEN#}}\nContent-Type:application/json",
        "params": "",
        "body": {"type": "json", "data": [{"key": "", "type": "text", "value": "{{#" + source + ".body#}}"}]},
        "timeout": {"connect": 5, "read": 30, "write": 10},
        "ssl_verify": True,
        "retry_config": {"retry_enabled": False, "max_retries": 0, "retry_interval": 100},
    }


def build():
    nodes = [
        ("100", {"type": "start", "title": "玩家输入", "variables": []}),
        ("110", code("解析存档码和指令", "parse_input", {"query": ["sys", "query"]}, ["body", "save_code", "action"])),
        ("120", http("从 PowerContext 读取存档", "110")),
        ("130", code("根据记忆决定剧情", "plan_turn", {
            "body": ["120", "body"], "save_code": ["110", "save_code"], "action": ["110", "action"],
        }, ["body", "reply"])),
        ("140", http("确认或保存进度", "130")),
        ("150", code("确认保存结果", "finish_turn", {
            "body": ["140", "body"], "reply": ["130", "reply"],
        }, ["reply"])),
        ("160", {"type": "answer", "title": "管理员回复", "answer": "{{#150.reply#}}", "variables": []}),
    ]
    graph = {"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 0.7}}
    for index, (node_id, data) in enumerate(nodes):
        position = {"x": 70 + index * 310, "y": 200}
        graph["nodes"].append({
            "id": node_id, "type": "custom", "data": {"desc": "", "selected": False, **data},
            "position": position, "positionAbsolute": position, "height": 58, "width": 244,
            "sourcePosition": "right", "targetPosition": "left", "selected": False,
        })
    for (source, left), (target, right) in zip(nodes, nodes[1:]):
        graph["edges"].append({
            "id": f"{source}-source-{target}-target", "type": "custom",
            "source": source, "target": target, "sourceHandle": "source", "targetHandle": "target",
            "data": {"isInIteration": False, "sourceType": left["type"], "targetType": right["type"]},
        })
    return {
        "kind": "app", "version": "0.3.0", "dependencies": [],
        "app": {
            "name": "失物招领事务所", "mode": "advanced-chat", "icon": "☂️", "icon_background": "#E0F2FE",
            "description": "会记住你承诺的雨夜调查游戏", "use_icon_as_answer_icon": False,
        },
        "workflow": {
            "conversation_variables": [],
            "environment_variables": [
                {"id": "1d50359d-05cc-42fb-a596-324d232b898d", "name": "GAME_API_URL",
                 "value": "http://lost-found-memory:8787", "value_type": "string", "description": "存档服务地址"},
                {"id": "e19c0c8b-17ce-4a61-8059-f35b83a93077", "name": "GAME_API_TOKEN",
                 "value": "", "value_type": "secret", "description": "与存档服务一致的令牌"},
            ],
            "features": {
                "opening_statement": "雨夜的失物招领事务所还亮着灯。发「A7K9 查看」开始。下次开新对话时，用同一个存档码继续。",
                "suggested_questions": ["A7K9 查看", "A7K9 承诺"],
                "suggested_questions_after_answer": {"enabled": False},
                "speech_to_text": {"enabled": False}, "text_to_speech": {"enabled": False},
                "retriever_resource": {"enabled": False}, "sensitive_word_avoidance": {"enabled": False},
                "file_upload": {"enabled": False, "image": {"enabled": False}},
            },
            "graph": graph,
        },
    }


if __name__ == "__main__":
    (ROOT / "lost-and-found.yml").write_text(
        yaml.safe_dump(build(), allow_unicode=True, sort_keys=False, width=120), encoding="utf-8"
    )
