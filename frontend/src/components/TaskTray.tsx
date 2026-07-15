import { FolderOpen, Save } from "lucide-react";
import { useAppStore } from "../store";

export function TaskTray() {
  const task = useAppStore((state) => state.activeTask);
  const saveState = useAppStore((state) => state.saveState);
  const taskDirty = useAppStore((state) => state.taskDirty);
  const setPage = useAppStore((state) => state.setPage);
  if (!task || task.items.length === 0) return null;
  return (
    <button className="taskTray" onClick={() => setPage("tasks")}>
      <FolderOpen size={18} />
      <span><strong>{task.title}</strong><small>{task.items.length} 条证据</small></span>
      <span className={`saveIndicator save-${saveState}`}><Save size={14} />{saveState === "saving" ? "保存中" : saveState === "offline" ? "本地草稿" : saveState === "conflict" ? "冲突待处理" : taskDirty ? "等待保存" : "已保存"}</span>
    </button>
  );
}
