import { ChevronDown, ChevronUp, FolderKanban, Save, ShieldAlert } from "lucide-react";
import { useAppStore } from "../store";

export function TaskTray() {
  const pack = useAppStore((state) => state.activeTaskPack);
  const expanded = useAppStore((state) => state.trayExpanded);
  const setExpanded = useAppStore((state) => state.setTrayExpanded);
  const setPage = useAppStore((state) => state.setPage);
  const saveState = useAppStore((state) => state.saveState);
  if (!pack || pack.items.length === 0) return null;
  const pending = pack.items.filter((item) => item.review_state === "pending").length;
  return (
    <div className={`taskTray ${expanded ? "trayExpanded" : ""}`}>
      <div className="traySummary">
        <FolderKanban size={19} /><button className="trayTitle" onClick={() => setPage("task-packs")}><strong>{pack.title}</strong><span>{pack.items.length} 项资料 · {pending} 项待核验</span></button>
        <span className={`saveIndicator save-${saveState}`}>{saveState === "saving" ? <Save size={15} /> : saveState === "conflict" || saveState === "offline" ? <ShieldAlert size={15} /> : <Save size={15} />}{saveState === "saving" ? "保存中" : saveState === "offline" ? "本地草稿" : saveState === "conflict" ? "版本冲突" : "已保存"}</span>
        <button className="iconButton" aria-label={expanded ? "收起任务包托盘" : "展开任务包托盘"} title={expanded ? "收起" : "展开"} onClick={() => setExpanded(!expanded)}>{expanded ? <ChevronDown size={18} /> : <ChevronUp size={18} />}</button>
      </div>
      {expanded && <div className="trayItems">{pack.items.slice(0, 8).map((item) => <button key={item.item_id} onClick={() => setPage("task-packs")}><span className={item.review_state === "pending" ? "pendingDot" : "confirmedDot"} />{item.name}<small>{pack.slots.find((slot) => slot.slot_id === item.slot_id)?.name}</small></button>)}{pack.items.length > 8 && <span className="moreItems">还有 {pack.items.length - 8} 项</span>}</div>}
    </div>
  );
}
