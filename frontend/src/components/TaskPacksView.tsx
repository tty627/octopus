import {
  DndContext,
  type DragEndEvent,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import * as Checkbox from "@radix-ui/react-checkbox";
import * as Dialog from "@radix-ui/react-dialog";
import * as Tabs from "@radix-ui/react-tabs";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  Archive,
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  Check,
  CheckCircle2,
  Download,
  FileDown,
  FolderArchive,
  FolderKanban,
  GripVertical,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Save,
  Search,
  Trash2,
  Undo2,
  X,
} from "lucide-react";
import { ApiError, api } from "../api";
import { chooseDirectory, saveTextFile } from "../bridge";
import {
  clearLocalDraft,
  mergeTaskPackSave,
  rebaseTaskPackDraft,
  saveLocalDraft,
  useAppStore,
} from "../store";
import type { TaskPack, TaskPackItem, TaskPackSlot } from "../types";
import { relativeTime, safeFileName } from "../utils";

function operationErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "操作没有完成，请重试。";
}

export function TaskPacksView() {
  const repositoryId = useAppStore((state) => state.repositoryId);
  const pack = useAppStore((state) => state.activeTaskPack);
  const setPack = useAppStore((state) => state.setTaskPack);
  const updatePack = useAppStore((state) => state.updateTaskPack);
  const setPage = useAppStore((state) => state.setPage);
  const saveState = useAppStore((state) => state.saveState);
  const taskPackDirty = useAppStore((state) => state.taskPackDirty);
  const setSaveState = useAppStore((state) => state.setSaveState);
  const [removed, setRemoved] = useState<{ item: TaskPackItem; index: number } | null>(null);
  const [outline, setOutline] = useState("");
  const [outlineError, setOutlineError] = useState("");
  const [exportOpen, setExportOpen] = useState(false);
  const [exportNotice, setExportNotice] = useState("");
  const [exportError, setExportError] = useState("");
  const [exportBusy, setExportBusy] = useState<"markdown" | "package" | null>(null);
  const [conflictBusy, setConflictBusy] = useState<"reload" | "keep" | null>(null);
  const [conflictError, setConflictError] = useState("");
  const [packageSelection, setPackageSelection] = useState<Set<string>>(new Set());
  const queryClient = useQueryClient();
  const summaries = useQuery({
    queryKey: ["task-packs", repositoryId],
    queryFn: () => api.taskPacks(repositoryId),
    enabled: Boolean(repositoryId),
  });
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const openPack = async (taskPackId: string) => setPack(await api.taskPack(repositoryId, taskPackId));
  const createPack = async () => {
    const created = await api.createTaskPack(repositoryId, "新任务", "");
    setPack(created);
  };

  if (!pack) {
    return (
      <div className="taskPacksPage">
        <div className="pageHeading"><div><p className="eyebrow">任务包</p><h1>把找到的资料变成可交付集合</h1><p>任务包保存引用、证据位置和加入原因，只有导出副本时才复制原文件。</p></div><button className="primaryButton" onClick={() => void createPack()}><Plus size={17} />新建任务包</button></div>
        {summaries.data?.length ? <div className="packTable"><div className="packTableHead"><span>任务</span><span>资料</span><span>待核验</span><span>最近保存</span><span /></div>{summaries.data.map((summary) => <button key={summary.task_pack_id} disabled={!summary.writable} onClick={() => void openPack(summary.task_pack_id)}><span><FolderKanban size={18} /><span><strong>{summary.title}</strong><small>{summary.goal || "尚未填写目标"}</small></span></span><span>{summary.item_count}</span><span>{summary.pending_count}</span><span>{relativeTime(summary.updated_at)}</span><MoreHorizontal size={17} /></button>)}</div> : <div className="searchEmpty"><FolderKanban size={28} /><h2>还没有任务包</h2><p>可以先发起任务，也可以在搜索结果中确认第一项资料。</p><button className="secondaryButton" onClick={() => setPage("search")}><Search size={17} />发起任务</button></div>}
      </div>
    );
  }

  const reorderSlots = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id) return;
    updatePack((value) => {
      const sorted = [...value.slots].sort((left, right) => left.position - right.position);
      const from = sorted.findIndex((slot) => slot.slot_id === active.id);
      const to = sorted.findIndex((slot) => slot.slot_id === over.id);
      return { ...value, slots: arrayMove(sorted, from, to).map((slot, position) => ({ ...slot, position })) };
    });
  };

  const addSlot = () => updatePack((value) => ({
    ...value,
    slots: [...value.slots, { slot_id: crypto.randomUUID(), name: "新资料槽位", description: "", position: value.slots.length, required: false }],
  }));

  const removeItem = (item: TaskPackItem) => updatePack((value) => {
    const index = value.items.findIndex((candidate) => candidate.item_id === item.item_id);
    setRemoved({ item, index });
    return { ...value, items: value.items.filter((candidate) => candidate.item_id !== item.item_id), excluded_node_ids: [...new Set([...value.excluded_node_ids, item.node_id])] };
  });

  const undoRemove = () => {
    if (!removed) return;
    updatePack((value) => {
      const items = [...value.items];
      items.splice(Math.max(0, removed.index), 0, removed.item);
      return { ...value, items, excluded_node_ids: value.excluded_node_ids.filter((id) => id !== removed.item.node_id) };
    });
    setRemoved(null);
  };

  const moveItem = (item: TaskPackItem, delta: number) => updatePack((value) => {
    const sameSlot = value.items.filter((candidate) => candidate.slot_id === item.slot_id).sort((left, right) => left.position - right.position);
    const from = sameSlot.findIndex((candidate) => candidate.item_id === item.item_id);
    const to = Math.max(0, Math.min(sameSlot.length - 1, from + delta));
    if (from === to) return value;
    const reordered = arrayMove(sameSlot, from, to).map((candidate, position) => ({ ...candidate, position }));
    const byId = new Map(reordered.map((candidate) => [candidate.item_id, candidate]));
    return { ...value, items: value.items.map((candidate) => byId.get(candidate.item_id) ?? candidate) };
  });

  const exportMarkdown = async () => {
    setExportError("");
    setExportNotice("");
    setExportBusy("markdown");
    try {
      const markdown = await api.taskPackMarkdown(pack);
      const saved = await saveTextFile(`${safeFileName(pack.title)}.md`, markdown);
      setExportNotice(saved ? "Markdown 已保存。" : "已取消保存。");
    } catch (error) {
      setExportError(operationErrorMessage(error));
    } finally {
      setExportBusy(null);
    }
  };

  const exportPackage = async () => {
    setExportError("");
    setExportNotice("");
    setExportBusy("package");
    try {
      const output = await chooseDirectory();
      if (!output) return;
      await api.packageTaskPack(pack, output, [...packageSelection]);
      setExportNotice("资料副本已进入后台导出。任务包保持不变。");
    } catch (error) {
      setExportError(operationErrorMessage(error));
    } finally {
      setExportBusy(null);
    }
  };

  const loadOutline = async () => {
    setOutlineError("");
    try {
      setOutline(await api.taskPackMarkdown(pack));
    } catch (error) {
      setOutlineError(operationErrorMessage(error));
    }
  };

  const openExport = () => {
    setExportError("");
    setExportNotice("");
    setPackageSelection(new Set(pack.items.filter((item) => item.review_state === "confirmed").map((item) => item.item_id)));
    setExportOpen(true);
  };

  const reloadConflict = async () => {
    const draft = useAppStore.getState().activeTaskPack ?? pack;
    setConflictBusy("reload");
    setConflictError("");
    try {
      const latest = await api.taskPack(repositoryId, draft.task_pack_id);
      clearLocalDraft(draft);
      setPack(latest, false);
    } catch (error) {
      setConflictError(operationErrorMessage(error));
    } finally {
      setConflictBusy(null);
    }
  };

  const keepLocalConflict = async () => {
    setConflictBusy("keep");
    setConflictError("");
    try {
      const latest = await api.taskPack(repositoryId, pack.task_pack_id);
      const localDraft = useAppStore.getState().activeTaskPack ?? pack;
      const submitted = rebaseTaskPackDraft(localDraft, latest);
      setSaveState("saving");
      const saved = await api.saveTaskPack(submitted);
      clearLocalDraft(localDraft);
      const current = useAppStore.getState().activeTaskPack;
      const merged = current === localDraft
        ? { pack: saved, dirty: false }
        : mergeTaskPackSave(saved, localDraft, current);
      if (merged) {
        setPack(merged.pack, merged.dirty);
        if (merged.dirty) saveLocalDraft(merged.pack);
      }
      void queryClient.invalidateQueries({ queryKey: ["task-packs", repositoryId] });
    } catch (error) {
      const draft = useAppStore.getState().activeTaskPack ?? pack;
      saveLocalDraft(draft);
      setSaveState(error instanceof ApiError && error.status === 409 ? "conflict" : "offline");
      setConflictError(operationErrorMessage(error));
    } finally {
      setConflictBusy(null);
    }
  };

  const archive = async () => {
    await api.archiveTaskPack(pack);
    setPack(null);
    void queryClient.invalidateQueries({ queryKey: ["task-packs", repositoryId] });
  };

  const sortedSlots = [...pack.slots].sort((left, right) => left.position - right.position);
  const confirmedCount = pack.items.filter((item) => item.review_state === "confirmed").length;
  const pendingCount = pack.items.length - confirmedCount;

  return (
    <div className="taskPackEditor">
      <div className="taskPackHeader">
        <button className="iconButton" aria-label="返回任务包列表" title="返回" onClick={() => setPack(null)}><ArrowLeft size={19} /></button>
        <div className="taskIdentity"><input className="taskTitleInput" value={pack.title} onChange={(event) => updatePack((value) => ({ ...value, title: event.target.value }))} aria-label="任务包名称" /><textarea value={pack.goal} onChange={(event) => updatePack((value) => ({ ...value, goal: event.target.value }))} placeholder="写下这个任务要达成的目标..." aria-label="任务目标" /></div>
        <div className="taskHeaderActions"><span className={`saveIndicator save-${saveState}`}><Save size={15} />{saveState === "saving" ? "保存中" : saveState === "offline" ? "本地草稿" : saveState === "conflict" ? "需要处理冲突" : taskPackDirty ? "等待保存" : "已保存"}</span><button className="secondaryButton" onClick={() => setPage("search")}><Search size={17} />继续查找</button><button className="primaryButton" onClick={openExport}><Download size={17} />导出</button></div>
      </div>
      <div className="taskStats"><span>{confirmedCount} 项已确认</span><span>{pendingCount} 项待核验</span><span>{sortedSlots.filter((slot) => slot.required && !pack.items.some((item) => item.slot_id === slot.slot_id)).length} 个必需槽位缺资料</span><button className="textButton archiveButton" onClick={() => void archive()}><Archive size={16} />归档</button></div>
      {saveState === "conflict" && <div className="conflictPanel" role="alert"><div><strong>任务包在其他窗口或进程中发生了变化</strong><span>重新载入会放弃当前草稿；保留本地草稿会基于服务器最新版本再次保存。</span></div><div className="conflictActions"><button className="secondaryButton" disabled={conflictBusy !== null} onClick={() => void reloadConflict()}><RefreshCw size={16} />{conflictBusy === "reload" ? "载入中" : "重新载入"}</button><button className="primaryButton" disabled={conflictBusy !== null} onClick={() => void keepLocalConflict()}><Save size={16} />{conflictBusy === "keep" ? "保存中" : "保留本地草稿"}</button></div>{conflictError && <div className="errorBox">{conflictError}</div>}</div>}
      <Tabs.Root defaultValue="sources" className="taskTabs" onValueChange={(value) => { if (value === "outline" && !outline) void loadOutline(); }}>
        <Tabs.List><Tabs.Trigger value="sources">资料</Tabs.Trigger><Tabs.Trigger value="outline">大纲</Tabs.Trigger></Tabs.List>
        <Tabs.Content value="sources">
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={reorderSlots}>
            <SortableContext items={sortedSlots.map((slot) => slot.slot_id)} strategy={verticalListSortingStrategy}>
              <div className="slotList">{sortedSlots.map((slot) => <SortableSlot key={slot.slot_id} slot={slot} pack={pack} updatePack={updatePack} removeItem={removeItem} moveItem={moveItem} />)}</div>
            </SortableContext>
          </DndContext>
          <button className="addSlotButton" onClick={addSlot}><Plus size={17} />新增资料槽位</button>
        </Tabs.Content>
        <Tabs.Content value="outline"><div className="outlinePreview"><div className="outlineToolbar"><FileDown size={17} /><span>确定性 Markdown，可直接作为 Markmap 输入</span><button className="secondaryButton smallButton" disabled={exportBusy !== null} onClick={() => void exportMarkdown()}>{exportBusy === "markdown" ? "保存中" : "保存 Markdown"}</button></div>{outlineError ? <div className="errorBox">{outlineError}<button className="textButton" onClick={() => void loadOutline()}>重试</button></div> : <pre>{outline || "正在生成大纲..."}</pre>}</div></Tabs.Content>
      </Tabs.Root>
      {removed && <div className="undoToast" role="status">已从任务包移除“{removed.item.name}”<button className="textButton" onClick={undoRemove}><Undo2 size={16} />撤销</button><button className="iconButton" aria-label="关闭撤销提示" onClick={() => setRemoved(null)}><X size={16} /></button></div>}
      <Dialog.Root open={exportOpen} onOpenChange={setExportOpen}>
        <Dialog.Portal><Dialog.Overlay className="dialogOverlay" /><Dialog.Content className="dialogContent"><div className="dialogHeader"><div><Dialog.Title>导出任务包</Dialog.Title><Dialog.Description>选择本次动作范围。待核验资料默认不复制。</Dialog.Description></div><Dialog.Close className="iconButton" aria-label="关闭"><X size={18} /></Dialog.Close></div><div className="exportOptions"><button disabled={exportBusy !== null} onClick={() => void exportMarkdown()}><FileDown size={21} /><span><strong>{exportBusy === "markdown" ? "正在保存 Markdown" : "带链接 Markdown"}</strong><small>保存任务结构、资料引用和证据锚点，兼容 Markmap。</small></span></button><div className="packageOption"><div className="packageHeading"><FolderArchive size={21} /><span><strong>复制已确认资料</strong><small>使用 Package Plugin 写入空目标目录。</small></span></div><div className="exportChecklist">{pack.items.map((item) => <label key={item.item_id} className={item.review_state === "pending" ? "pendingExport" : ""}><Checkbox.Root checked={packageSelection.has(item.item_id)} disabled={item.review_state === "pending"} onCheckedChange={(checked) => setPackageSelection((current) => { const next = new Set(current); if (checked === true) next.add(item.item_id); else next.delete(item.item_id); return next; })}><Checkbox.Indicator><Check size={13} /></Checkbox.Indicator></Checkbox.Root><span>{item.name}<small>{item.review_state === "pending" ? "待核验，默认不复制" : pack.slots.find((slot) => slot.slot_id === item.slot_id)?.name}</small></span></label>)}</div><button className="primaryButton" disabled={packageSelection.size === 0 || exportBusy !== null} onClick={() => void exportPackage()}><FolderArchive size={17} />{exportBusy === "package" ? "正在创建导出任务" : `选择空目录并导出 ${packageSelection.size} 项`}</button></div></div>{exportError && <div className="errorBox" role="alert">{exportError}</div>}{exportNotice && <div className="successBox" role="status"><CheckCircle2 size={17} />{exportNotice}</div>}</Dialog.Content></Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

interface SortableSlotProps {
  slot: TaskPackSlot;
  pack: TaskPack;
  updatePack: (updater: (pack: TaskPack) => TaskPack) => void;
  removeItem: (item: TaskPackItem) => void;
  moveItem: (item: TaskPackItem, delta: number) => void;
}

function SortableSlot({ slot, pack, updatePack, removeItem, moveItem }: SortableSlotProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: slot.slot_id });
  const items = pack.items.filter((item) => item.slot_id === slot.slot_id).sort((left, right) => left.position - right.position);
  const updateSlot = (changes: Partial<TaskPackSlot>) => updatePack((value) => ({ ...value, slots: value.slots.map((candidate) => candidate.slot_id === slot.slot_id ? { ...candidate, ...changes } : candidate) }));
  const removeSlot = () => {
    if (pack.slots.length <= 1 || items.length > 0) return;
    updatePack((value) => ({ ...value, slots: value.slots.filter((candidate) => candidate.slot_id !== slot.slot_id).map((candidate, position) => ({ ...candidate, position })) }));
  };
  return (
    <section ref={setNodeRef} style={{ transform: CSS.Transform.toString(transform), transition }} className={`taskSlot ${isDragging ? "slotDragging" : ""}`}>
      <div className="slotHeader"><button className="dragHandle" aria-label={`拖动 ${slot.name}`} title="拖动排序" {...attributes} {...listeners}><GripVertical size={18} /></button><div className="slotIdentity"><input value={slot.name} onChange={(event) => updateSlot({ name: event.target.value })} aria-label="槽位名称" /><input value={slot.description} onChange={(event) => updateSlot({ description: event.target.value })} placeholder="说明这个槽位需要什么资料" aria-label="槽位说明" /></div><span className="slotCount">{items.length} 项</span><label className="requiredToggle"><input type="checkbox" checked={slot.required} onChange={(event) => updateSlot({ required: event.target.checked })} />必需</label><button className="iconButton" aria-label={`删除槽位 ${slot.name}`} title={items.length ? "先移除槽位中的资料" : "删除槽位"} disabled={pack.slots.length <= 1 || items.length > 0} onClick={removeSlot}><Trash2 size={16} /></button></div>
      {items.length ? <div className="taskItemList">{items.map((item, index) => <div className="taskItem" key={item.item_id}><span className={item.review_state === "pending" ? "pendingDot" : "confirmedDot"} /><div className="taskItemBody"><strong>{item.name}</strong><small>{item.rationale || item.raw_relative_path}</small></div><select value={item.slot_id} onChange={(event) => updatePack((value) => ({ ...value, items: value.items.map((candidate) => candidate.item_id === item.item_id ? { ...candidate, slot_id: event.target.value, position: value.items.filter((other) => other.slot_id === event.target.value).length } : candidate) }))} aria-label={`${item.name} 所属槽位`}>{[...pack.slots].sort((left, right) => left.position - right.position).map((candidate) => <option value={candidate.slot_id} key={candidate.slot_id}>{candidate.name}</option>)}</select><select value={item.review_state} onChange={(event) => updatePack((value) => ({ ...value, items: value.items.map((candidate) => candidate.item_id === item.item_id ? { ...candidate, review_state: event.target.value as "confirmed" | "pending" } : candidate) }))} aria-label={`${item.name} 核验状态`}><option value="confirmed">已确认</option><option value="pending">待核验</option></select><div className="itemOrder"><button className="iconButton" disabled={index === 0} aria-label={`上移 ${item.name}`} title="上移" onClick={() => moveItem(item, -1)}><ArrowUp size={15} /></button><button className="iconButton" disabled={index === items.length - 1} aria-label={`下移 ${item.name}`} title="下移" onClick={() => moveItem(item, 1)}><ArrowDown size={15} /></button></div><button className="iconButton" aria-label={`从任务包移除 ${item.name}`} title="移除" onClick={() => removeItem(item)}><Trash2 size={16} /></button></div>)}</div> : <div className="slotEmpty">这个槽位还没有资料。可以继续搜索，或把其他槽位中的资料移到这里。</div>}
    </section>
  );
}
