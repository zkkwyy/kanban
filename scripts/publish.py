# -*- coding: utf-8 -*-
"""一键发布到GitHub Pages
用法: python publish.py <token> [--user 用户名]
步骤: 创建公开仓库kanban -> 推送main -> 启用Pages(main/root) -> 回填repo_url -> 等待部署
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

REPO = "kanban"
API = "https://api.github.com"


def gh(token, method, path, body=None):
    import urllib.request
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data, headers={
        "Authorization": "token " + token,
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "kanban-publisher",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(txt) if txt.strip() else {})
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")


def run_git(*args):
    r = subprocess.run(["git"] + list(args), cwd=C.ROOT,
                       capture_output=True, text=True, shell=False)
    if r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (args, r.stderr[-500:]))
    return r.stdout.strip()


def main():
    token = sys.argv[1]
    user = None
    if "--user" in sys.argv:
        user = sys.argv[sys.argv.index("--user") + 1]

    # 0. 获取用户名
    st, me = gh(token, "GET", "/user")
    if st != 200:
        raise SystemExit("token无效或无权限: %s %s" % (st, me.get("message")))
    login = user or me["login"]
    C.log("认证成功: %s (plan=%s)" % (login, me.get("plan") or "?"))

    # 1. 创建公开仓库(若已存在则复用)
    st, res = gh(token, "POST", "/user/repos",
                 {"name": REPO, "private": False,
                  "description": "金融数据看板: ETF资金流/融资余额/指数对比/美股板块 — 免费数据源+自动更新",
                  "has_issues": True})
    if st == 201:
        C.log("仓库已创建: %s/%s" % (login, REPO))
    elif st == 422 and "already exist" in json.dumps(res).lower():
        C.log("仓库已存在, 复用: %s/%s" % (login, REPO))
    else:
        raise SystemExit("创建仓库失败: %s %s" % (st, res))
    full = "%s/%s" % (login, REPO)
    pages_url = "https://%s.github.io/%s/" % (login, REPO)

    # 2. 配置远程并推送(token仅写入remote URL, 不落盘到其他文件)
    try:
        run_git("remote", "remove", "origin")
    except RuntimeError:
        pass  # 尚无origin
    run_git("remote", "add", "origin",
            "https://%s@github.com/%s.git" % (token, full))
    C.log("推送 main (含全量数据)...")
    run_git("push", "-u", "origin", "main")
    C.log("推送完成")

    # 3. 启用 Pages: main / root
    st, res = gh(token, "POST", "/repos/%s/pages" % full,
                 {"source": {"branch": "main", "path": "/"}})
    if st in (201, 202):
        C.log("Pages 已启用")
    elif st == 409:
        C.log("Pages 已存在(409), 检查配置...")
        st2, cur = gh(token, "GET", "/repos/%s/pages" % full)
        if st2 == 200 and cur.get("source", {}).get("branch") != "main":
            gh(token, "PUT", "/repos/%s/pages" % full,
               {"source": {"branch": "main", "path": "/"}})
            C.log("Pages 来源已改为 main/root")
    else:
        C.log("Pages 启用响应: %s %s (继续验证)" % (st, res.get("message")))

    # 4. 回填 repo_url 到 meta.json 与首页链接
    meta = C.load_json(os.path.join(C.DATA, "meta.json")) or {}
    meta["repo_url"] = "https://github.com/%s" % full
    meta["pages_url"] = pages_url
    C.save_json(os.path.join(C.DATA, "meta.json"), meta)
    idx = os.path.join(C.ROOT, "index.html")
    with open(idx, encoding="utf-8") as f:
        html = f.read()
    html = html.replace('href="https://github.com" id="repo-link"',
                        'href="https://github.com/%s" id="repo-link"' % full)
    with open(idx, "w", encoding="utf-8") as f:
        f.write(html)
    run_git("add", "-A")
    r = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=C.ROOT)
    if r.returncode != 0:
        run_git("commit", "-m", "meta: 记录仓库与Pages地址")
        run_git("push")
        C.log("repo_url 已回填并推送")

    # 5. 清理: 从remote URL中移除token(避免长期留在.git/config)
    run_git("remote", "set-url", "origin", "https://github.com/%s.git" % full)
    C.log("remote URL已清理(不含token)")

    # 6. 等待 Pages 部署生效(首次构建通常1-3分钟)
    C.log("等待 Pages 部署: %s" % pages_url)
    import urllib.request
    for i in range(24):
        time.sleep(15)
        try:
            req = urllib.request.Request(pages_url, headers={"User-Agent": C.UA})
            with urllib.request.urlopen(req, timeout=20) as resp:
                if resp.status == 200:
                    C.log("Pages 上线成功: %s (%d秒)" % (pages_url, (i + 1) * 15))
                    C.log("提示: token已用完使命, 可到 github.com/settings/tokens 撤销")
                    return pages_url
        except Exception as e:  # noqa: BLE001
            C.log("  尚未就绪(%s), 继续等待..." % type(e).__name__)
    C.log("等待超时, Pages 构建可能仍在进行; 可稍后手动访问: %s" % pages_url)
    C.log("也可到 https://github.com/%s/actions 查看构建状态" % full)
    return pages_url


if __name__ == "__main__":
    main()
