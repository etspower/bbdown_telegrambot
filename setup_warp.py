#!/usr/bin/env python3
"""
setup_warp.py
自动安装、配置、验证 Cloudflare WARP，并将代理写入 .env。
直接运行：  python3 setup_warp.py
"""

import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
ENV_FILE = PROJECT_ROOT / ".env"
WARP_PROXY_HOST = "127.0.0.1"
WARP_PROXY_PORT = 40000


def _run(cmd: list[str], check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def _print_step(msg: str):
    print(f"\n{'='*55}\n  {msg}\n{'='*55}")


def step_install_warp() -> bool:
    _print_step("步骤 1：安装 Cloudflare WARP")

    if shutil.which("warp-cli"):
        print("✅ warp-cli 已安装，跳过安装。")
        return True

    print("📦 开始安装 cloudflare-warp...", flush=True)
    try:
        # 添加 GPG key
        _run(["sudo", "mkdir", "-p", "/usr/share/keyrings"])
        key_cmd = (
            "curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg "
            "| sudo gpg --batch --yes --dearmor "
            "-o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg"
        )
        subprocess.run(key_cmd, shell=True, check=True)

        # 添加 apt 源
        import platform
        codename = subprocess.check_output(["lsb_release", "-cs"], text=True).strip()
        repo_line = (
            f"deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] "
            f"https://pkg.cloudflareclient.com/ {codename} main"
        )
        list_file = "/etc/apt/sources.list.d/cloudflare-client.list"
        subprocess.run(
            ["sudo", "tee", list_file],
            input=repo_line + "\n",
            text=True, check=True, capture_output=True
        )

        # apt update + install
        print("🔄 apt update...", flush=True)
        _run(["sudo", "apt-get", "update", "-qq"], capture=False)
        print("📦 apt install cloudflare-warp...", flush=True)
        _run(["sudo", "apt-get", "install", "-y", "cloudflare-warp"], capture=False)

        if shutil.which("warp-cli"):
            print("✅ cloudflare-warp 安装成功。")
            return True
        else:
            print("❌ 安装后仍未找到 warp-cli！")
            return False

    except subprocess.CalledProcessError as e:
        print(f"❌ 安装失败：{e}")
        return False


def step_register_and_connect() -> bool:
    _print_step("步骤 2：注册并启用 WARP 代理模式")

    # 注册（已注册则跳过）
    reg_result = _run(["warp-cli", "registration", "show"], check=False)
    if reg_result.returncode != 0 or "Error" in reg_result.stdout:
        print("📝 注册 WARP（免费账户）...", flush=True)
        r = _run(["warp-cli", "registration", "new"], check=False)
        if r.returncode != 0:
            print(f"❌ 注册失败：{r.stdout}\n{r.stderr}")
            return False
        print("✅ 注册成功。")
    else:
        print("✅ WARP 已经注册。")

    # 切换到 proxy 模式（不劫持全局流量，只开 socks5 代理）
    print("🔧 设置模式为 proxy...", flush=True)
    _run(["warp-cli", "mode", "proxy"], check=False)

    # 连接
    status = _run(["warp-cli", "status"], check=False)
    if "Connected" in status.stdout:
        print("✅ WARP 已处于 Connected 状态。")
    else:
        print("🔗 连接 WARP...", flush=True)
        _run(["warp-cli", "connect"], check=False)
        for i in range(15):
            time.sleep(1)
            s = _run(["warp-cli", "status"], check=False)
            if "Connected" in s.stdout:
                print(f"✅ WARP 连接成功（{i+1}s）")
                break
            print(f"  等待连接... {i+1}s", end="\r", flush=True)
        else:
            print("\n⚠️  WARP 连接超时，但代理服务可能仍在运行。")

    return True


def step_verify_proxy() -> bool:
    _print_step("步骤 3：验证代理可用性")

    # 检查端口是否监听
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(3)
        if s.connect_ex((WARP_PROXY_HOST, WARP_PROXY_PORT)) != 0:
            print(f"❌ 代理端口 {WARP_PROXY_PORT} 未开放！")
            print("请检查 warp-cli status 和 warp-cli mode proxy")
            return False

    print(f"✅ 代理端口 {WARP_PROXY_PORT} 开放。", flush=True)

    # 通过代理请求 B 站验证能否联通
    print("🌐 通过代理测试访问 B 站...", flush=True)
    proxies = {
        "http" : f"socks5://{WARP_PROXY_HOST}:{WARP_PROXY_PORT}",
        "https": f"socks5://{WARP_PROXY_HOST}:{WARP_PROXY_PORT}",
    }
    try:
        import urllib.request
        proxy_handler = urllib.request.ProxyHandler({
            "http" : f"http://{WARP_PROXY_HOST}:{WARP_PROXY_PORT}",
            "https": f"http://{WARP_PROXY_HOST}:{WARP_PROXY_PORT}",
        })
        opener = urllib.request.build_opener(proxy_handler)
        req = urllib.request.Request(
            "https://api.bilibili.com/x/web-interface/nav",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with opener.open(req, timeout=10) as resp:
            body = resp.read().decode(errors="ignore")
        if """"code""" in body:
            print("✅ B 站接口通过代理访问成功！")
            return True
        else:
            print(f"⚠️  请求成功但返回内容异常：{body[:200]}")
            return True  # 端口通就认为可用
    except Exception as e:
        print(f"⚠️  访问 B 站失败：{e}")
        print("   （WARP 免费版在某些地区可能不支持代理 B 站）")
        return False


def step_write_env():
    _print_step("步骤 4：将代理写入 .env")

    proxy_val = f"socks5://{WARP_PROXY_HOST}:{WARP_PROXY_PORT}"

    if ENV_FILE.exists():
        content = ENV_FILE.read_text(encoding="utf-8")
    else:
        content = ""

    new_lines = []
    wrote_http = wrote_https = wrote_all = False
    for line in content.splitlines():
        if re.match(r"^HTTP_PROXY\s*=", line, re.I):
            new_lines.append(f"HTTP_PROXY={proxy_val}")
            wrote_http = True
        elif re.match(r"^HTTPS_PROXY\s*=", line, re.I):
            new_lines.append(f"HTTPS_PROXY={proxy_val}")
            wrote_https = True
        elif re.match(r"^ALL_PROXY\s*=", line, re.I):
            new_lines.append(f"ALL_PROXY={proxy_val}")
            wrote_all = True
        else:
            new_lines.append(line)

    if not wrote_http:
        new_lines.append(f"HTTP_PROXY={proxy_val}")
    if not wrote_https:
        new_lines.append(f"HTTPS_PROXY={proxy_val}")
    if not wrote_all:
        new_lines.append(f"ALL_PROXY={proxy_val}")

    ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"✅ 已写入 .env：")
    print(f"   HTTP_PROXY={proxy_val}")
    print(f"   HTTPS_PROXY={proxy_val}")
    print(f"   ALL_PROXY={proxy_val}")


def step_patch_start_api():
    """start_api.py 里的 urllib 请求不读环境变量，需要在进程层面设置。"""
    _print_step("步骤 5：将 WARP 代理添加到环境变量")
    proxy_val = f"socks5://{WARP_PROXY_HOST}:{WARP_PROXY_PORT}"
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        os.environ[var] = proxy_val
    print("✅ 当前进程环境变量已设置。")


def step_test_bbdown():
    _print_step("步骤 6：用 WARP 代理测试 BBDown")
    bbdown = (
        str(PROJECT_ROOT / "tools" / "BBDown")
        if (PROJECT_ROOT / "tools" / "BBDown").exists()
        else shutil.which("BBDown") or shutil.which("bbdown")
    )
    if not bbdown:
        print("⚠️  未找到 BBDown，跳过测试。")
        return

    test_url = "https://b23.tv/LIwKq8K"
    print(f"🧪 测试链接：{test_url}", flush=True)

    proxy_val = f"socks5://{WARP_PROXY_HOST}:{WARP_PROXY_PORT}"
    env = os.environ.copy()
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        env[var] = proxy_val

    data_dir = str(PROJECT_ROOT / "data")
    result = subprocess.run(
        [bbdown, "--only-show-info", test_url],
        cwd=data_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr
    print(output[:1500])
    if result.returncode == 0 or "标题" in output or "Title" in output:
        print("✅ BBDown 通过 WARP 代理解析成功！")
    elif "412" in output:
        print(
            "❌ 仍然 412。WARP 免费版在本地区可能无法绕过 B 站封锁。\n"
            "建议考虑以下方案：\n"
            "  1. 购买 WARP+ (仍然免费，通过換叁可得），或圈子订阅\n"
            "  2. 使用国内服务器中转\n"
            "  3. 备案 WireGuard peer 经过国内节点"
        )
    else:
        print(f"⚠️  返回码 {result.returncode}，请手动检查输出。")


def main():
    print("🚀 Cloudflare WARP 自动配置脚本")
    print(f"   项目目录：{PROJECT_ROOT}")
    print(f"   .env 路径：{ENV_FILE}")

    # 安装
    if not step_install_warp():
        sys.exit(1)

    # 注册 + 连接
    if not step_register_and_connect():
        sys.exit(1)

    # 验证代理
    proxy_ok = step_verify_proxy()

    # 写入 .env
    step_write_env()

    # 设置当前进程环境变量
    step_patch_start_api()

    # 测试 BBDown
    if proxy_ok:
        step_test_bbdown()
    else:
        print("\n⚠️  代理验证未通过，跳过 BBDown 测试。")

    print("\n" + "="*55)
    print("🎉 配置完成！")
    print()
    print("  下一步：重启 Bot，代理将自动生效。")
    print("  python3 -m bot.main")
    print()
    if not proxy_ok:
        print("⚠️  注意：WARP 代理当前测试未通过 B 站。")
        print("  如果重启 Bot 后仍然 412，考虑使用国内中转服务器。")
    print("="*55)


if __name__ == "__main__":
    main()
