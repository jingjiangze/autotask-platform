# -*- coding: utf-8 -*-
"""access_admin 幂等性与结构化返回测试（不触网、不产生真实资源）

做法：把 access_admin._call 换成内存版假 CF API，然后连续执行两次 setup()，
断言第二次全部返回 already_exists，且资源计数不增长。
"""
import json
import os
import re
import sys

sys.path.insert(0, r"D:\web\cf")
import access_admin as A


class FakeCF:
    """最小内存实现：organizations / access apps / access policies

    注意返回契约必须与 access_admin._call 一致：
        成功 -> (True, result, None)
        失败 -> (False, None, {"code":..., "retryable":..., "message":...})
    （不是 deploy_cf.api_raw 的 (status, payload, raw)）
    """

    def __init__(self):
        self.orgs, self.apps, self.policies = [], [], {}
        self.writes = 0

    @staticmethod
    def _ok(result):
        return True, result, None

    @staticmethod
    def _err(code, retryable=False, msg=""):
        return False, None, {"code": code, "retryable": retryable, "message": msg}

    def __call__(self, method, path, **kw):
        body = kw.get("json") or {}
        if path.endswith("/access/organizations"):
            if method == "GET":
                return self._ok(self.orgs)
            self.writes += 1
            o = {"id": "org1", "name": body["name"], "auth_domain": body["auth_domain"]}
            self.orgs.append(o)
            return self._ok(o)

        m = re.match(r"^/accounts/([^/]+)/access/apps$", path)
        if m:
            if method == "GET":
                return self._ok(list(self.apps))
            self.writes += 1
            a = dict(body, id="app%d" % (len(self.apps) + 1))
            self.apps.append(a)
            return self._ok(a)

        m = re.match(r"^/accounts/([^/]+)/access/apps/([^/]+)$", path)
        if m and method == "GET":
            aid = m.group(2)
            a = next((x for x in self.apps if x["id"] == aid), None)
            return self._ok(a) if a else self._err("NOT_FOUND", msg="app not found")

        m = re.match(r"^/accounts/([^/]+)/access/apps/([^/]+)/policies$", path)
        if m:
            aid = m.group(2)
            lst = self.policies.setdefault(aid, [])
            if method == "GET":
                return self._ok(list(lst))
            self.writes += 1
            n = sum(len(v) for v in self.policies.values()) + 1
            p = dict(body, id="pol%d" % n)
            lst.append(p)
            return self._ok(p)

        return self._err("NOT_FOUND", msg=f"unhandled {method} {path}")


def main():
    fake = FakeCF()
    A._call = fake                                        # 替换 HTTP 出口
    A.verify_protected = lambda h, timeout=20: A._result(
        True, "verify_access_protection", hostname=h, protected=True)

    print("=== 第 1 次 setup()：应当全部创建 ===")
    r1 = A.setup()
    print("  success =", r1["success"])
    print("  组织    =", r1["organization"]["status"])
    for t in r1["targets"]:
        print(f"  {t['domain']:26} app={t['app']['status']:14} policy={t['policy']['status']}")
    w1 = fake.writes
    print(f"  -> 写入次数(W): {w1}   资源: orgs={len(fake.orgs)} apps={len(fake.apps)} "
          f"policies={sum(len(v) for v in fake.policies.values())}")

    print("\n=== 第 2 次 setup()：应当全部 already_exists，且零写入 ===")
    r2 = A.setup()
    print("  success =", r2["success"])
    print("  组织    =", r2["organization"]["status"])
    for t in r2["targets"]:
        print(f"  {t['domain']:26} app={t['app']['status']:14} policy={t['policy']['status']}")
    w2 = fake.writes
    print(f"  -> 写入次数增量: {w2 - w1}   资源: orgs={len(fake.orgs)} apps={len(fake.apps)} "
          f"policies={sum(len(v) for v in fake.policies.values())}")

    print("\n===== 判定 =====")
    idem = (w2 == w1
            and r2["organization"]["status"] == "already_exists"
            and all(t["app"]["status"] == "already_exists" for t in r2["targets"])
            and all(t["policy"]["status"] == "already_exists" for t in r2["targets"])
            and len(fake.apps) == 2
            and sum(len(v) for v in fake.policies.values()) == 2)
    print("  重复执行零新增资源 ->", "PASS ✅" if idem else "FAIL ❌")

    struct = all(("success" in t["app"] and "action" in t["app"]
                  and "error_code" in t["app"] and "retryable" in t["app"])
                 for t in r1["targets"])
    print("  返回结构含 success/action/error_code/retryable ->", "PASS ✅" if struct else "FAIL ❌")

    print("\n=== 策略内容核对 ===")
    print(" ", json.dumps(fake.policies["app1"][0]["include"], ensure_ascii=False))
    print("  decision =", fake.policies["app1"][0]["decision"])
    print("  session  =", fake.apps[0]["session_duration"],
          " httponly =", fake.apps[0]["http_only_cookie_attribute"],
          " samesite =", fake.apps[0]["same_site_cookie_attribute"])

    return 0 if (idem and struct) else 1


if __name__ == "__main__":
    sys.exit(main())
