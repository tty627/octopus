from __future__ import annotations

import os
import queue
import sys
import threading
import webbrowser
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from tkinter import (
    BOTH,
    END,
    LEFT,
    RIGHT,
    Listbox,
    StringVar,
    Tk,
    Toplevel,
    filedialog,
    messagebox,
    ttk,
)
from typing import Any, cast

from .activation import ActivationSession
from .config import create_repository
from .engine import UpdateEngine
from .models import RepositoryEstimate, SearchResult, UpdatePhase, UpdateProgress
from .onboarding import classify_onboarding_error, estimate_repository
from .progress import CancellationToken, UpdateCancelledError
from .sample_data import SAMPLE_SEARCH_TASKS, default_sample_paths, materialize_sample_repository
from .search import SearchIndex
from .upgrade import UpgradeCheckResult, UpgradeStatus, check_for_upgrade


def _open_path(path: str | os.PathLike[str]) -> None:
    """Open a path with Explorer without exposing a platform-only os attribute to mypy."""
    if sys.platform != "win32":
        raise RuntimeError("Opening paths from the desktop app is supported only on Windows")
    startfile = cast(
        Callable[[str | os.PathLike[str]], None] | None,
        getattr(os, "startfile", None),
    )
    if startfile is None:
        raise RuntimeError("Windows path opener is unavailable")
    startfile(path)

PHASE_LABELS = {
    UpdatePhase.preparing: "正在准备",
    UpdatePhase.scanning: "正在扫描资料",
    UpdatePhase.leaf: "正在生成文件索引",
    UpdatePhase.foldernode: "正在生成文件夹索引",
    UpdatePhase.committing: "正在安全提交",
    UpdatePhase.search_rebuild: "正在建立搜索缓存",
    UpdatePhase.complete: "索引完成",
    UpdatePhase.cancelled: "已安全取消",
    UpdatePhase.failed: "索引失败",
}

ERROR_MESSAGES = {
    "raw_missing": "资料目录不存在，请重新选择。",
    "raw_unreadable": "无法读取资料目录，请检查权限。",
    "index_nested": "资料目录与索引目录必须分离，且不能互相嵌套。",
    "index_not_empty": "索引目录必须是不存在或空目录。",
    "index_permission": "无法写入索引位置，请选择其他目录。",
    "disk_space": "磁盘空间不足，请释放空间或选择其他磁盘。",
    "repository_locked": "另一个 Octopus 任务正在使用此仓库，请稍后重试。",
    "parser_failure": "部分资料解析失败；基础索引仍可保留并稍后重试。",
    "network_ai": "AI 网络不可用；基础离线索引不受影响。",
    "unknown": "操作未完成，请展开技术详情并按提示重试。",
}


def suggest_index_path(raw: Path) -> Path:
    base = raw.parent / f"{raw.name}-Octopus-Index"
    candidate = base
    number = 2
    while candidate.exists():
        candidate = raw.parent / f"{raw.name}-Octopus-Index-{number}"
        number += 1
    return candidate


def format_bytes(value: int) -> str:
    if value >= 1024**3:
        return f"{value / 1024**3:.1f} GiB"
    return f"{value / 1024**2:.1f} MiB"


