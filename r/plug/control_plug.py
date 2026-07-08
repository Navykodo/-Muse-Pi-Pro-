import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from miio import Device

IP_ENV = "MIIO_PLUG_IP"
TOKEN_ENV = "MIIO_PLUG_TOKEN"

host = os.environ.get("PLUG_CONTROL_HOST", "127.0.0.1")
port = int(os.environ.get("PLUG_CONTROL_PORT", "2876"))
commands = {"on", "off", "status"}

device = None


def get_device():
    global device
    if device is None:
        ip = os.environ.get(IP_ENV)
        token = os.environ.get(TOKEN_ENV)
        if not ip or not token:
            raise RuntimeError(
                f"missing plug credentials; set {IP_ENV} and {TOKEN_ENV} in the environment"
            )
        device = Device(ip, token)
    return device


def turn_on():
    result = get_device().raw_command("set_properties", [{"siid": 2, "piid": 1, "value": True}])
    return f"开启结果: {result}"


def turn_off():
    result = get_device().raw_command("set_properties", [{"siid": 2, "piid": 1, "value": False}])
    return f"关闭结果: {result}"


def get_status():
    result = get_device().raw_command("get_properties", [{"siid": 2, "piid": 1}])
    status = "开启" if result[0]["value"] else "关闭"
    return f"插座状态: {status}"


def handle_command(command):
    command = command.strip().lower()
    if command == "on":
        return turn_on()
    if command == "off":
        return turn_off()
    if command == "status":
        return get_status()
    raise ValueError(f"不支持的参数: {command}")


class PlugControlHandler(BaseHTTPRequestHandler):
    server_version = "PlugControl/1.0"

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def handle_request(self):
        command = self.extract_command()
        if command not in commands:
            self.write_json(
                400,
                {
                    "ok": False,
                    "error": "参数必须是 on、off 或 status",
                    "usage": [
                        f"http://{host}:{port}/on",
                        f"http://{host}:{port}/off",
                        f"http://{host}:{port}/status",
                        f"http://{host}:{port}/?cmd=status",
                    ],
                },
            )
            return

        try:
            message = handle_command(command)
        except Exception as exc:
            self.write_json(500, {"ok": False, "command": command, "error": str(exc)})
            return

        self.write_json(200, {"ok": True, "command": command, "message": message})

    def extract_command(self):
        parsed = urlparse(self.path)
        path_command = parsed.path.strip("/").split("/", 1)[0].lower()
        if path_command in commands:
            return path_command

        query = parse_qs(parsed.query)
        for name in ("cmd", "action", "command"):
            value = query.get(name, [""])[0].strip().lower()
            if value:
                return value

        if self.command == "POST":
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(min(length, 4096)).decode("utf-8").strip()
            if body:
                body_params = parse_qs(body)
                for name in ("cmd", "action", "command"):
                    value = body_params.get(name, [""])[0].strip().lower()
                    if value:
                        return value
                return body.split()[0].lower()

        return ""

    def write_json(self, status_code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_server():
    server = ThreadingHTTPServer((host, port), PlugControlHandler)
    print(f"正在监听 http://{host}:{port}")
    print(f"可用参数: {', '.join(sorted(commands))}")
    print(f"插座配置来自环境变量: {IP_ENV}, {TOKEN_ENV}")
    server.serve_forever()


def print_usage():
    print("用法:")
    print("  python control_plug.py              # 持续监听本地端口 2876")
    print("  python control_plug.py serve        # 持续监听本地端口 2876")
    print("  python control_plug.py [on|off|status]")
    print(f"环境变量: {IP_ENV}=<plug-ip> {TOKEN_ENV}=<plug-token>")


if __name__ == "__main__":
    if len(sys.argv) == 1 or sys.argv[1] in {"serve", "server", "listen"}:
        start_server()
    elif sys.argv[1] in commands:
        print(handle_command(sys.argv[1]))
    else:
        print_usage()
        sys.exit(2)
