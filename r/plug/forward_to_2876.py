#!/usr/bin/env python3
import http.client
import os
import sys
import urllib.parse


HOST = os.environ.get("PLUG_CONTROL_HOST", "127.0.0.1")
PORT = int(os.environ.get("PLUG_CONTROL_PORT", "2876"))


def main():
    if len(sys.argv) < 2:
        print(f"用法: {sys.argv[0]} <命令参数...>")
        return 2

    command = " ".join(sys.argv[1:])
    body = urllib.parse.urlencode({"cmd": command})
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    try:
        conn = http.client.HTTPConnection(HOST, PORT, timeout=5)
        conn.request("POST", "/", body=body, headers=headers)
        response = conn.getresponse()
        data = response.read().decode("utf-8", errors="replace")
    except OSError as exc:
        print(f"转发失败: 无法连接到 http://{HOST}:{PORT} ({exc})")
        return 1
    finally:
        try:
            conn.close()
        except UnboundLocalError:
            pass

    print(data)
    return 0 if 200 <= response.status < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
