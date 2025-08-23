import requests
import json
import os
import re


def clean_proxy_line(line):
    """
    清理并格式化单行代理数据，只返回 "ip:端口" 或 "域名:端口" 的形式。
    """
    line = line.strip()
    # 先剥离协议头和认证信息
    if "//" in line:
        line = line.split('//')[-1]
    if "@" in line:
        line = line.split('@')[-1]

    # 再处理可能存在的国家等额外信息
    parts = line.split(':')
    if len(parts) > 2:
        line = f"{parts[0]}:{parts[1]}"

    if ':' in line and line.split(':')[0] and line.split(':')[1]:
        return line.strip()
    return None


# --- 核心优化：智能协议推断函数 ---
def deduce_protocol(original_line, default_protocol):
    """
    根据原始行内容推断协议。
    如果行内有明确标识，则使用标识的协议。
    否则，使用源定义的默认协议。
    """
    line_lower = original_line.lower()
    if 'socks5' in line_lower or 'socks' in line_lower:  # 包含 "socks" 但不包含 "socks5" 的也归为 SOCKS5
        return 'socks5'
    if 'socks4' in line_lower:
        return 'socks4'  # 也可以单独处理 SOCKS4
    if 'http' in line_lower:
        return 'http'
    # 如果行内没有明确标识，则返回该源的默认协议
    return default_protocol


# --- 代理源定义，'protocol' 在此作为后备默认值 ---
SOURCES = [
    {"name": "TheSpeedX/PROXY-List", "url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
     "parser": "text", "protocol": "socks5"},
    {"name": "hookzof/socks5_list", "url": "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
     "parser": "text", "protocol": "socks5"},
    {"name": "ProxyScraper/ProxyScraper",
     "url": "https://raw.githubusercontent.com/ProxyScraper/ProxyScraper/main/socks5.txt", "parser": "text",
     "protocol": "socks5"},
    {"name": "proxifly/free-proxy-list",
     "url": "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/http/data.txt",
     "parser": "text", "protocol": "http"},
    {"name": "zloi-user/hideip.me", "url": "https://raw.githubusercontent.com/zloi-user/hideip.me/master/socks5.txt",
     "parser": "text", "protocol": "socks5"},
    {"name": "gfpcom/free-proxy-list",
     "url": "https://raw.githubusercontent.com/gfpcom/free-proxy-list/main/list/socks5.txt", "parser": "text",
     "protocol": "socks5"},
    {"name": "monosans/proxy-list", "url": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies.json",
     "parser": "json-list", "protocol": "socks5"},
    {"name": "fate0/proxylist", "url": "https://raw.githubusercontent.com/fate0/proxylist/master/proxy.list",
     "parser": "json", "protocol": "http"},  # fate0/proxylist 大部分是http
    {"name": "proxik/http",
     "url": "https://proxik.cloud/proxy_list?type=http", "parser": "text",
     "protocol": "http"},
    {"name": "proxik/socks5",
         "url": "https://proxik.cloud/proxy_list?type=socks5", "parser": "text",
         "protocol": "socks5"},
    {"name": "proxik/socks4",
         "url": "https://proxik.cloud/proxy_list?type=socks4", "parser": "text",
         "protocol": "socks4"}
]


def save_proxies_to_file(proxies_set, filename, output_dir):
    """
    将代理集合保存到指定文件。
    """
    if not proxies_set:
        print(f"\n[-] 代理列表 '{filename}' 为空，无需保存。")
        return

    file_path = os.path.join(output_dir, filename)
    try:
        os.makedirs(output_dir, exist_ok=True)
        sorted_proxies = sorted(list(proxies_set))
        with open(file_path, 'w', encoding='utf-8') as f:
            for proxy in sorted_proxies:
                f.write(f"{proxy}\n")
        print(f"\n[SUCCESS] {len(sorted_proxies)} 个代理已成功保存到: {file_path}")

    except Exception as e:
        print(f"\n[ERROR] 保存文件 '{filename}' 时出错: {e}")


def fetch_and_save_proxies():
    """
    获取、清理、并智能分类合并所有来源的代理，然后分别保存到文件。
    """
    http_proxies = set()
    socks5_proxies = set()
    # 可以选择性地为SOCKS4创建集合
    # socks4_proxies = set()

    for source in SOURCES:
        print(f"[*] 正在从 {source['name']} 获取代理列表...")
        try:
            response = requests.get(source['url'], timeout=15)
            response.raise_for_status()

            initial_http_count = len(http_proxies)
            initial_socks5_count = len(socks5_proxies)

            content = response.text.strip()
            lines = content.split('\n')

            # --- 分类逻辑重构 ---
            for line in lines:
                if not line.strip():
                    continue

                # 1. 智能推断协议
                protocol = deduce_protocol(line, source['protocol'])

                # 2. 清理代理地址
                cleaned_proxy = None
                if source['parser'] in ['text', 'json-list']:  # json-list 的原始行就是 ip:port
                    cleaned_proxy = clean_proxy_line(line)
                elif source['parser'] == 'json':
                    try:
                        proxy_info = json.loads(line)
                        host = proxy_info.get("host")
                        port = proxy_info.get("port")
                        if host and port:
                            cleaned_proxy = f"{host}:{port}"
                    except json.JSONDecodeError:
                        continue  # 跳过无法解析的JSON行

                if not cleaned_proxy:
                    continue

                # 3. 根据推断出的协议进行分类和添加前缀
                if protocol == 'http':
                    http_proxies.add(f"http://{cleaned_proxy}")
                elif protocol == 'socks5':
                    socks5_proxies.add(f"socks5://{cleaned_proxy}")
                # elif protocol == 'socks4':
                #     socks4_proxies.add(f"socks4://{cleaned_proxy}")

            new_http = len(http_proxies) - initial_http_count
            new_socks5 = len(socks5_proxies) - initial_socks5_count
            print(f"[+] 从此来源添加了 {new_http} 个HTTP代理, {new_socks5} 个SOCKS5代理。")

        except requests.exceptions.RequestException as e:
            print(f"[!] 从 {source['name']} 获取代理时出错: {e}")

        print("-" * 20)
    
    # [!] 修改：将输出目录设置为当前脚本所在的目录
    output_dir = os.getcwd()
    save_proxies_to_file(http_proxies, "http.txt", output_dir)
    save_proxies_to_file(socks5_proxies, "git.txt", output_dir)
    # save_proxies_to_file(socks4_proxies, "socks4.txt", output_dir)


if __name__ == "__main__":

    fetch_and_save_proxies()
