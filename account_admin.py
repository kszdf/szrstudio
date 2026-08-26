# -*- coding: utf-8 -*-
"""
account_admin.py — 工作台账号管理（给局域网同事建号）

用法:
  D:/heygem/py310/Scripts/python.exe account_admin.py create <用户名> <密码> [editor|admin]
  D:/heygem/py310/Scripts/python.exe account_admin.py list
  D:/heygem/py310/Scripts/python.exe account_admin.py reset <用户名> <新密码>
  D:/heygem/py310/Scripts/python.exe account_admin.py verify <用户名> <密码>
  D:/heygem/py310/Scripts/python.exe account_admin.py delete <用户名>

说明: 默认账号 admin/admin888(管理员)。同事访问地址: http://192.168.4.103:8385
"""
import hashlib
import sys
import time

sys.path.insert(0, r"D:\heygem_data\gpt_sovits")
import studio_db


def _default_tenant_id():
    t = studio_db.get_default_tenant()
    return t.get("id") if t else 1


def cmd_create(username, password, role="editor"):
    tid = _default_tenant_id()
    r = studio_db.create_user(tid, username, password, role=role)
    print("创建成功 ✅" if r.get("ok") else f"创建失败: {r.get('error')}")
    return r


def cmd_list():
    import sqlite3
    cx = studio_db._conn()
    rows = cx.execute(
        "SELECT id, tenant_id, username, role, created_at FROM users ORDER BY id").fetchall()
    cx.close()
    print(f"共 {len(rows)} 个账号:")
    for r in rows:
        ts = time.strftime("%Y-%m-%d", time.localtime(r["created_at"]))
        print(f"  #{r['id']}  {r['username']:<16} 角色={r['role']:<7} 租户={r['tenant_id']} 建号={ts}")
    return rows


def cmd_reset(username, new_password):
    tid = _default_tenant_id()
    h = studio_db._hash_pw(new_password)
    cx = studio_db._conn()
    cur = cx.execute("UPDATE users SET password_hash=? WHERE username=? AND tenant_id=?",
                     (h, username, tid))
    cx.commit()
    n = cur.rowcount
    cx.close()
    print(f"已重置 {n} 条（{username}）" if n else f"未找到账号 {username}")
    return n


def cmd_delete(username):
    cx = studio_db._conn()
    cur = cx.execute("DELETE FROM users WHERE username=?", (username,))
    cx.commit()
    n = cur.rowcount
    cx.close()
    print(f"已删除 {n} 条（{username}）" if n else f"未找到账号 {username}")
    return n


def cmd_verify(username, password):
    r = studio_db.verify_user(username, password)
    print(f"登录验证 {'通过 ✅ (role=' + r['role'] + ')' if r else '失败 ❌'}")
    return r


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    act = sys.argv[1]
    if act == "create" and len(sys.argv) >= 4:
        cmd_create(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "editor")
    elif act == "list":
        cmd_list()
    elif act == "reset" and len(sys.argv) >= 4:
        cmd_reset(sys.argv[2], sys.argv[3])
    elif act == "delete" and len(sys.argv) >= 3:
        cmd_delete(sys.argv[2])
    elif act == "verify" and len(sys.argv) >= 4:
        cmd_verify(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
