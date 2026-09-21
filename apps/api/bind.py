#!/usr/bin/env python3
"""按 HUNTER_BIND_HOST 建好监听 socket，再把它交给 uvicorn。

为什么不直接 `uvicorn --host`：
    `uvicorn --host ::` 起出来的是**只收 IPv6** 的 socket —— asyncio 在
    `create_server` 里对 AF_INET6 显式 `setsockopt(IPV6_V6ONLY, True)`，
    内核的 `net.ipv6.bindv6only=0` 在这里不起作用。
    实测（M4，Railway 等价栈）：api 绑 `::` 之后，同一条 docker 网络上的
    web / opencode 走 IPv4 连过来全是 `Connection refused`，容器自己的
    healthcheck 打 127.0.0.1 也连不上 —— 而 `[::1]` 是 200。
    这种「日志一切正常、就是连不上」的坏法最难查，所以这里自己建 socket，
    把 IPV6_V6ONLY 关掉，一个 socket 同时收 v4 与 v6，再用 `uvicorn --fd` 交出去。

    另外三家不需要这层：llm-shim 是裸 http.server（自己关了 V6ONLY），
    opencode 与 web 是 Bun / Node（默认就是双栈）。

默认 HUNTER_BIND_HOST=0.0.0.0 时这里退化成普通的 IPv4 监听，行为与以前完全一致。
"""
import os
import socket
import sys


def main() -> None:
    host = (os.environ.get("HUNTER_BIND_HOST") or "0.0.0.0").strip()
    port = int(os.environ.get("HUNTER_BIND_PORT") or 8000)

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6:
        try:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except OSError as e:          # 内核不让改就只收 IPv6，总比起不来强
            print(f"[boot] ⚠ 关不掉 IPV6_V6ONLY({e})，这个 socket 只收 IPv6", flush=True)
    sock.bind((host, port))
    sock.listen(2048)
    os.set_inheritable(sock.fileno(), True)   # 不设的话 exec 之后 fd 就没了
    print(f"[boot] 监听 {host}:{port}（family={family.name}，fd={sock.fileno()}）", flush=True)

    os.execvp("uvicorn", ["uvicorn", "main:app", "--fd", str(sock.fileno())] + sys.argv[1:])


if __name__ == "__main__":
    main()
