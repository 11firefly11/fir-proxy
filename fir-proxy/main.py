# proxy_pool/main.py

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, TclError
from tkinter import filedialog
import ttkbootstrap as bs
import queue
import threading
from datetime import datetime
import re
import json
import os

# 导入核心模块
from modules.fetcher import ProxyFetcher
from modules.checker import ProxyChecker 
from modules.rotator import ProxyRotator
from modules.server import ProxyServer 

class ProxyPoolApp:
    """
    高可用代理池 1.0 by firefly
    - 交互优化与bug修复
    - 启动时自动校验内置代理
    """
    def __init__(self, root):
        self.root = root
        self.root.title("高可用代理池 1.0 版本 by firefly")
        self.root.geometry("1200x850")
        self.root.minsize(1100, 700)

        # 线程与状态
        self.result_queue = queue.Queue()
        self.log_queue = queue.Queue()
        self.is_running_task = False

        # 核心模块
        self.fetcher = ProxyFetcher()
        self.checker = ProxyChecker() 
        self.rotator = ProxyRotator()
        self.displayed_proxies = set()
        self.proxy_to_tree_item_map = {}
        
        # 代理服务
        self.proxy_server = ProxyServer(
            http_host='127.0.0.1', http_port=1801,
            socks5_host='127.0.0.1', socks5_port=1800,
            rotator=self.rotator, log_queue=self.log_queue
        )
        self.is_server_running = False

        # 自动轮换
        self.is_auto_rotating = False
        self.auto_rotate_job_id = None
        self.use_high_quality_var = tk.BooleanVar(value=False)

        # UI
        self._create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        
        # 启动后台任务
        threading.Thread(target=self.checker.initialize_public_ip, args=(self.log_queue,), daemon=True).start()
        threading.Thread(target=self._run_builtin_check, daemon=True).start()
        self.process_log_queue()

    def _create_widgets(self):
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.rowconfigure(2, weight=1)
        main_frame.columnconfigure(0, weight=1)

        # --- 控制面板 ---
        top_frame = ttk.Frame(main_frame)
        top_frame.grid(row=0, column=0, sticky='ew', pady=(0, 10))
        
        self.fetch_button = ttk.Button(top_frame, text="获取在线代理", command=self.start_fetch_validate_thread, style='success.TButton', width=15)
        self.fetch_button.pack(side=tk.LEFT, padx=(0, 10))

        self.import_button = ttk.Button(top_frame, text="导入代理", command=self.import_and_validate_proxies, style='primary.TButton', width=12)
        self.import_button.pack(side=tk.LEFT, padx=(0, 10))
        
        self.clear_button = ttk.Button(top_frame, text="清空列表", command=self.clear_all_proxies, style='danger.TButton', width=12)
        self.clear_button.pack(side=tk.LEFT, padx=(0, 10))

        self.test_all_button = ttk.Button(top_frame, text="全部测试", command=self.start_revalidate_thread, state=tk.DISABLED, style='warning.TButton', width=12)
        self.test_all_button.pack(side=tk.LEFT, padx=(0, 10))
        
        self.export_button = ttk.Button(top_frame, text="导出代理", command=self.export_proxies, state=tk.DISABLED, style='primary.TButton', width=12)
        self.export_button.pack(side=tk.LEFT, padx=(0, 10))

        region_panel = ttk.Labelframe(top_frame, text="区域轮换与筛选")
        region_panel.pack(side=tk.LEFT, padx=5, fill=tk.Y)
        
        self.region_combobox = ttk.Combobox(region_panel, state="readonly", width=18)
        self.region_combobox.pack(side=tk.LEFT, padx=5, pady=5)
        self.region_combobox.bind('<<ComboboxSelected>>', self._refresh_treeview)
        self.region_combobox.set("全部地区") 

        self.quality_checkbutton = ttk.Checkbutton(region_panel, text="优质(<2s)", variable=self.use_high_quality_var, command=self._refresh_treeview)
        self.quality_checkbutton.pack(side=tk.LEFT, padx=5, pady=5)
        
        self.rotate_button = ttk.Button(region_panel, text="轮换IP", command=self.rotate_proxy, state=tk.DISABLED, width=8)
        self.rotate_button.pack(side=tk.LEFT, padx=5, pady=5)
        
        self.auto_rotate_button = ttk.Button(region_panel, text="自动", command=self.toggle_auto_rotate, state=tk.DISABLED, style='info.TButton', width=6)
        self.auto_rotate_button.pack(side=tk.LEFT, padx=5, pady=5)

        self.interval_spinbox = ttk.Spinbox(region_panel, from_=1, to=300, width=4)
        self.interval_spinbox.set("10")
        self.interval_spinbox.pack(side=tk.LEFT, padx=(0, 5), pady=5)
        ttk.Label(region_panel, text="秒").pack(side=tk.LEFT, padx=(0,5), pady=5)

        server_panel = ttk.Labelframe(top_frame, text="代理服务 (SOCKS5:1800 / HTTP:1801)")
        server_panel.pack(side=tk.LEFT, padx=5, fill=tk.Y)

        self.server_button = ttk.Button(server_panel, text="启动服务", command=self.toggle_server, state=tk.DISABLED, style='info.TButton', width=12)
        self.server_button.pack(side=tk.LEFT, padx=5, pady=5)
        
        self.current_proxy_var = tk.StringVar(value="当前使用: N/A")
        proxy_entry = ttk.Entry(top_frame, textvariable=self.current_proxy_var, state='readonly', width=30)
        proxy_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)
        
        self.progress_bar = ttk.Progressbar(main_frame, mode='determinate', style='success.Striped.TProgressbar')
        self.progress_bar.grid(row=1, column=0, sticky='ew', pady=5)
        paned_window = ttk.PanedWindow(main_frame, orient=tk.VERTICAL)
        paned_window.grid(row=2, column=0, sticky='nsew')
        list_frame = ttk.Labelframe(paned_window, text="可用代理列表 (右键操作)", padding=10)
        paned_window.add(list_frame, weight=3)
        
        columns = ('score', 'anonymity', 'protocol', 'proxy', 'delay', 'speed', 'region')
        self.tree = ttk.Treeview(list_frame, columns=columns, show='headings', height=20)
        
        self.tree.heading('score', text='分数', command=lambda: self.sort_treeview_column('score', True))
        self.tree.heading('anonymity', text='匿名度', command=lambda: self.sort_treeview_column('anonymity', False))
        self.tree.heading('protocol', text='协议', command=lambda: self.sort_treeview_column('protocol', False))
        self.tree.heading('proxy', text='代理地址')
        self.tree.heading('delay', text='延迟(ms)', command=lambda: self.sort_treeview_column('delay', False))
        self.tree.heading('speed', text='速度(Mbps)', command=lambda: self.sort_treeview_column('speed', True))
        self.tree.heading('region', text='地区')
        
        self.tree.column('score', width=70, anchor='center'); self.tree.column('anonymity', width=80, anchor='center')
        self.tree.column('protocol', width=60, anchor='center'); self.tree.column('proxy', width=180)
        self.tree.column('delay', width=80, anchor='center'); self.tree.column('speed', width=90, anchor='center')
        self.tree.column('region', width=200)
        
        self.tree.bind("<Double-1>", self.copy_to_clipboard)
        self.tree.bind("<Button-3>", self._show_context_menu)

        tree_scroll_y = ttk.Scrollbar(list_frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll_y.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        
        log_frame = ttk.Labelframe(paned_window, text="实时日志", padding=10)
        paned_window.add(log_frame, weight=1)
        self.log_frame = log_frame
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, state='disabled', bg='#2a2a2a', fg='#cccccc')
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _run_builtin_check(self):
        """在后台线程中校验内置代理。"""
        proxy_str = '222.66.69.78:23344'
        self.log_queue.put(f"正在校验内置代理: http://{proxy_str}")
        builtin_proxy_info = {'proxy': proxy_str, 'protocol': 'http'}
        
        if not self.checker._pre_check_proxy(builtin_proxy_info['proxy']):
            self.log_queue.put(f"内置代理 {proxy_str} TCP 连接失败。")
            return
            
        result = self.checker._full_check_proxy(builtin_proxy_info, 'online')
        if self.root.winfo_exists():
            self.root.after(0, self._process_builtin_result, result)

    def _process_builtin_result(self, result_dict):
        """在UI线程中处理内置代理的校验结果。"""
        if result_dict.get('status') == 'Working':
            proxy_address = result_dict['proxy']
            if proxy_address in self.displayed_proxies:
                return 
            self.displayed_proxies.add(proxy_address)
            
            is_first_proxy = self.rotator.get_working_proxies_count() == 0
            
            latency, speed, anonymity = result_dict['latency'], result_dict['speed'], result_dict['anonymity']
            score = 0
            if latency != float('inf'): score += (1 / latency) * 50
            score += speed * 10
            if anonymity == 'Elite': score += 50
            elif anonymity == 'Anonymous': score += 20
            result_dict['score'] = score
            
            self.rotator.add_proxy(result_dict)
            
            display_values = (
                f"{score:.1f}", anonymity, result_dict['protocol'], proxy_address,
                f"{latency * 1000:.1f}", f"{speed:.2f}", result_dict['location']
            )
            self.tree.insert('', 0, values=display_values)
            self.sort_treeview_column('score', True)

            self.log(f"内置代理可用: {proxy_address} | 分数: {score:.1f}")
            
            if is_first_proxy:
                self.log("首个可用代理已发现！功能已激活。")

            self._update_regions_and_counts(premium_only=self.use_high_quality_var.get())
            working = self.rotator.get_working_proxies_count()
            self.log_frame.config(text=f"实时日志 | 可用: {working}")
        else:
            self.log(f"内置代理 {result_dict['proxy']} 验证失败。")


    def _refresh_treeview(self, event=None):
        """根据筛选条件刷新代理列表和地区计数。"""
        is_high_quality_mode = self.use_high_quality_var.get()
        
        self._update_regions_and_counts(premium_only=is_high_quality_mode)
        
        selected_item = self.region_combobox.get()
        region_key = "全部地区"
        if selected_item and selected_item != "全部地区":
            match = re.match(r"(.+?)\s*\(\d+\)", selected_item)
            if match:
                region_key = match.group(1).strip()
        
        all_proxies = sorted(self.rotator.all_proxies, key=lambda p: p.get('score', 0), reverse=True)

        proxies_to_display = []
        for p_info in all_proxies:
            region_match = (region_key == "全部地区" or p_info.get('location') == region_key)
            if not region_match:
                continue
            
            if is_high_quality_mode and p_info.get('latency', float('inf')) > 2.0:
                continue
            
            proxies_to_display.append(p_info)
            
        self.tree.delete(*self.tree.get_children())
        for p_info in proxies_to_display:
            score = p_info.get('score', 0)
            display_values = (
                f"{score:.1f}", p_info.get('anonymity', 'N/A'), p_info.get('protocol', 'N/A'), p_info.get('proxy', 'N/A'),
                f"{p_info.get('latency', float('inf')) * 1000:.1f}", f"{p_info.get('speed', 0):.2f}", p_info.get('location', 'N/A')
            )
            self.tree.insert('', 'end', values=display_values)
        
        if event:
            quality_str = " + 优质(<2s)" if is_high_quality_mode else ""
            self.log(f"列表已更新，显示 [{region_key}{quality_str}] 代理。")


    def process_result_queue(self):
        try:
            result_dict = self.result_queue.get_nowait()
            if result_dict is None:
                self.finalize_validation()
                return
            
            self.progress_bar['value'] += 1

            if result_dict.get('status') == 'Working':
                proxy_address = result_dict['proxy']
                if proxy_address in self.displayed_proxies:
                    return 

                self.displayed_proxies.add(proxy_address)
                is_first_proxy = self.rotator.get_working_proxies_count() == 0
                
                latency, speed, anonymity = result_dict['latency'], result_dict['speed'], result_dict['anonymity']
                score = 0
                if latency != float('inf'): score += (1 / latency) * 50
                score += speed * 10
                if anonymity == 'Elite': score += 50
                elif anonymity == 'Anonymous': score += 20
                result_dict['score'] = score
                
                self.rotator.add_proxy(result_dict)
                
                selected_item = self.region_combobox.get()
                region_key = "全部地区"
                if selected_item and selected_item != "全部地区":
                    match = re.match(r"(.+?)\s*\(\d+\)", selected_item)
                    if match: region_key = match.group(1).strip()
                
                is_high_quality_mode = self.use_high_quality_var.get()
                
                region_match = (region_key == "全部地区" or result_dict.get('location') == region_key)
                quality_match = (not is_high_quality_mode or latency <= 2.0)

                if region_match and quality_match:
                    display_values = (
                        f"{score:.1f}", anonymity, result_dict['protocol'], proxy_address,
                        f"{latency * 1000:.1f}", f"{speed:.2f}", result_dict['location']
                    )
                    self.tree.insert('', 0, values=display_values)
                    self.sort_treeview_column('score', True)

                self.log(f"成功: {proxy_address} | 分数: {score:.1f} | 延迟: {latency*1000:.1f}ms")
                
                if is_first_proxy:
                    self.log("首个可用代理已发现！功能已激活。")
                    self._update_regions_and_counts(premium_only=is_high_quality_mode)
            
            working = self.rotator.get_working_proxies_count()
            current_progress = int(self.progress_bar['value'])
            max_progress = int(self.progress_bar['maximum'])
            if max_progress > 0:
                self.log_frame.config(text=f"实时日志 | 进度: {current_progress}/{max_progress} | 可用: {working}")
            else:
                self.log_frame.config(text=f"实时日志 | 可用: {working}")

        except queue.Empty: pass
        if self.is_running_task: self.root.after(10, self.process_result_queue)


    def _update_regions_and_counts(self, premium_only=False):
        """更新地区列表和计数,并控制按钮状态。"""
        working_count = self.rotator.get_working_proxies_count()
        
        if not self.is_running_task:
            try:
                self.log_frame.config(text=f"实时日志 | 可用: {working_count}")
            except AttributeError:
                pass

        regions_with_counts = self.rotator.get_available_regions_with_counts(premium_only=premium_only)
        current_selection = self.region_combobox.get()
        
        if regions_with_counts:
            sorted_regions = sorted(regions_with_counts.items(), key=lambda item: item[1], reverse=True)
            formatted_regions = [f"{region} ({count})" for region, count in sorted_regions]
            
            new_values = ["全部地区"] + formatted_regions
            
            current_region_key = None
            if current_selection and current_selection != "全部地区":
                match = re.match(r"(.+?)\s*\(\d+\)", current_selection)
                if match:
                    current_region_key = match.group(1).strip()

            self.region_combobox['values'] = new_values
            
            new_selection_found = False
            if current_region_key:
                for item in new_values:
                    if item.startswith(current_region_key):
                        self.region_combobox.set(item)
                        new_selection_found = True
                        break
            
            if not new_selection_found:
                self.region_combobox.set("全部地区")

        else:
            self.region_combobox['values'] = ["全部地区"]
            self.region_combobox.set("全部地区")

        if working_count > 0:
            self.export_button.config(state=tk.NORMAL)
            self.server_button.config(state=tk.NORMAL)
            self.rotate_button.config(state=tk.NORMAL)
            self.auto_rotate_button.config(state=tk.NORMAL)
            self.test_all_button.config(state=tk.NORMAL)
        else:
            self.export_button.config(state=tk.DISABLED)
            self.server_button.config(state=tk.DISABLED)
            self.rotate_button.config(state=tk.DISABLED)
            self.auto_rotate_button.config(state=tk.DISABLED)
            self.test_all_button.config(state=tk.DISABLED)
            self.current_proxy_var.set("当前使用: N/A")
            if self.is_server_running: self.toggle_server()
            if self.is_auto_rotating: self.toggle_auto_rotate()


    def finalize_validation(self):
        self.is_running_task = False
        self.fetch_button.config(state=tk.NORMAL, text="获取在线代理")
        self.import_button.config(state=tk.NORMAL)
        self.clear_button.config(state=tk.NORMAL)
        
        self._refresh_treeview() 
        
        final_count = self.rotator.get_working_proxies_count()
        self.log_frame.config(text=f"实时日志 | 可用: {final_count}")
        self.log(f"\n{'='*20} 任务全部完成 {'='*20}\n代理池中现有 {final_count} 个可用的代理。")

    def finalize_revalidation(self):
        self.is_running_task = False
        self.fetch_button.config(state=tk.NORMAL, text="获取在线代理")
        self.import_button.config(state=tk.NORMAL)
        self.clear_button.config(state=tk.NORMAL)
        self.test_all_button.config(text="全部测试")

        self._refresh_treeview()
        self.sort_treeview_column('score', True)

        final_count = self.rotator.get_working_proxies_count()
        self.log_frame.config(text=f"实时日志 | 可用: {final_count}")
        self.log(f"\n{'='*20} 全部测试完成 {'='*20}\n代理池中现有 {final_count} 个可用的代理。")
        self.proxy_to_tree_item_map.clear()
        
    def _delete_selected_proxy(self):
        selected_items = self.tree.selection()
        if not selected_items:
            return
        
        item_id = selected_items[0]
        proxy_address = self.tree.item(item_id, 'values')[3]
        
        if self.rotator.remove_proxy(proxy_address):
            if proxy_address in self.displayed_proxies:
                self.displayed_proxies.remove(proxy_address)
            
            self.log(f"已手动删除代理: {proxy_address}")
            self._refresh_treeview()
        else:
            self.log(f"错误: 尝试删除的代理 {proxy_address} 在后端未找到。")

    def rotate_proxy(self):
        """根据UI选项轮换代理。"""
        selected_item = self.region_combobox.get()
        region_key = "All"
        if selected_item and selected_item != "全部地区":
            match = re.match(r"(.+?)\s*\(\d+\)", selected_item)
            if match:
                region_key = match.group(1).strip()
    
        is_high_quality_mode = self.use_high_quality_var.get()
        
        proxy_info = self.rotator.get_next_proxy(region=region_key, premium_only=is_high_quality_mode)
        
        mode_str = "优质" if is_high_quality_mode else "常规"
        
        if proxy_info:
            self.current_proxy_var.set(f"当前使用: {proxy_info['proxy']}")
            self.log(f"已轮换代理 ({region_key} | {mode_str}模式): {proxy_info['protocol'].lower()}://{proxy_info['proxy']}")
        else:
            self.current_proxy_var.set("当前使用: N/A")
            self.log(f"[{region_key}] 区域内无可用({mode_str}模式)代理。")


    def log(self, message):
        if not self.root.winfo_exists(): return
        self.log_text.config(state='normal')
        self.log_text.insert(tk.END, f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')

    def clear_all_proxies(self):
        if self.is_running_task:
            messagebox.showwarning("操作无效", "请等待当前任务完成后再清空列表。")
            return
        if messagebox.askyesno("确认操作", "您确定要清空所有已发现的代理吗？此操作不可逆。"):
            self.log("正在清空所有代理...")
            self.rotator.clear()
            self.displayed_proxies.clear()
            self.log("所有代理已清空。")
            self._refresh_treeview()

    def _reset_ui_for_task(self, task_name="正在运行..."):
        if self.is_running_task: return True
        self.is_running_task = True
        self.fetch_button.config(state=tk.DISABLED, text=task_name)
        self.import_button.config(state=tk.DISABLED)
        self.clear_button.config(state=tk.DISABLED)
        self.test_all_button.config(state=tk.DISABLED)
        self.export_button.config(state=tk.DISABLED)
        self.progress_bar['value'] = 0
        return False

    def start_fetch_validate_thread(self):
        if self._reset_ui_for_task("正在获取..."): return
        threading.Thread(target=self.fetch_and_validate, daemon=True).start()
        self.process_result_queue()

    def import_and_validate_proxies(self):
        file_path = filedialog.askopenfilename(
            title="导入代理(TXT/JSON)",
            filetypes=[("Text and JSON files", "*.txt *.json"), ("All files", "*.*")]
        )
        if not file_path: return
        proxies_by_protocol = {'http': [], 'socks4': [], 'socks5': []}
        valid_parse_protocols = {'http', 'https', 'socks4', 'socks5'}
        try:
            _, ext = os.path.splitext(file_path)
            if ext.lower() == '.json':
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        for item in data:
                            url, protocol = item.get('url'), item.get('protocol', 'http').lower()
                            if url:
                                parsed = re.match(r'(\w+)://(.+)', url)
                                if parsed: protocol, proxy = parsed.groups()
                                else: proxy = url
                            else: proxy = f"{item.get('ip')}:{item.get('port')}"
                            if protocol == 'https': protocol = 'http'
                            if protocol in proxies_by_protocol: proxies_by_protocol[protocol].append(proxy)
            else:
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#'): continue
                        protocol, proxy_address = 'http', line
                        match = re.match(r'(\w+)://(.+)', line)
                        if match:
                            proto_part, proxy_part = match.groups()
                            if proto_part.lower() in valid_parse_protocols:
                                proxy_address, protocol = proxy_part, 'http' if proto_part.lower() == 'https' else proto_part.lower()
                        elif ',' in line:
                            parts = [p.strip().lower() for p in line.split(',', 1)]
                            if len(parts) == 2 and parts[0] in valid_parse_protocols:
                                proxy_address, protocol = parts[1], 'http' if parts[0] == 'https' else parts[0]
                        if protocol in proxies_by_protocol and re.match(r'^\d{1,3}(?:\.\d{1,3}){3}:\d+$', proxy_address):
                             proxies_by_protocol[protocol].append(proxy_address)
                        else: self.log(f"已跳过无效格式行: {line}")
            total_imported = sum(len(v) for v in proxies_by_protocol.values())
            if total_imported == 0:
                messagebox.showwarning("无内容", "文件中未找到有效格式的代理。")
                self.fetch_button.config(state=tk.NORMAL)
                return
            self.log(f"成功从文件导入 {total_imported} 个代理，准备验证...")
            if self._reset_ui_for_task("正在验证..."): return
            threading.Thread(target=self.run_validation_task, args=(proxies_by_protocol, 'import'), daemon=True).start()
            self.process_result_queue()
        except Exception as e:
            messagebox.showerror("导入错误", f"读取或解析文件时出错: {e}")
            self.log(f"导入代理失败: {e}")
            self.finalize_validation()

    def fetch_and_validate(self):
        self.log_queue.put("="*20 + " 步骤 1: 开始获取在线代理 " + "="*20)
        proxies_by_protocol = self.fetcher.fetch_all(self.log_queue)
        self.run_validation_task(proxies_by_protocol, validation_mode='online')

    def run_validation_task(self, proxies_by_protocol, validation_mode='online'):
        total_to_validate = sum(len(v) for v in proxies_by_protocol.values())
        if self.root.winfo_exists(): self.root.after(0, self.progress_bar.config, {'maximum': total_to_validate})
        if total_to_validate > 0:
            self.checker.validate_all(proxies_by_protocol, self.result_queue, self.log_queue, validation_mode)
        else:
            self.result_queue.put(None)

    def process_log_queue(self):
        try:
            while True: self.log(self.log_queue.get_nowait())
        except queue.Empty: pass
        if self.root.winfo_exists(): self.root.after(100, self.process_log_queue)

    def start_revalidate_thread(self):
        if self._reset_ui_for_task("测试中..."): return
        self.test_all_button.config(text="测试中...")
        self.proxy_to_tree_item_map = {self.tree.item(iid, 'values')[3]: iid for iid in self.tree.get_children('')}
        threading.Thread(target=self.revalidate_all, daemon=True).start()
        self.process_revalidate_queue()

    def revalidate_all(self):
        self.log_queue.put("="*20 + " 开始重新验证所有代理 " + "="*20)
        all_current_proxies_info = self.rotator.all_proxies[:]
        if not all_current_proxies_info:
            self.log_queue.put("代理池为空，无需测试。")
            self.result_queue.put(None)
            return
        
        from collections import defaultdict
        proxies_by_protocol = defaultdict(list)
        for p_info in all_current_proxies_info:
            protocol = p_info.get('protocol', 'http').lower()
            proxy = p_info.get('proxy')
            if proxy:
                proxies_by_protocol[protocol].append(proxy)
        self.run_validation_task(proxies_by_protocol, 'online')

    def process_revalidate_queue(self):
        try:
            result_dict = self.result_queue.get_nowait()
            if result_dict is None:
                self.finalize_revalidation()
                return

            self.progress_bar['value'] += 1
            proxy_address = result_dict['proxy']
            
            original_proxy_info = next((p for p in self.rotator.all_proxies if p['proxy'] == proxy_address), None)
            if not original_proxy_info:
                self.log(f"更新跳过: 代理 {proxy_address} 在测试完成时已不存在。")
                return

            tree_item_id = self.proxy_to_tree_item_map.get(proxy_address)

            if result_dict.get('status') == 'Working':
                latency, speed, anonymity = result_dict['latency'], result_dict['speed'], result_dict['anonymity']
                score = 0
                if latency != float('inf'): score += (1 / latency) * 50
                score += speed * 10
                if anonymity == 'Elite': score += 50
                elif anonymity == 'Anonymous': score += 20
                
                result_dict['score'] = score
                original_proxy_info.update(result_dict)

                if tree_item_id and self.tree.exists(tree_item_id):
                    display_values = (
                        f"{score:.1f}", anonymity, result_dict['protocol'], proxy_address,
                        f"{latency * 1000:.1f}", f"{speed:.2f}", result_dict['location']
                    )
                    self.tree.item(tree_item_id, values=display_values)
                self.log(f"更新: {proxy_address} | 分数: {score:.1f} | 延迟: {latency*1000:.1f}ms")
            else:
                self.log(f"测试失败，正在移除: {proxy_address}")
                if self.rotator.remove_proxy(proxy_address):
                    if proxy_address in self.displayed_proxies:
                        self.displayed_proxies.remove(proxy_address)
                    if tree_item_id and self.tree.exists(tree_item_id):
                        self.tree.delete(tree_item_id)
            
            working = self.rotator.get_working_proxies_count()
            current_progress = int(self.progress_bar['value'])
            max_progress = int(self.progress_bar['maximum'])
            if max_progress > 0:
                self.log_frame.config(text=f"实时日志 | 进度: {current_progress}/{max_progress} | 可用: {working}")
            else:
                self.log_frame.config(text=f"实时日志 | 可用: {working}")

        except queue.Empty:
            pass
        
        if self.is_running_task:
            self.root.after(20, self.process_revalidate_queue)

    def sort_treeview_column(self, col, reverse):
        data = [(self.tree.set(child, col), child) for child in self.tree.get_children('')]
        try:
            data.sort(key=lambda t: float(t[0]), reverse=reverse)
        except ValueError:
            data.sort(key=lambda t: str(t[0]), reverse=reverse)
        for index, (val, child) in enumerate(data):
            self.tree.move(child, '', index)

    def copy_to_clipboard(self, event):
        selected_item = self.tree.selection()
        if not selected_item: return
        proxy_address = self.tree.item(selected_item[0], 'values')[3]
        self.root.clipboard_clear(); self.root.clipboard_append(proxy_address)
        self.log(f"已复制到剪贴板: {proxy_address}")
        
    def export_proxies(self):
        working_proxies = self.rotator.all_proxies
        if not working_proxies:
            messagebox.showwarning("无内容", "没有可用的代理可以导出。")
            return
        
        file_path = filedialog.asksaveasfilename(title="导出代理到文件", defaultextension=".csv", filetypes=[("CSV files", "*.csv"), ("Text files", "*.txt"), ("JSON files", "*.json")])
        if not file_path: return
        try:
            _, ext = os.path.splitext(file_path)
            if ext.lower() == '.json':
                with open(file_path, 'w', encoding='utf-8') as f:
                    export_data = [{'protocol': p['protocol'], 'proxy': p['proxy'], 'location': p['location']} for p in working_proxies]
                    json.dump(export_data, f, indent=2, ensure_ascii=False)
            elif ext.lower() == '.txt':
                 with open(file_path, 'w', encoding='utf-8') as f:
                    for p in working_proxies: f.write(f"{p['protocol'].lower()}://{p['proxy']}\n")
            else: 
                with open(file_path, 'w', encoding='utf-8', newline='') as f:
                    f.write("score,anonymity,protocol,proxy,latency_ms,speed_mbps,location\n")
                    for p in working_proxies:
                        lat_ms, spd_mbps = f"{p['latency'] * 1000:.1f}", f"{p['speed']:.2f}"
                        score = p.get('score', 0)
                        f.write(f"{score:.1f},{p['anonymity']},{p['protocol']},{p['proxy']},{lat_ms},{spd_mbps},\"{p['location']}\"\n")
            
            self.log(f"成功导出 {len(working_proxies)} 个代理到 {file_path}")
            messagebox.showinfo("成功", f"已成功导出 {len(working_proxies)} 个代理。")
        except Exception as e:
            self.log(f"导出代理失败: {e}")
            messagebox.showerror("失败", f"导出代理时发生错误:\n{e}")

    def _show_context_menu(self, event):
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return
        self.tree.selection_set(item_id)
        context_menu = tk.Menu(self.root, tearoff=0)
        context_menu.add_command(label="使用此代理", command=self._use_selected_proxy)
        context_menu.add_command(label="删除此代理", command=self._delete_selected_proxy)
        context_menu.tk_popup(event.x_root, event.y_root)

    def _use_selected_proxy(self):
        selected_items = self.tree.selection()
        if not selected_items:
            return
        proxy_address = self.tree.item(selected_items[0], 'values')[3]
        proxy_info = self.rotator.set_current_proxy_by_address(proxy_address)
        if proxy_info:
            self.current_proxy_var.set(f"当前使用: {proxy_info['proxy']}")
            self.log(f"已手动切换代理: {proxy_info['protocol'].lower()}://{proxy_info['proxy']}")
        else:
            self.log(f"错误: 尝试设置的代理 {proxy_address} 在轮换器中未找到。")
            
    def toggle_server(self):
        if self.is_server_running:
            self.proxy_server.stop_all()
            self.server_button.config(text="启动服务", style='info.TButton')
            self.is_server_running = False
        else:
            if self.rotator.get_working_proxies_count() == 0:
                messagebox.showwarning("启动失败", "代理池中无可用代理，无法启动服务。")
                return
            if not self.rotator.get_current_proxy(): self.rotate_proxy()
            self.proxy_server.start_all()
            self.server_button.config(text="停止服务", style='danger.TButton')
            self.is_server_running = True

    def _on_closing(self):
        if self.is_server_running: self.proxy_server.stop_all()
        self.root.destroy()
        
    def toggle_auto_rotate(self):
        if self.is_auto_rotating:
            self.is_auto_rotating = False
            if self.auto_rotate_job_id: self.root.after_cancel(self.auto_rotate_job_id)
            self.auto_rotate_button.config(text="自动", style='info.TButton')
            self.log("自动轮换已停止。")
        else:
            try:
                interval_sec = int(self.interval_spinbox.get())
                if interval_sec <= 0: raise ValueError()
            except ValueError:
                messagebox.showerror("无效间隔", "时间间隔必须是正整数。")
                return
            self.is_auto_rotating = True
            self.auto_rotate_button.config(text="停止", style='danger.TButton')
            self.log(f"自动轮换已启动，间隔 {interval_sec} 秒。")
            self._perform_auto_rotation()
            
    def _perform_auto_rotation(self):
        if not self.is_auto_rotating: return
        self.rotate_proxy()
        try:
            interval_ms = int(self.interval_spinbox.get()) * 1000
            self.auto_rotate_job_id = self.root.after(interval_ms, self._perform_auto_rotation)
        except (ValueError, TclError):
            if self.is_auto_rotating: self.toggle_auto_rotate()

if __name__ == "__main__":
    root = bs.Window(themename="superhero")
    app = ProxyPoolApp(root)
    root.mainloop()