class OctopusWizard:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("Octopus 首次设置")
        self.root.minsize(760, 560)
        self.events: queue.SimpleQueue[tuple[str, Any]] = queue.SimpleQueue()
        self.token: CancellationToken | None = None
        self.estimate: RepositoryEstimate | None = None
        self.results: list[SearchResult] = []
        self.index_path: Path | None = None
        self.session: ActivationSession | None = None
        self.sample_mode = StringVar(value="sample")
        sample_raw, sample_index = default_sample_paths()
        self.raw_path = StringVar(value=str(sample_raw))
        self.index_value = StringVar(value=str(sample_index))
        self.status = StringVar(value="选择示例资料或自己的资料目录。")
        self.query = StringVar(value=SAMPLE_SEARCH_TASKS[0])
        self.upgrade_message = StringVar(value="")
        self.upgrade_result: UpgradeCheckResult | None = None
        self.release_button: ttk.Button | None = None
        self._render_selection()
        self.root.after(100, self._poll_events)
        threading.Thread(
            target=self._upgrade_worker,
            args=(False,),
            name="octopus-upgrade-check",
            daemon=True,
        ).start()

    def _clear(self) -> None:
        for child in self.root.winfo_children():
            child.destroy()

    def _frame(self) -> ttk.Frame:
        frame = ttk.Frame(self.root, padding=24)
        frame.pack(fill=BOTH, expand=True)
        return frame

    def _render_selection(self) -> None:
        self._clear()
        frame = self._frame()
        ttk.Label(frame, text="欢迎使用 Octopus", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text="Octopus 只读取资料目录，并把索引写入另一个目录。首次流程不使用 AI。",
            wraplength=680,
        ).pack(anchor="w", pady=(8, 20))
        ttk.Radiobutton(
            frame,
            text="使用内置示例（推荐首次体验）",
            variable=self.sample_mode,
            value="sample",
            command=self._mode_changed,
        ).pack(anchor="w", pady=4)
        ttk.Radiobutton(
            frame,
            text="选择自己的资料目录",
            variable=self.sample_mode,
            value="own",
            command=self._mode_changed,
        ).pack(anchor="w", pady=4)

        paths = ttk.LabelFrame(frame, text="目录", padding=12)
        paths.pack(fill="x", pady=18)
        ttk.Label(paths, text="资料目录").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(paths, textvariable=self.raw_path).grid(row=0, column=1, sticky="ew", padx=8)
        self.raw_button = ttk.Button(paths, text="选择…", command=self._choose_raw)
        self.raw_button.grid(row=0, column=2)
        ttk.Label(paths, text="索引目录").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(paths, textvariable=self.index_value).grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Button(paths, text="选择…", command=self._choose_index).grid(row=1, column=2)
        paths.columnconfigure(1, weight=1)

        ttk.Label(frame, textvariable=self.status, wraplength=680).pack(anchor="w", pady=8)
        update_row = ttk.Frame(frame)
        update_row.pack(fill="x", pady=4)
        ttk.Label(update_row, textvariable=self.upgrade_message, wraplength=460).pack(side=LEFT)
        ttk.Button(update_row, text="检查更新", command=self._manual_upgrade_check).pack(side=RIGHT)
        self.release_button = ttk.Button(
            update_row,
            text="打开发布页",
            command=self._open_release,
            state="disabled",
        )
        self.release_button.pack(side=RIGHT, padx=8)
        if (
            self.upgrade_result
            and self.upgrade_result.status == UpgradeStatus.update_available
        ):
            self.release_button.state(["!disabled"])
        ttk.Button(frame, text="检查并继续", command=self._preflight).pack(anchor="e", pady=12)
        self._mode_changed()

    def _mode_changed(self) -> None:
        if self.sample_mode.get() == "sample":
            raw, index = default_sample_paths()
            self.raw_path.set(str(raw))
            self.index_value.set(str(index))
            self.raw_button.state(["disabled"])
            self.query.set(SAMPLE_SEARCH_TASKS[0])
        else:
            self.raw_button.state(["!disabled"])
            self.query.set("")

    def _choose_raw(self) -> None:
        chosen = filedialog.askdirectory(title="选择资料目录")
        if chosen:
            raw = Path(chosen)
            self.raw_path.set(str(raw))
            self.index_value.set(str(suggest_index_path(raw)))

    def _choose_index(self) -> None:
        chosen = filedialog.askdirectory(title="选择索引目录；也可以输入一个新目录")
        if chosen:
            self.index_value.set(chosen)

    def _sample_estimate(self, raw: Path, index: Path) -> RepositoryEstimate:
        parent = index.parent
        free = 0
        try:
            import shutil

            free = shutil.disk_usage(parent).free
        except OSError:
            pass
        required = 256 * 1024 * 1024
        blockers: list[str] = []
        if raw.exists() or index.exists():
            blockers.append("index_not_empty")
        if free < required:
            blockers.append("disk_space")
        return RepositoryEstimate(
            raw_path=str(raw),
            index_path=str(index),
            file_count=6,
            directory_count=0,
            supported_file_count=6,
            format_counts={
                ".md": 1,
                ".pdf": 1,
                ".docx": 1,
                ".xlsx": 1,
                ".pptx": 1,
                ".png": 1,
            },
            estimated_index_bytes=4 * 1024 * 1024,
            required_free_bytes=required,
            available_free_bytes=free,
            estimated_seconds_p50=15,
            estimated_seconds_p95=90,
            coefficient_version="sample-v1",
            blockers=blockers,
        )

    def _preflight(self) -> None:
        raw = Path(self.raw_path.get())
        index = Path(self.index_value.get())
        try:
            self.estimate = (
                self._sample_estimate(raw, index)
                if self.sample_mode.get() == "sample"
                else estimate_repository(raw, index, ai_enabled=False)
            )
        except Exception as error:
            self._show_error(error)
            return
        if self.estimate.blockers:
            message = "\n".join(
                f"• {ERROR_MESSAGES.get(code, code)}" for code in self.estimate.blockers
            )
            messagebox.showerror("无法继续", message)
            return
        self._render_confirmation()

    def _render_confirmation(self) -> None:
        assert self.estimate is not None
        self._clear()
        frame = self._frame()
        ttk.Label(frame, text="开始前检查", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        formats = "、".join(
            f"{suffix} × {count}" for suffix, count in self.estimate.format_counts.items()
        )
        details = [
            f"资料文件：{self.estimate.file_count}",
            f"支持格式：{self.estimate.supported_file_count}（{formats or '无'}）",
            f"暂不解析：{self.estimate.unsupported_file_count}",
            "预计时间："
            f"{self.estimate.estimated_seconds_p50:.0f}–"
            f"{self.estimate.estimated_seconds_p95:.0f} 秒",
            f"预计索引：{format_bytes(self.estimate.estimated_index_bytes)}",
            f"所需可用空间：{format_bytes(self.estimate.required_free_bytes)}",
            "AI 调用：0",
            "",
            f"资料目录：{self.estimate.raw_path}",
            f"索引目录：{self.estimate.index_path}",
        ]
        ttk.Label(frame, text="\n".join(details), wraplength=680, justify=LEFT).pack(
            anchor="w", pady=20
        )
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=16)
        ttk.Button(buttons, text="返回", command=self._render_selection).pack(side=LEFT)
        ttk.Button(buttons, text="开始建立索引", command=self._start_build).pack(side=RIGHT)

    def _start_build(self) -> None:
        self._render_progress()
        self.token = CancellationToken()
        sample_mode = self.sample_mode.get() == "sample"
        raw = Path(self.raw_path.get())
        index = Path(self.index_value.get())
        self.session = ActivationSession(sample_mode=sample_mode)
        self.session.stage("confirmed")
        thread = threading.Thread(
            target=self._build_worker,
            args=(raw, index, sample_mode),
            name="octopus-onboarding",
            daemon=True,
        )
        thread.start()

    def _render_progress(self) -> None:
        self._clear()
        frame = self._frame()
        ttk.Label(frame, text="正在建立索引", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(frame, textvariable=self.status, wraplength=680).pack(anchor="w", pady=16)
        self.progress = ttk.Progressbar(frame, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=12)
        self.cancel_button = ttk.Button(frame, text="安全取消", command=self._cancel)
        self.cancel_button.pack(anchor="e", pady=12)

    def _build_worker(self, raw: Path, index: Path, sample_mode: bool) -> None:
        try:
            if sample_mode:
                materialize_sample_repository(raw)
            config = create_repository(
                raw,
                index,
                raw.name,
                ai_enabled=False,
                require_empty=True,
            )
            self.index_path = Path(config.repository.index_repository_path)
            assert self.token is not None
            UpdateEngine(self.index_path).run(
                force_path="*",
                progress_callback=lambda event: self.events.put(("progress", event)),
                cancellation_token=self.token,
            )
            self.events.put(("success", None))
        except UpdateCancelledError:
            self.events.put(("cancelled", None))
        except Exception as error:
            self.events.put(("error", error))

    def _retry_build(self) -> None:
        if not self.index_path:
            self._render_selection()
            return
        self.status.set("正在重新开始；已完成的提交会被复用。")
        self._render_progress()
        self.token = CancellationToken()
        if self.session:
            self.session.stage("retry")
        threading.Thread(
            target=self._retry_worker,
            name="octopus-onboarding-retry",
            daemon=True,
        ).start()

    def _retry_worker(self) -> None:
        try:
            assert self.index_path is not None
            assert self.token is not None
            UpdateEngine(self.index_path).run(
                force_path="*",
                progress_callback=lambda event: self.events.put(("progress", event)),
                cancellation_token=self.token,
            )
            self.events.put(("success", None))
        except UpdateCancelledError:
            self.events.put(("cancelled", None))
        except Exception as error:
            self.events.put(("error", error))

    def _cancel(self) -> None:
        if self.token:
            self.token.cancel()
            self.status.set("已请求取消；正在安全完成当前文件。")
            self.cancel_button.state(["disabled"])

    def _manual_upgrade_check(self) -> None:
        self.upgrade_message.set("正在检查更新…")
        threading.Thread(
            target=self._upgrade_worker,
            args=(True,),
            name="octopus-upgrade-check",
            daemon=True,
        ).start()

    def _upgrade_worker(self, force: bool) -> None:
        self.events.put(("upgrade", check_for_upgrade(force=force)))

    def _open_release(self) -> None:
        if self.upgrade_result and self.upgrade_result.release_url:
            webbrowser.open(self.upgrade_result.release_url)

    def _poll_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                event = payload
                assert isinstance(event, UpdateProgress)
                label = PHASE_LABELS[event.phase]
                self.status.set(f"{label}\n{event.current_path}")
                self.progress["value"] = event.percent
                if not event.cancellable:
                    self.cancel_button.state(["disabled"])
            elif kind == "success":
                if self.session and self.estimate:
                    self.session.stage("indexed")
                self._render_results()
            elif kind == "cancelled":
                if self.session and self.estimate:
                    self.session.finish("cancelled", file_count=self.estimate.file_count)
                self._render_cancelled()
            elif kind == "error":
                assert isinstance(payload, Exception)
                if self.session and self.estimate:
                    code = classify_onboarding_error(payload).value
                    self.session.finish(
                        "failed",
                        error_code=code,
                        file_count=self.estimate.file_count,
                    )
                self._show_error(payload)
                self._render_selection()
            elif kind == "upgrade":
                assert isinstance(payload, UpgradeCheckResult)
                self.upgrade_result = payload
                if payload.status == UpgradeStatus.update_available:
                    notes = payload.release_notes.replace("\n", " ").strip()
                    summary = f" — {notes[:180]}" if notes else ""
                    self.upgrade_message.set(
                        f"有新版本：{payload.latest_version}{summary}"
                    )
                    if self.release_button and self.release_button.winfo_exists():
                        self.release_button.state(["!disabled"])
                elif payload.status == UpgradeStatus.current:
                    self.upgrade_message.set("当前已是最新版本")
                elif payload.status == UpgradeStatus.ahead:
                    self.upgrade_message.set("当前为开发版本")
                else:
                    self.upgrade_message.set("暂时无法检查更新；不影响离线使用")
        self.root.after(100, self._poll_events)

    def _render_cancelled(self) -> None:
        self._clear()
        frame = self._frame()
        ttk.Label(frame, text="已安全取消", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text=(
                "原始资料没有被修改，未提交的索引已回滚。仓库注册信息仍然保留，"
                "可以从头安全重试。"
            ),
            wraplength=680,
        ).pack(anchor="w", pady=18)
        ttk.Button(frame, text="重新建立索引", command=self._retry_build).pack(anchor="e")

    def _render_results(self) -> None:
        self._clear()
        frame = self._frame()
        ttk.Label(frame, text="索引已完成", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(frame, text="输入关键词，打开首条有用结果。AI 不会参与此次搜索。").pack(
            anchor="w", pady=(8, 16)
        )
        search_row = ttk.Frame(frame)
        search_row.pack(fill="x")
        ttk.Entry(search_row, textvariable=self.query).pack(side=LEFT, fill="x", expand=True)
        ttk.Button(search_row, text="搜索", command=self._search).pack(side=RIGHT, padx=(8, 0))
        self.result_list = Listbox(frame, height=12)
        self.result_list.pack(fill=BOTH, expand=True, pady=14)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="打开索引结果", command=self._open_index).pack(side=LEFT)
        ttk.Button(buttons, text="打开原文件", command=self._open_source).pack(side=LEFT, padx=8)
        ttk.Button(buttons, text="打开索引目录", command=self._open_index_directory).pack(
            side=RIGHT
        )
        self._search()

    def _search(self) -> None:
        if not self.index_path:
            return
        query = self.query.get().strip()
        if not query:
            return
        self.results = SearchIndex(self.index_path).search(query, limit=5)
        self.result_list.delete(0, END)
        for result in self.results:
            self.result_list.insert(END, f"{result.name} — {result.summary}")
        if self.results:
            self.result_list.selection_set(0)

    def _selected(self) -> SearchResult | None:
        selection = self.result_list.curselection()  # type: ignore[no-untyped-call]
        if not selection:
            return None
        return self.results[int(selection[0])]

    def _open_index(self) -> None:
        result = self._selected()
        if result:
            _open_path(result.index_path)
            self._record_result_opened()

    def _open_source(self) -> None:
        result = self._selected()
        if result and result.source_uri:
            webbrowser.open(result.source_uri)
            self._record_result_opened()

    def _record_result_opened(self) -> None:
        if self.session and self.session.record.outcome != "success":
            self.session.stage("opened_result")
            self.session.finish(
                "success",
                file_count=self.estimate.file_count if self.estimate else 0,
            )

    def _open_index_directory(self) -> None:
        if self.index_path:
            _open_path(self.index_path)

    def _show_error(self, error: Exception) -> None:
        code = classify_onboarding_error(error).value
        dialog = Toplevel(self.root)
        dialog.title("Octopus 未能完成操作")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill=BOTH, expand=True)
        ttk.Label(
            frame,
            text=ERROR_MESSAGES[code],
            wraplength=520,
            font=("Segoe UI", 11),
        ).pack(anchor="w")
        details = ttk.Label(
            frame,
            text=f"错误码：{code}\n{type(error).__name__}: {error}",
            wraplength=520,
            justify=LEFT,
        )

        def reveal() -> None:
            details.pack(anchor="w", pady=(14, 0))
            reveal_button.state(["disabled"])

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(18, 0))
        reveal_button = ttk.Button(buttons, text="显示技术详情", command=reveal)
        reveal_button.pack(side=LEFT)
        ttk.Button(buttons, text="关闭", command=dialog.destroy).pack(side=RIGHT)
        dialog.grab_set()


def main() -> None:
    if "--smoke-test" in sys.argv:
        from .parsers import ParserRegistry

        ParserRegistry()
        return
    root = Tk()
    with suppress(Exception):
        ttk.Style(root).theme_use("vista")
    OctopusWizard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
