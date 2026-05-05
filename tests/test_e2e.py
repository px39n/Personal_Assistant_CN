"""端到端测试 — 通过 HTTP 调用完整对话流程"""

import httpx
import json

BASE = "http://127.0.0.1:8000"


def test_health():
    r = httpx.get(f"{BASE}/health")
    print(f"[health] {r.status_code} {r.json()}")
    assert r.status_code == 200


def test_skills():
    r = httpx.get(f"{BASE}/api/skills")
    data = r.json()
    print(f"[skills] count={data['count']}, names={[s['name'] for s in data['skills']]}")
    assert data["count"] >= 1


def test_chat_direct():
    """测试闲聊 — 不触发工具"""
    r = httpx.post(f"{BASE}/api/chat", json={
        "message": "你好，介绍一下你自己",
        "stream": False,
        "user_id": "test",
    }, timeout=30)
    data = r.json()
    print(f"\n[chat_direct] status={r.status_code}")
    print(f"  message: {data['message'][:100]}...")
    print(f"  skills_used: {len(data['skill_results'])}")
    assert r.status_code == 200
    assert len(data["message"]) > 0
    assert len(data["skill_results"]) == 0  # 闲聊不该触发工具


def test_chat_with_search():
    """测试搜索场景 — 应触发 web_search（SearxNG 未运行会报错但流程完整）"""
    r = httpx.post(f"{BASE}/api/chat", json={
        "message": "搜索一下2024年诺贝尔物理学奖得主是谁",
        "stream": False,
        "user_id": "test",
    }, timeout=30)
    data = r.json()
    print(f"\n[chat_search] status={r.status_code}")
    print(f"  message: {data['message'][:150]}...")
    print(f"  skills_used: {len(data['skill_results'])}")
    for sr in data["skill_results"]:
        print(f"    - {sr['skill']}: success={sr.get('success')}, error={sr.get('error', 'none')}")
    assert r.status_code == 200


def test_conversation_continuity():
    """测试多轮对话 — 检查会话记忆"""
    # 第一轮
    r1 = httpx.post(f"{BASE}/api/chat", json={
        "message": "我叫张三，请记住",
        "stream": False,
        "user_id": "test",
    }, timeout=30)
    conv_id = r1.json()["conversation_id"]
    print(f"\n[continuity] conv_id={conv_id}")
    print(f"  round1: {r1.json()['message'][:80]}...")

    # 第二轮，同一会话
    r2 = httpx.post(f"{BASE}/api/chat", json={
        "message": "我叫什么名字？",
        "stream": False,
        "user_id": "test",
        "conversation_id": conv_id,
    }, timeout=30)
    print(f"  round2: {r2.json()['message'][:80]}...")
    assert "张三" in r2.json()["message"]  # 应该记得

    # 查看历史
    r3 = httpx.get(f"{BASE}/api/conversations/{conv_id}/history")
    history = r3.json()["messages"]
    print(f"  history_count: {len(history)}")
    assert len(history) == 4  # 2 user + 2 assistant


def test_sse_stream():
    """测试 SSE 流式响应"""
    with httpx.stream("POST", f"{BASE}/api/chat", json={
        "message": "用一句话介绍Python",
        "stream": True,
        "user_id": "test",
    }, timeout=30) as r:
        events = []
        for line in r.iter_lines():
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())
        print(f"\n[sse] event_types: {events}")
        assert "message" in events
        assert "done" in events


if __name__ == "__main__":
    tests = [test_health, test_skills, test_chat_direct, test_chat_with_search, test_conversation_continuity, test_sse_stream]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  ✅ {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  ❌ {t.__name__}: {e}")

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
