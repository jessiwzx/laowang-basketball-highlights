"""路由层骨架：静态页能出、根路径重定向、错误映射装上了。

不起浏览器、不碰 work/——只用 TestClient 打这个进程里的 app 对象。"""
from fastapi.testclient import TestClient

from bbh.web.app import create_app

client = TestClient(create_app())


def test_root_redirects_to_workbench():
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/edit.html"


def test_three_pages_are_served():
    for page in ("edit.html", "wall.html", "calib.html"):
        r = client.get("/" + page)
        assert r.status_code == 200, page
        assert "篮球集锦" in r.text


def test_unknown_path_is_404():
    r = client.get("/nope.html")
    assert r.status_code == 404
    # 契约：失败体恒为 {"error": 中文}。裸 Starlette 会给 {"detail": "Not Found"}
    # ——键和语言都不对，前端弹出来是英文乱码，所以这里连文案一起钉住。
    err = r.json()["error"]
    assert err and any("一" <= ch <= "鿿" for ch in err), err
