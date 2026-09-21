# -*- coding: utf-8 -*-
"""crypto_manager 单元测试：Recovery/KEK-DEK/文件/目录 bundle/错误分类/轮换"""
import os, sys, shutil, tempfile, json, base64
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import crypto_manager as cm  # noqa

def _run():
    t = tempfile.mkdtemp(prefix="wbenc_test_")
    try:
        _t(t)
    finally:
        shutil.rmtree(t, ignore_errors=True)
    print(f"ALL CRYPTO_BUNDLE TESTS PASS")

def _t(d):
    dek = os.urandom(32)
    # Recovery
    rp = os.path.join(d, "recovery.json")
    cm.save_recovery("Passw0rd!", dek, path=rp)
    assert cm.recover_dek("Passw0rd!", path=rp) == dek
    try:
        cm.recover_dek("wrong", path=rp); raise AssertionError("bad pwd accepted")
    except cm.BadPassword:
        pass
    # 篡改 wrap
    with open(rp, encoding="utf-8") as f: pl = json.load(f)
    ct = bytearray(base64.b64decode(pl["wrap"]["ct"])); ct[3] ^= 1
    pl["wrap"]["ct"] = base64.b64encode(bytes(ct)).decode()
    try:
        cm._unwrap_dek(pl["wrap"], cm._kdf_derive("Passw0rd!", base64.b64decode(pl["kdf"]["salt"])), aad=b"WBENC-recovery-v1")
        raise AssertionError("tampered wrap accepted")
    except cm.BadPassword:
        pass
    # 数据
    p = cm.encrypt_bytes(dek, b"hello", {"k": 1})
    assert cm.decrypt_bytes(dek, p) == b"hello"
    p2 = dict(p); p2["meta"] = {"k": 2}
    try:
        cm.decrypt_bytes(dek, p2); raise AssertionError("aad mismatch accepted")
    except cm.AuthFailed:
        pass
    p3 = dict(p); bad = bytearray(base64.b64decode(p3["ciphertext"])); bad[5] ^= 1
    p3["ciphertext"] = base64.b64encode(bytes(bad)).decode()
    try:
        cm.decrypt_bytes(dek, p3); raise AssertionError("tamper accepted")
    except cm.AuthFailed:
        pass
    p4 = dict(p); raw = base64.b64decode(p4["ciphertext"])
    p4["ciphertext"] = base64.b64encode(raw[:len(raw)//2]).decode()
    try:
        cm.decrypt_bytes(dek, p4); raise AssertionError("trunc accepted")
    except (cm.AuthFailed, cm.CorruptedData):
        pass
    p5 = dict(p); p5["version"] = 99
    try:
        cm.decrypt_bytes(dek, p5); raise AssertionError("ver accepted")
    except cm.UnsupportedVersion:
        pass
    p6 = {"format": "WBENC", "version": 1}
    try:
        cm.decrypt_bytes(dek, p6); raise AssertionError("missing accepted")
    except cm.CorruptedData:
        pass
    # 文件/目录
    src = os.path.join(d, "a.txt"); open(src, "w", encoding="utf-8").write("sc")
    enc = os.path.join(d, "a.enc"); cm.encrypt_file(dek, src, enc, {"kind": "t"})
    out = os.path.join(d, "o", "a.txt")
    assert cm.decrypt_file(dek, enc, out) == b"sc"
    sub = os.path.join(d, "prof"); os.makedirs(os.path.join(sub, "nested"))
    open(os.path.join(sub, "n1.json"), "w").write("{}"); open(os.path.join(sub, "nested", "n2.txt"), "w").write("x")
    bp = cm.make_dir_bundle(dek, sub, {"kind": "browser-profile"})
    assert bp["file_manifest"] == ["n1.json", "nested/n2.txt"], bp["file_manifest"]
    rst = os.path.join(d, "rst")
    assert cm.restore_dir_bundle(dek, bp, rst) == 2
    assert open(os.path.join(rst, "nested", "n2.txt")).read() == "x"
    # 轮换
    r1 = os.path.join(d, "r1.json"); cm.save_recovery("oldPwd", dek, path=r1)
    r2 = cm.rotate_recovery_password("oldPwd", "newPwd!", payload=None, path=r1)
    assert cm.recover_dek("newPwd!", payload=r2) == dek
    try:
        cm.recover_dek("oldPwd", payload=r2); raise AssertionError("old still works")
    except cm.BadPassword:
        pass
    # storage_status 不触发 CM 写
    st = cm.storage_status()
    assert "local_dek" in st and "format" in st

if __name__ == "__main__":
    _run()
