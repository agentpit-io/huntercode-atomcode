# hunter-community · llm-shim 镜像
#
# 为什么要有这个镜像:原来 compose 里 llm-shim 用的是裸 `python:3.12-alpine`,
# 靠 `./scripts/llm-shim:/app:ro` 把源码挂进去。挂载依赖「宿主机上有这个仓库」,
# 云平台上没有仓库目录,容器起来就是一句 `can't open file '/app/shim.py'`。
# 把源码 COPY 进镜像之后,`docker compose up` 不再需要仓库文件。
#
# 构建上下文是**仓库根**(与 api / opencode 两个镜像一致),所以这里写
# `COPY scripts/llm-shim /app` 而不是 `COPY . /app`。
#
# 注意 shim 是纯标准库实现(见 scripts/llm-shim/shim.py),不装任何依赖 ——
# 所以没有 pip install 这一步,镜像就是基础镜像 + 几十 KB 源码。
FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY scripts/llm-shim /app

EXPOSE 3999

# 与改造前 docker-compose.yml 里那条 healthcheck 逐字相同 ——
# 搬进镜像是为了让「不写 compose 的平台」(K8s / 云平台表单)也能拿到健康检查。
HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=5 \
    CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:3999/health',timeout=3)"

CMD ["python3", "/app/shim.py"]
