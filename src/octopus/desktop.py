from __future__ import annotations

import queue
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import (
    BOTH,
    END,
    LEFT,
    RIGHT,
    BooleanVar,
    StringVar,
    Tk,
    Toplevel,
    filedialog,
    messagebox,
    ttk,
)
from typing import Any

from .desktop_client import (
    DesktopController,
    DesktopServiceError,
    LocalApiClient,
    recovery_guidance,
)

DESKTOP_SHORTCUTS = {
    "focus_search": "<Control-f>",
    "add_repository": "<Control-n>",
    "refresh": "<F5>",
}


def desktop_scale(pixels_per_inch: float) -> float:
    return max(1.0, pixels_per_inch / 72.0)


class OctopusDesktop:
    def __init__(
        self,
        root: Tk,
        client_factory: Callable[[], LocalApiClient] = LocalApiClient.from_runtime,
    ) -> None:
        self.root = root
        self.client_factory = client_factory
        self.controller: DesktopController | None = None
        self.events: queue.SimpleQueue[tuple[str, Any]] = queue.SimpleQueue()
        self.search_results: list[dict[str, Any]] = []
        self.status = StringVar(value="正在连接本地服务…")
        self.repository_detail = StringVar(value="尚未选择仓库")
        self.search_query = StringVar()
        self.auto_search = BooleanVar(value=False)
        self.search_detail = StringVar(value="输入关键词后查看推荐原因、证据和风险。")
        self.migration_message = StringVar()
        self.root.title("Octopus 桌面端 Beta")
        self.root.minsize(980, 680)
        self._configure_scaling()
        self._build()
        self._bind_keys()
        self.root.after(100, self._poll_events)
        self._run("connect", self._connect, self._connected)

    def _configure_scaling(self) -> None:
        try:
            scale = desktop_scale(float(self.root.winfo_fpixels("1i")))
            self.root.tk.call("tk", "scaling", scale)
        except Exception:
            pass

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill=BOTH, expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="Octopus", font=("Segoe UI", 18, "bold")).pack(side=LEFT)
        ttk.Label(header, textvariable=self.status).pack(side=RIGHT)
        ttk.Label(
            outer,
            text="Raw Repository 始终只读；更新、校验和修复仅写入独立 Index Repository。",
        ).pack(anchor="w", pady=(4, 8))
        self.migration_banner = ttk.Label(
            outer,
            textvariable=self.migration_message,
            foreground="#9a5b00",
        )
        self.migration_banner.pack(anchor="w", pady=(0, 8))

        body = ttk.Panedwindow(outer, orient="horizontal")
        body.pack(fill=BOTH, expand=True)
        sidebar = ttk.Frame(body, padding=(0, 0, 10, 0))
        body.add(sidebar, weight=1)
        content = ttk.Frame(body)
        body.add(content, weight=4)

        ttk.Label(sidebar, text="仓库", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.repository_tree = ttk.Treeview(sidebar, columns=("status",), show="tree headings")
        self.repository_tree.heading("#0", text="名称")
        self.repository_tree.heading("status", text="状态")
        self.repository_tree.column("#0", width=180)
        self.repository_tree.column("status", width=70, anchor="center")
        self.repository_tree.pack(fill=BOTH, expand=True, pady=8)
        self.repository_tree.bind("<<TreeviewSelect>>", self._repository_selected)
        side_buttons = ttk.Frame(sidebar)
        side_buttons.pack(fill="x")
        ttk.Button(side_buttons, text="添加", command=self._add_repository).pack(side=LEFT)
        ttk.Button(side_buttons, text="刷新", command=self.refresh).pack(side=RIGHT)

        notebook = ttk.Notebook(content)
        notebook.pack(fill=BOTH, expand=True)
        overview = ttk.Frame(notebook, padding=14)
        search = ttk.Frame(notebook, padding=14)
        state = ttk.Frame(notebook, padding=14)
        notebook.add(overview, text="概览与操作")
        notebook.add(search, text="搜索")
        notebook.add(state, text="状态中心")

        ttk.Label(
            overview,
            textvariable=self.repository_detail,
            justify=LEFT,
            wraplength=680,
        ).pack(anchor="w", fill="x")
        actions = ttk.LabelFrame(overview, text="仓库操作", padding=12)
        actions.pack(fill="x", pady=14)
        ttk.Button(actions, text="更新索引", command=self._update).pack(side=LEFT)
        ttk.Button(actions, text="重试失败项", command=self._retry).pack(side=LEFT, padx=6)
        ttk.Button(actions, text="校验", command=self._validate).pack(side=LEFT, padx=6)
        ttk.Button(actions, text="修复搜索缓存", command=self._repair).pack(side=LEFT, padx=6)
        ttk.Button(actions, text="打开索引目录", command=self._open_index).pack(side=RIGHT)

        search_row = ttk.Frame(search)
        search_row.pack(fill="x")
        self.search_entry = ttk.Entry(search_row, textvariable=self.search_query)
        self.search_entry.pack(side=LEFT, fill="x", expand=True)
        self.search_entry.bind("<Return>", lambda _event: self._search())
        ttk.Button(search_row, text="搜索", command=self._search).pack(side=RIGHT, padx=(8, 0))
        ttk.Checkbutton(
            search,
            text="允许 AI 增强（失败时自动使用本地结果）",
            variable=self.auto_search,
        ).pack(anchor="w", pady=(8, 0))
        self.result_tree = ttk.Treeview(
            search,
            columns=("type", "status"),
            show="tree headings",
            height=12,
        )
        self.result_tree.heading("#0", text="结果")
        self.result_tree.heading("type", text="类型")
        self.result_tree.heading("status", text="状态")
        self.result_tree.column("#0", width=430)
        self.result_tree.column("type", width=90)
        self.result_tree.column("status", width=100)
        self.result_tree.pack(fill=BOTH, expand=True, pady=10)
        self.result_tree.bind("<<TreeviewSelect>>", self._result_selected)
        ttk.Label(
            search,
            textvariable=self.search_detail,
            justify=LEFT,
            wraplength=680,
        ).pack(fill="x")
        result_actions = ttk.Frame(search)
        result_actions.pack(fill="x", pady=(10, 0))
        ttk.Button(result_actions, text="打开推荐目标", command=self._open_result).pack(side=LEFT)
        ttk.Button(result_actions, text="打开索引", command=self._open_result_index).pack(
            side=LEFT, padx=6
        )
        ttk.Button(result_actions, text="打开原文件", command=self._open_result_source).pack(
            side=LEFT
        )

        ttk.Label(state, text="待处理状态", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.state_text = StringVar(value="连接后显示 pending、failed、orphaned 与最近运行信息。")
        ttk.Label(
            state,
            textvariable=self.state_text,
            justify=LEFT,
            wraplength=680,
        ).pack(anchor="w", fill="x", pady=12)
        ttk.Button(state, text="刷新状态", command=self.refresh).pack(anchor="e")
        ttk.Button(
            state,
            text="保存本地诊断包…",
            command=self._save_diagnostics,
        ).pack(anchor="e", pady=(8, 0))

    def _bind_keys(self) -> None:
        self.root.bind(
            DESKTOP_SHORTCUTS["focus_search"],
            lambda _event: self.search_entry.focus_set(),
        )
        self.root.bind(
            DESKTOP_SHORTCUTS["add_repository"],
            lambda _event: self._add_repository(),
        )
        self.root.bind(DESKTOP_SHORTCUTS["refresh"], lambda _event: self.refresh())

    def _run(
        self,
        name: str,
        worker: Callable[[], Any],
        success: Callable[[Any], None] | None = None,
    ) -> None:
        self.status.set(f"正在{name}…")

        def execute() -> None:
            try:
                self.events.put(("success", (name, worker(), success)))
            except Exception as error:
                self.events.put(("error", error))

        threading.Thread(target=execute, name=f"octopus-desktop-{name}", daemon=True).start()

    def _connect(self) -> DesktopController:
        controller = DesktopController(self.client_factory())
        controller.connect()
        return controller

    def _connected(self, controller: DesktopController) -> None:
        self.controller = controller
        self._render_repositories()
        self._refresh_details()

    def refresh(self) -> None:
        if not self.controller:
            self._run("重新连接服务", self._connect, self._connected)
            return
        self._run("刷新", self.controller.refresh_repositories, lambda _value: self._refreshed())

    def _refreshed(self) -> None:
        self._render_repositories()
        self._refresh_details()

    def _render_repositories(self) -> None:
        if not self.controller:
            return
        self.repository_tree.delete(*self.repository_tree.get_children())
        for item in self.controller.repositories:
            identifier = str(item.get("repository_id", ""))
            self.repository_tree.insert(
                "",
                END,
                iid=identifier,
                text=str(item.get("name", identifier)),
                values=("可用" if item.get("available", True) else "不可用",),
            )
        selected = self.controller.selected_repository_id
        if selected and self.repository_tree.exists(selected):
            self.repository_tree.selection_set(selected)

    def _repository_selected(self, _event: object | None = None) -> None:
        if not self.controller:
            return
        selection = self.repository_tree.selection()
        if not selection:
            return
        identifier = selection[0]
        controller = self.controller
        self._run(
            "读取仓库",
            lambda: controller.select(identifier),
            lambda _value: self._refresh_details(),
        )

    def _refresh_details(self) -> None:
        if not self.controller or not self.controller.repository_state:
            self.repository_detail.set("尚无仓库。点击“添加”即可在不使用命令行的情况下初始化。")
            self.state_text.set("无仓库状态。")
            return
        item = self.controller.repository_state
        self.repository_detail.set(
            f"{item.get('name', '')}\n"
            f"资料目录：{item.get('raw_repository_path', '')}\n"
            f"索引目录：{item.get('index_repository_path', '')}\n"
            f"{self.controller.status_summary()}"
        )
        self.state_text.set(self.controller.status_summary())
        self._run("检查迁移", self.controller.migrations, self._show_migrations)
        self._run("读取报告", self.controller.latest_report, self._show_report)

    def _show_migrations(self, payload: dict[str, Any]) -> None:
        self.migration_message.set(
            "检测到迁移需求：请先查看迁移计划。" if payload.get("required") else ""
        )

    def _show_report(self, report: dict[str, Any] | None) -> None:
        if not report:
            return
        usage = report.get("ai_usage", {})
        existing = self.state_text.get()
        self.state_text.set(
            f"{existing}\n最近运行：{report.get('status', 'unknown')} · "
            f"恢复动作 {len(report.get('recovery_actions', []))} · "
            f"AI 调用 {usage.get('calls', 0)} / token {usage.get('total_tokens', 0)}"
        )

    def _save_diagnostics(self) -> None:
        if not self.controller or not self.controller.selected_repository_id:
            messagebox.showinfo("无可用仓库", "请先选择一个仓库。")
            return
        output = filedialog.asksaveasfilename(
            title="保存本地诊断包",
            defaultextension=".zip",
            filetypes=[("ZIP 诊断包", "*.zip")],
            initialfile="octopus-diagnostics.zip",
        )
        if not output:
            return
        controller = self.controller

        def saved(result: dict[str, Any]) -> None:
            messagebox.showinfo(
                "诊断包已保存",
                f"已在本地生成 {result.get('file', '诊断包')}。\n"
                "未上传任何数据；分享前仍需你的明确同意。",
            )

        self._run(
            "生成诊断包",
            lambda: controller.create_diagnostics(output),
            saved,
        )

    def _add_repository(self) -> None:
        dialog = Toplevel(self.root)
        dialog.title("添加 Octopus 仓库")
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill=BOTH, expand=True)
        raw = StringVar()
        index = StringVar()
        name = StringVar()

        def choose_raw() -> None:
            value = filedialog.askdirectory(title="选择只读资料目录")
            if value:
                raw.set(value)
                source = Path(value)
                index.set(str(source.parent / f"{source.name}-Octopus-Index"))

        def choose_index() -> None:
            value = filedialog.askdirectory(title="选择空索引目录")
            if value:
                index.set(value)

        for row, (label, variable, command) in enumerate(
            (("资料目录", raw, choose_raw), ("索引目录", index, choose_index))
        ):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=6)
            ttk.Entry(frame, textvariable=variable, width=62).grid(row=row, column=1, padx=8)
            ttk.Button(frame, text="选择…", command=command).grid(row=row, column=2)
        ttk.Label(frame, text="名称").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=name).grid(row=2, column=1, sticky="ew", padx=8)

        def create() -> None:
            controller = self.controller
            if not controller or not raw.get() or not index.get():
                messagebox.showerror("无法添加", "请选择资料目录和索引目录。", parent=dialog)
                return
            dialog.destroy()
            self._run(
                "创建仓库",
                lambda: controller.create(raw.get(), index.get(), name.get() or None),
                self._created,
            )

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side=LEFT)
        ttk.Button(buttons, text="创建并建立索引", command=create).pack(side=LEFT, padx=6)
        dialog.transient(self.root)
        dialog.grab_set()

    def _created(self, payload: dict[str, Any]) -> None:
        self._refreshed()
        job = payload.get("job")
        if isinstance(job, dict) and job.get("job_id"):
            self._wait_job(str(job["job_id"]))

    def _wait_job(self, job_id: str) -> None:
        controller = self.controller
        assert controller is not None
        self._run(
            "执行后台任务",
            lambda: controller.wait_for_job(job_id),
            self._job_finished,
        )

    def _job_finished(self, job: dict[str, Any]) -> None:
        if job.get("status") == "failed":
            raise DesktopServiceError(
                str(job.get("error_code", "job_failed")),
                str(job.get("error_message", "后台任务失败")),
            )
        self.refresh()

    def _submit(self, retry_only: bool = False) -> None:
        controller = self.controller
        if not controller:
            return
        self._run(
            "提交更新",
            lambda: controller.submit_update(retry_only=retry_only),
            lambda job: self._wait_job(str(job["job_id"])),
        )

    def _update(self) -> None:
        self._submit(False)

    def _retry(self) -> None:
        self._submit(True)

    def _repair(self) -> None:
        if self.controller:
            self._run(
                "修复搜索缓存",
                self.controller.rebuild_search,
                lambda job: self._wait_job(str(job["job_id"])),
            )

    def _validate(self) -> None:
        if self.controller:
            self._run("校验", self.controller.validate, self._validation_done)

    def _validation_done(self, report: dict[str, Any]) -> None:
        messagebox.showinfo(
            "校验完成",
            f"错误 {report.get('error_count', 0)} · 警告 {report.get('warning_count', 0)}",
            parent=self.root,
        )

    def _search(self) -> None:
        query = self.search_query.get().strip()
        controller = self.controller
        if controller and query:
            auto_mode = self.auto_search.get()
            self._run(
                "搜索",
                lambda: controller.search(query, auto_mode=auto_mode),
                self._search_done,
            )

    def _search_done(self, report: dict[str, Any]) -> None:
        self.search_results = list(report.get("results", []))
        self.result_tree.delete(*self.result_tree.get_children())
        for number, result in enumerate(self.search_results):
            self.result_tree.insert(
                "",
                END,
                iid=str(number),
                text=str(result.get("name", "")),
                values=(result.get("index_type", ""), result.get("status", "")),
            )
        if self.search_results:
            self.result_tree.selection_set("0")
            self._result_selected()
        actual = report.get("actual_mode")
        if actual == "degraded":
            reason = report.get("degradation_reason", "unknown")
            self.status.set(f"AI 已降级：{reason}；本地结果可用")

    def _selected_result(self) -> dict[str, Any] | None:
        selection = self.result_tree.selection()
        if not selection:
            return None
        return self.search_results[int(selection[0])]

    def _result_selected(self, _event: object | None = None) -> None:
        result = self._selected_result()
        if not result:
            return
        evidence = result.get("evidence", [])
        first = evidence[0] if evidence else {}
        self.search_detail.set(
            f"推荐原因：{result.get('explanation', '本地相关性排序')}\n"
            f"证据：{first.get('locator', '无')} · {first.get('text_excerpt', '')}\n"
            f"风险：{', '.join(result.get('risk_flags', [])) or '无已知风险'}"
        )

    def _open_result(self) -> None:
        result = self._selected_result()
        if result and result.get("open_target_uri"):
            webbrowser.open(str(result["open_target_uri"]))

    def _open_result_source(self) -> None:
        result = self._selected_result()
        if result and result.get("source_uri"):
            webbrowser.open(str(result["source_uri"]))

    def _open_result_index(self) -> None:
        result = self._selected_result()
        if result and result.get("index_path"):
            self._open_local_path(str(result["index_path"]))

    def _open_index(self) -> None:
        if self.controller and self.controller.repository_state.get("index_repository_path"):
            self._open_local_path(str(self.controller.repository_state["index_repository_path"]))

    @staticmethod
    def _open_local_path(path: str) -> None:
        from .gui import _open_path

        _open_path(path)

    def _poll_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "success":
                name, value, callback = payload
                self.status.set(f"{name}完成")
                if callback:
                    try:
                        callback(value)
                    except Exception as error:
                        self._show_operation_error(error)
            else:
                assert isinstance(payload, Exception)
                self._show_operation_error(payload)
        self.root.after(100, self._poll_events)

    def _show_operation_error(self, error: Exception) -> None:
        if isinstance(error, DesktopServiceError):
            guidance = recovery_guidance(error)
            code = error.code
        else:
            guidance = "可刷新状态后重试；Raw Repository 不会被修改。"
            code = type(error).__name__
        self.status.set(f"操作失败：{code}")
        messagebox.showerror(
            "Octopus 未能完成操作",
            f"{guidance}\n\n错误码：{code}\n{error}",
            parent=self.root,
        )
